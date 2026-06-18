"""
confirmation.py  —  Course entry discipline: "Waiting For Confirmation".

Wraps ANY strategy. When the wrapped strategy fires a BUY, this overlay does NOT
act immediately — it waits for the market to CONFIRM by closing above the signal
bar's high within CONFIRMATION_MAX_BARS. If price instead closes back below the
signal bar's low, the setup is invalidated and dropped; if it just never confirms
in time, it's skipped. This is the standard "don't anticipate, wait for the
candle to prove it" rule, used here to A/B whether waiting helps.

Run as a paper challenger (e.g. champion + confirmation) so its effect on the
real strategy can be measured before it's trusted.
"""

import logging
from typing import Optional

from config import CONFIRMATION_MAX_BARS
from modules.strategy import BUY, Signal

logger = logging.getLogger(__name__)


class ConfirmationOverlay:
    def __init__(self, strategy):
        self._strategy = strategy
        self._pending = {}   # ticker -> {signal, high, low, bars}

    def evaluate(self, ticker: str, candles) -> Optional[Signal]:
        try:
            close = float(candles["close"].iloc[-1])
        except Exception:
            return None

        # 1) Resolve any pending setup first.
        p = self._pending.get(ticker)
        if p is not None:
            p["bars"] += 1
            if close < p["low"]:                       # invalidated
                del self._pending[ticker]
                return None
            if close > p["high"]:                      # confirmed!
                sig = p["signal"]
                del self._pending[ticker]
                confirmed = Signal(ticker, BUY, getattr(sig, "strategy", "confirmed"),
                                   "confirmed: " + getattr(sig, "reason", ""), close)
                for attr in ("stop", "target"):
                    if hasattr(sig, attr):
                        setattr(confirmed, attr, getattr(sig, attr))
                return confirmed
            if p["bars"] >= CONFIRMATION_MAX_BARS:     # timed out
                del self._pending[ticker]
            return None

        # 2) No pending setup — ask the wrapped strategy.
        sig = self._strategy.evaluate(ticker, candles)
        if sig and sig.action == BUY:
            try:
                self._pending[ticker] = {
                    "signal": sig,
                    "high": float(candles["high"].iloc[-1]),
                    "low": float(candles["low"].iloc[-1]),
                    "bars": 0,
                }
            except Exception:
                return sig
            return None        # hold — wait for confirmation
        return sig             # pass SELLs / None straight through
