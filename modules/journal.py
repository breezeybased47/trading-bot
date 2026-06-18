"""
journal.py
The research brain: a fully-queryable SQLite record of EVERY trade, auto-tagged
with the full context it happened in.

WHY
---
The single most valuable research output of this whole project. A CSV of fills
tells you *what* happened; this journal tells you *under what conditions* — the
market regime, time of day, which signals fired and their values, the spread and
liquidity at entry, which sizing model was used, and whether any guard blocked
then allowed the trade. With those tags you can answer the only questions that
matter: "which conditions make money, and which quietly bleed it?"

Everything downstream reads from here:
  - regime_filter  -> expectancy per regime
  - position_sizer -> rolling win rate / win-loss ratio / consecutive losses
  - journal_report -> weekly tag analysis
  - ml_filter      -> training data (features = tags, label = win)

Design
------
- SQLite, stdlib only. Short-lived connections + a module lock = thread-safe for
  a low-frequency bot whose exit/shadow engines live on other threads.
- Never raises into the trading loop: all writes are defensive.
- Champion trades have shadow = '' / NULL; challenger (shadow-mode) trades carry
  the challenger's name so live and hypothetical histories never mix.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

import pytz

import config

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")
_lock = threading.RLock()

_COLUMNS = [
    "entry_ts", "exit_ts", "ticker", "strategy", "status",
    "regime", "time_bucket", "signals",
    "entry_price", "exit_price", "qty", "pnl", "pnl_pct",
    "exit_score", "exit_reason",
    "spread_bps", "liquidity",
    "sizing_model", "size_chosen", "size_reasoning",
    "blocked_then_allowed",
    "max_favorable_pct", "max_adverse_pct",
    "win", "shadow", "extra_tags",
]


# ── Setup ─────────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.JOURNAL_DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    """Create the database, table and indices if they don't exist."""
    os.makedirs(os.path.dirname(config.JOURNAL_DB_FILE) or ".", exist_ok=True)
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_ts            TEXT,
                exit_ts             TEXT,
                ticker              TEXT,
                strategy            TEXT,
                status              TEXT,
                regime              TEXT,
                time_bucket         TEXT,
                signals             TEXT,
                entry_price         REAL,
                exit_price          REAL,
                qty                 INTEGER,
                pnl                 REAL,
                pnl_pct             REAL,
                exit_score          REAL,
                exit_reason         TEXT,
                spread_bps          REAL,
                liquidity           TEXT,
                sizing_model        TEXT,
                size_chosen         INTEGER,
                size_reasoning      TEXT,
                blocked_then_allowed TEXT,
                max_favorable_pct   REAL,
                max_adverse_pct     REAL,
                win                 INTEGER,
                shadow              TEXT,
                extra_tags          TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON trades(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_regime ON trades(regime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow ON trades(shadow)")
    logger.info("Journal ready: %s", config.JOURNAL_DB_FILE)


# ── Tagging helpers ───────────────────────────────────────────────────────────

def time_bucket(dt: Optional[datetime] = None) -> str:
    """Bucket a datetime into a trading-session slot (ET)."""
    dt = dt or datetime.now(ET)
    hm = dt.strftime("%H:%M")
    if hm < "09:30":
        return "pre_market"
    if hm < "10:00":
        return "open_30"
    if hm < "12:00":
        return "morning"
    if hm < "14:00":
        return "midday"
    if hm < "15:00":
        return "afternoon"
    if hm < "16:00":
        return "power_hour"
    return "after_hours"


# ── Writing ───────────────────────────────────────────────────────────────────

def record_entry(
    ticker: str,
    strategy: str,
    entry_price: float,
    qty: int,
    regime: Optional[str] = None,
    signals: Optional[dict] = None,
    spread_bps: Optional[float] = None,
    liquidity: Optional[dict] = None,
    sizing_model: Optional[str] = None,
    size_chosen: Optional[int] = None,
    size_reasoning: Optional[str] = None,
    blocked_then_allowed: Optional[str] = None,
    shadow: str = "",
    entry_ts: Optional[datetime] = None,
    **extra,
) -> Optional[int]:
    """
    Insert an OPEN trade with its full entry context. Returns the trade id
    (used later by record_exit / update_excursion) or None on failure.
    """
    ts = entry_ts or datetime.now(ET)
    try:
        with _lock, _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades
                    (entry_ts, ticker, strategy, status, regime, time_bucket,
                     signals, entry_price, qty, spread_bps, liquidity,
                     sizing_model, size_chosen, size_reasoning,
                     blocked_then_allowed, shadow, extra_tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts.isoformat(), ticker, strategy, "open", regime, time_bucket(ts),
                    json.dumps(signals or {}, default=str), entry_price, qty,
                    spread_bps, json.dumps(liquidity or {}, default=str),
                    sizing_model, size_chosen, size_reasoning,
                    blocked_then_allowed, shadow or "",
                    json.dumps(extra or {}, default=str),
                ),
            )
            return cur.lastrowid
    except Exception as exc:  # never crash the trading loop
        logger.error("journal record_entry failed (%s): %s", ticker, exc)
        return None


def update_excursion(trade_id: int, pnl_pct: float) -> None:
    """
    Track the best (favorable) and worst (adverse) unrealised excursion of an
    open trade. Used to label win=1 as "reached +target before stop" rather than
    the cruder "closed green", which matters for the ML filter's honesty.
    """
    if trade_id is None:
        return
    try:
        with _lock, _connect() as conn:
            row = conn.execute(
                "SELECT max_favorable_pct, max_adverse_pct FROM trades "
                "WHERE id=? AND status='open'", (trade_id,)
            ).fetchone()
            if not row:
                return
            mfe = pnl_pct if row["max_favorable_pct"] is None else max(pnl_pct, row["max_favorable_pct"])
            mae = pnl_pct if row["max_adverse_pct"] is None else min(pnl_pct, row["max_adverse_pct"])
            conn.execute(
                "UPDATE trades SET max_favorable_pct=?, max_adverse_pct=? WHERE id=?",
                (mfe, mae, trade_id),
            )
    except Exception as exc:
        logger.error("journal update_excursion failed (id=%s): %s", trade_id, exc)


def record_exit(
    trade_id: int,
    exit_price: float,
    exit_reason: str = "",
    exit_score: Optional[float] = None,
    exit_ts: Optional[datetime] = None,
) -> None:
    """Close out a journaled trade, computing pnl, pnl_pct and the win label."""
    if trade_id is None:
        return
    ts = exit_ts or datetime.now(ET)
    try:
        with _lock, _connect() as conn:
            row = conn.execute(
                "SELECT entry_price, qty, max_favorable_pct FROM trades WHERE id=?",
                (trade_id,),
            ).fetchone()
            if not row:
                logger.warning("journal record_exit: trade id %s not found", trade_id)
                return
            entry = row["entry_price"] or 0.0
            qty = row["qty"] or 0
            pnl = (exit_price - entry) * qty
            pnl_pct = (exit_price - entry) / entry if entry else 0.0

            # Win label: did it reach +target before exit? Fall back to pnl sign
            # when no excursion was tracked (e.g. shadow trades).
            target = getattr(config, "JOURNAL_WIN_TARGET_PCT", 0.015)
            mfe = row["max_favorable_pct"]
            win = (1 if mfe >= target else 0) if mfe is not None else (1 if pnl > 0 else 0)

            conn.execute(
                """
                UPDATE trades
                   SET exit_ts=?, exit_price=?, pnl=?, pnl_pct=?,
                       exit_reason=?, exit_score=?, win=?, status='closed'
                 WHERE id=?
                """,
                (ts.isoformat(), exit_price, pnl, pnl_pct,
                 exit_reason, exit_score, win, trade_id),
            )
    except Exception as exc:
        logger.error("journal record_exit failed (id=%s): %s", trade_id, exc)


# ── Reading (most-recent-first unless noted) ──────────────────────────────────

def closed_trades(shadow: Optional[str] = "", limit: Optional[int] = None) -> List[dict]:
    """
    Closed trades, newest first. `shadow=''` (default) returns CHAMPION trades
    only; pass a challenger name for its hypothetical history, or shadow=None for
    everything.
    """
    q = "SELECT * FROM trades WHERE status='closed'"
    params: list = []
    if shadow is not None:
        q += " AND IFNULL(shadow,'')=?"
        params.append(shadow)
    q += " ORDER BY id DESC"
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    try:
        with _lock, _connect() as conn:
            return [dict(r) for r in conn.execute(q, params).fetchall()]
    except Exception as exc:
        logger.error("journal closed_trades failed: %s", exc)
        return []


def count(shadow: Optional[str] = "") -> int:
    """Number of closed trades (champion by default)."""
    q = "SELECT COUNT(*) AS n FROM trades WHERE status='closed'"
    params: list = []
    if shadow is not None:
        q += " AND IFNULL(shadow,'')=?"
        params.append(shadow)
    try:
        with _lock, _connect() as conn:
            return int(conn.execute(q, params).fetchone()["n"])
    except Exception as exc:
        logger.error("journal count failed: %s", exc)
        return 0


def regime_stats(shadow: Optional[str] = "") -> Dict[str, dict]:
    """
    Per-regime performance: {regime: {n, wins, win_rate, expectancy, total_pnl}}.
    `expectancy` is mean P&L per trade (dollars) — exactly what the regime master
    switch tests against REGIME_MIN_EXPECTANCY.
    """
    stats: Dict[str, dict] = {}
    for r in closed_trades(shadow=shadow):
        reg = r.get("regime") or "UNKNOWN"
        pnl = r.get("pnl") or 0.0
        s = stats.setdefault(reg, {"n": 0, "wins": 0, "total_pnl": 0.0})
        s["n"] += 1
        s["wins"] += 1 if pnl > 0 else 0
        s["total_pnl"] += pnl
    for s in stats.values():
        s["win_rate"] = s["wins"] / s["n"] if s["n"] else 0.0
        s["expectancy"] = s["total_pnl"] / s["n"] if s["n"] else 0.0
    return stats


def recent_winrate_and_ratio(n: int = 50, shadow: Optional[str] = "") -> tuple:
    """
    Returns (win_rate, win_loss_ratio, sample_size) over the last n closed trades.
    win_loss_ratio = avg_win / avg_loss; 0.0 signals "undefined" (no losses yet)
    so the caller can decide how to handle it. Feeds the Kelly sizer.
    """
    rows = closed_trades(shadow=shadow, limit=n)
    if not rows:
        return (0.0, 0.0, 0)
    pnls = [r.get("pnl") or 0.0 for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    sample = len(pnls)
    win_rate = len(wins) / sample
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    return (win_rate, ratio, sample)


def consecutive_losses(shadow: Optional[str] = "") -> int:
    """Length of the current losing streak (most recent closed trades)."""
    streak = 0
    for r in closed_trades(shadow=shadow, limit=50):  # newest first
        if (r.get("pnl") or 0.0) < 0:
            streak += 1
        else:
            break
    return streak
