"""
cooldowns.py  —  Module 5: adaptive cooldowns + per-ticker heat.

WHY
---
A flat 15-minute timeout treats a clean, planned exit the same as getting
stopped out twice in a row. It shouldn't. Revenge-trading a name that just
whipsawed you is one of the most reliable ways to bleed an account, so the
cooldown should get LONGER exactly when a ticker is misbehaving and SHORTER when
things are going cleanly:

  clean win  (exited on a signal/target, not a stop)  -> 7 min
  stop loss  (exited via a stop)                       -> 30 min
  whipsaw    (entered and stopped within 3 min)        -> 60 min
  neutral    (anything else)                           -> 15 min  (base)

On top of that:
  - each CONSECUTIVE loss on a ticker DOUBLES its cooldown that day (2^(n-1)),
  - a per-ticker "heat" score rises with bad trades and decays over time; the
    hotter a ticker, the more its cooldown is extended.

The base bot has no cooldown today, so this is gated by ADAPTIVE_COOLDOWN_ENABLED
(default OFF) and wired into risk_manager.approve_entry in the integration pass.
Heat is still tracked when disabled so the dashboard can show it in observe mode.
"""

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Dict, Optional

import pytz

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


def _now() -> datetime:
    return datetime.now(ET)


def classify_outcome(pnl: float, exit_reason: str, hold_seconds: Optional[float]) -> str:
    """Map a closed trade to a cooldown category."""
    reason = (exit_reason or "").lower()
    is_stop = "stop" in reason
    if is_stop and hold_seconds is not None and hold_seconds <= config.WHIPSAW_SECONDS:
        return "whipsaw"
    if pnl < 0 and is_stop:
        return "stop_loss"
    if pnl > 0 and not is_stop:
        return "clean_win"
    return "neutral"


_BASE_MIN = {
    "clean_win": "COOLDOWN_CLEAN_WIN_MIN",
    "stop_loss": "COOLDOWN_STOP_LOSS_MIN",
    "whipsaw": "COOLDOWN_WHIPSAW_MIN",
    "neutral": "COOLDOWN_BASE_MIN",
}


class CooldownManager:
    def __init__(self):
        self._cooldown_until: Dict[str, datetime] = {}
        self._consec_losses: Dict[str, int] = {}
        self._heat: Dict[str, float] = {}
        self._heat_ts: Dict[str, datetime] = {}
        self._last_reason: Dict[str, str] = {}
        self._day: Dict[str, date] = {}
        self._lock = threading.RLock()

    # ── Heat with time decay ──────────────────────────────────────────────────

    def _decayed_heat(self, ticker: str, now: datetime) -> float:
        h = self._heat.get(ticker, 0.0)
        last = self._heat_ts.get(ticker)
        if last is not None and h > 0:
            hours = max(0.0, (now - last).total_seconds() / 3600.0)
            h = max(0.0, h - config.HEAT_DECAY_PER_HOUR * hours)
        return h

    def heat(self, ticker: str, now: Optional[datetime] = None) -> float:
        return round(self._decayed_heat(ticker, now or _now()), 3)

    # ── Register a closed trade ───────────────────────────────────────────────

    def register_close(self, ticker: str, pnl: float, exit_reason: str,
                       hold_seconds: Optional[float] = None,
                       now: Optional[datetime] = None) -> dict:
        """Update cooldown + heat after a trade closes. Returns the decision detail."""
        now = now or _now()
        with self._lock:
            # daily reset of the consecutive-loss counter
            if self._day.get(ticker) != now.date():
                self._consec_losses[ticker] = 0
                self._day[ticker] = now.date()

            outcome = classify_outcome(pnl, exit_reason, hold_seconds)
            base_min = getattr(config, _BASE_MIN[outcome])

            is_loss = pnl < 0
            if is_loss:
                self._consec_losses[ticker] = self._consec_losses.get(ticker, 0) + 1
            else:
                self._consec_losses[ticker] = 0
            consec = self._consec_losses[ticker]
            loss_mult = 2 ** (consec - 1) if consec >= 1 else 1

            # heat: decay to now, then add for this trade
            cur_heat = self._decayed_heat(ticker, now)
            if outcome == "whipsaw":
                cur_heat += config.HEAT_PER_WHIPSAW
            elif is_loss:
                cur_heat += config.HEAT_PER_LOSS
            self._heat[ticker] = cur_heat
            self._heat_ts[ticker] = now

            total_min = base_min * loss_mult + cur_heat * config.HEAT_COOLDOWN_MIN_PER_UNIT
            self._cooldown_until[ticker] = now + timedelta(minutes=total_min)
            reason = ("%s: base %dm x%d (consec_loss=%d) + heat %.1f -> %.1fm"
                      % (outcome, base_min, loss_mult, consec, cur_heat, total_min))
            self._last_reason[ticker] = reason

        slog.log_event("cooldown_set", ticker=ticker, outcome=outcome,
                       minutes=round(total_min, 1), consec_losses=consec,
                       heat=round(cur_heat, 2), enabled=config.ADAPTIVE_COOLDOWN_ENABLED)
        return {"ticker": ticker, "outcome": outcome, "minutes": round(total_min, 1),
                "consec_losses": consec, "heat": round(cur_heat, 3), "reason": reason}

    # ── Gate ──────────────────────────────────────────────────────────────────

    def is_blocked(self, ticker: str, now: Optional[datetime] = None) -> dict:
        now = now or _now()
        until = self._cooldown_until.get(ticker)
        remaining = max(0.0, (until - now).total_seconds()) if until else 0.0
        if not config.ADAPTIVE_COOLDOWN_ENABLED:
            return {"blocked": False, "seconds": remaining, "reason": "filter_off"}
        blocked = remaining > 0
        return {"blocked": blocked, "seconds": round(remaining),
                "reason": self._last_reason.get(ticker, "") if blocked else "ok"}

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def status(self, now: Optional[datetime] = None) -> Dict[str, dict]:
        now = now or _now()
        out = {}
        tickers = set(self._cooldown_until) | set(self._heat)
        for t in tickers:
            until = self._cooldown_until.get(t)
            remaining = max(0.0, (until - now).total_seconds()) if until else 0.0
            out[t] = {
                "cooldown_seconds": round(remaining),
                "cooldown_minutes": round(remaining / 60.0, 1),
                "heat": self.heat(t, now),
                "consec_losses": self._consec_losses.get(t, 0),
            }
        return out
