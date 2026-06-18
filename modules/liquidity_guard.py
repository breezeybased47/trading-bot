"""
liquidity_guard.py  —  Module 4: liquidity & market-impact guard (free-tier).

WHY
---
A great signal still loses money if you can only fill it at a terrible price.
This guard refuses entries when the market is too thin or the spread is too wide,
and it logs predicted-vs-actual slippage on every fill so a real slippage model
accumulates from live data.

FREE-TIER REALITY (important, honest limitation)
------------------------------------------------
Alpaca's free IEX feed gives only TOP-OF-BOOK bid/ask — there is no Level-2 order
book, so the spec's "sum displayed depth within 0.2% of mid" cannot be computed.
For this universe (mega-caps + leveraged ETFs at a ≤10% paper position) true
market impact is negligible anyway, so the genuinely useful, achievable signals
are:

  1. SPREAD GUARD (primary, actionable): reject entries when the bid/ask spread is
     abnormally wide for that name — tight cap for AAPL/MSFT, looser for TQQQ/SQQQ.
     Wide spreads are the real free-tier tell for "bad time to trade this."
  2. IMPACT PROXY: since real depth is invisible, approximate "absorbable size"
     by the LARGER of top-of-book quote size and recent average 1-minute volume.
     DEPTH_* thresholds then mean "% of typical 1-minute liquidity", which for
     mega-caps essentially never fires (correct) but catches genuinely thin or
     halted conditions (also correct).
  3. SLIPPAGE LOG: predicted (half-spread) vs actual (fill vs reference) per fill.

The guard FAILS OPEN when no quote is available (never silently halts trading).
"""

import json
import logging
import os
import threading
from typing import Optional

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)
_slip_lock = threading.Lock()


# ── Pure helpers (unit tested) ────────────────────────────────────────────────

def spread_bps(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid * 1e4 if mid > 0 else None


def max_spread_bps(ticker: str) -> float:
    return float(config.SPREAD_MAX_BPS_OVERRIDES.get(ticker, config.SPREAD_MAX_BPS_DEFAULT))


def slippage_bps(side: str, reference_price: float, fill_price: float) -> Optional[float]:
    """Signed slippage in bps: positive = worse than reference (paid up on a buy)."""
    if not reference_price or reference_price <= 0:
        return None
    sign = 1.0 if side.upper() == "BUY" else -1.0
    return sign * (fill_price - reference_price) / reference_price * 1e4


def assess(ticker: str, intended_shares: int, bid: Optional[float], ask: Optional[float],
           depth_proxy: Optional[float], side: str = "BUY") -> dict:
    """
    Pure liquidity decision. Returns {allow, adjusted_shares, spread_bps, reason}.
    `adjusted_shares` may be reduced below intended; 0 means rejected.
    """
    sp = spread_bps(bid, ask)
    result = {"allow": True, "adjusted_shares": intended_shares,
              "spread_bps": _r(sp), "reason": "liquidity_ok", "depth_proxy": _r(depth_proxy)}

    if sp is None:
        result["reason"] = "no_quote_data"      # fail open
        return result

    cap = max_spread_bps(ticker)
    if sp > cap:
        result.update(allow=False, adjusted_shares=0,
                      reason="spread %.1fbps > %.1fbps max for %s" % (sp, cap, ticker))
        return result

    if depth_proxy and depth_proxy > 0 and intended_shares > 0:
        ratio = intended_shares / depth_proxy
        if ratio > config.DEPTH_REJECT_PCT:
            result.update(allow=False, adjusted_shares=0,
                          reason="size %d is %.0f%% of liquidity proxy %.0f (>%.0f%% reject)"
                          % (intended_shares, ratio * 100, depth_proxy, config.DEPTH_REJECT_PCT * 100))
            return result
        if ratio > config.DEPTH_REDUCE_PCT:
            safe = max(1, int(depth_proxy * config.DEPTH_REDUCE_PCT))
            result.update(adjusted_shares=min(intended_shares, safe),
                          reason="reduced %d->%d (was %.0f%% of liquidity proxy)"
                          % (intended_shares, min(intended_shares, safe), ratio * 100))
            return result

    return result


# ── Live wrapper ──────────────────────────────────────────────────────────────

class LiquidityGuard:
    def __init__(self, data_feed):
        self._feed = data_feed

    def check_entry(self, ticker: str, intended_shares: int, side: str = "BUY") -> dict:
        if not config.LIQUIDITY_GUARD_ENABLED:
            return {"allow": True, "adjusted_shares": intended_shares,
                    "spread_bps": None, "reason": "filter_off"}

        quote = self._feed.get_quote(ticker) if hasattr(self._feed, "get_quote") else None
        bid = quote.get("bid") if quote else None
        ask = quote.get("ask") if quote else None
        depth = self._depth_proxy(ticker, quote, side)

        res = assess(ticker, intended_shares, bid, ask, depth, side)
        if not res["allow"]:
            slog.log_block("liquidity", ticker, res["reason"], spread_bps=res["spread_bps"])
        elif res["adjusted_shares"] != intended_shares:
            slog.log_decision("liquidity_reduce", ticker, **{
                "from": intended_shares, "to": res["adjusted_shares"], "reason": res["reason"]})
        return res

    def _depth_proxy(self, ticker: str, quote: Optional[dict], side: str) -> Optional[float]:
        sizes = []
        if quote:
            s = quote.get("ask_size") if side.upper() == "BUY" else quote.get("bid_size")
            if s:
                sizes.append(float(s))
        candles = self._feed.get_candles(ticker) if hasattr(self._feed, "get_candles") else None
        try:
            if candles is not None and "volume" in candles and len(candles) >= 5:
                sizes.append(float(candles["volume"].tail(20).mean()))
        except Exception:
            pass
        return max(sizes) if sizes else None

    def predicted_slippage_bps(self, ticker: str, side: str = "BUY") -> Optional[float]:
        """Cheap pre-trade estimate: you cross roughly half the spread."""
        quote = self._feed.get_quote(ticker) if hasattr(self._feed, "get_quote") else None
        if not quote:
            return None
        sp = spread_bps(quote.get("bid"), quote.get("ask"))
        return _r(sp / 2.0) if sp is not None else None

    def record_fill(self, ticker: str, side: str, reference_price: float,
                    fill_price: float, predicted_bps: Optional[float] = None) -> Optional[float]:
        """Log predicted vs actual slippage for one fill (builds the slippage model)."""
        actual = slippage_bps(side, reference_price, fill_price)
        rec = {"ticker": ticker, "side": side, "reference": reference_price,
               "fill": fill_price, "predicted_bps": _r(predicted_bps), "actual_bps": _r(actual)}
        slog.log_event("slippage", **rec)
        _append_jsonl(config.SLIPPAGE_LOG_FILE, rec)
        return actual


# ── Slippage file I/O ─────────────────────────────────────────────────────────

def _append_jsonl(path: str, record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _slip_lock, open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        logger.error("slippage log write failed: %s", exc)


def slippage_summary(path: Optional[str] = None) -> dict:
    """Mean predicted vs actual slippage (bps) over the logged history — for reports."""
    path = path or config.SLIPPAGE_LOG_FILE
    preds, acts = [], []
    try:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if r.get("predicted_bps") is not None:
                        preds.append(r["predicted_bps"])
                    if r.get("actual_bps") is not None:
                        acts.append(r["actual_bps"])
    except Exception as exc:
        logger.error("slippage summary read failed: %s", exc)
    return {
        "n": len(acts),
        "mean_predicted_bps": round(sum(preds) / len(preds), 2) if preds else None,
        "mean_actual_bps": round(sum(acts) / len(acts), 2) if acts else None,
    }


def _r(v: Optional[float]):
    return round(v, 2) if v is not None else None
