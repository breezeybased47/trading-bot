"""
reversal_strategy.py  —  Course entry setup: "3 Stages of a Reversal".

This is a STANDARD technical-analysis interpretation of a common concept (not a
claim about the user's specific course — tune the thresholds to match it). It is
run as a PAPER challenger (no real orders) so it can be A/B-tested against the
live strategy before it's ever trusted.

Bullish reversal state machine (mirror the logic for shorts):
  Stage 1 — Exhaustion: RSI < oversold while price makes a fresh low. The
            down-move is overextended (capitulation).
  Stage 2 — Base: RSI turns back up by REVERSAL_RSI_TURN (momentum divergence)
            and price holds a HIGHER low than the exhaustion low.
  Stage 3 — Breakout = BUY: price closes back above the 9-EMA (reclaim).
  Stop   = the stage-2 base low. Target = REVERSAL_TARGET_R x risk (default 2R).
  Invalidation: a new low below the exhaustion low resets the whole setup.

Reuses indicators.compute/latest and strategy.Signal — no forked logic.
"""

import logging
from typing import Dict, Optional

from config import MIN_CANDLES, REVERSAL_RSI_TURN, REVERSAL_TARGET_R, RSI_OVERSOLD
from modules.indicators import compute, latest
from modules.strategy import BUY, Signal

logger = logging.getLogger(__name__)


class ReversalStrategy:
    def __init__(self):
        self._st: Dict[str, dict] = {}

    def _reset(self, ticker: str):
        self._st[ticker] = {"stage": 0, "cap_low": None, "cap_rsi": None, "base_low": None}

    def stage(self, ticker: str) -> int:
        return self._st.get(ticker, {}).get("stage", 0)

    def evaluate(self, ticker: str, candles) -> Optional[Signal]:
        if candles is None or len(candles) < MIN_CANDLES:
            return None
        df = compute(candles)
        if df is None:
            return None
        v = latest(df)
        rsi, ema9, price = v.get("rsi"), v.get("ema9"), v.get("price")
        if rsi is None or price is None:
            return None
        try:
            low = float(candles["low"].iloc[-1])
        except Exception:
            return None

        st = self._st.setdefault(ticker, {"stage": 0, "cap_low": None, "cap_rsi": None, "base_low": None})

        # Stage 1 — exhaustion: oversold while making a fresh low
        if rsi < RSI_OVERSOLD:
            if st["cap_low"] is None or low < st["cap_low"]:
                self._st[ticker] = {"stage": 1, "cap_low": low, "cap_rsi": rsi, "base_low": None}
            return None

        if st["stage"] >= 1:
            # invalidation — a new low under the capitulation low kills the setup
            if st["cap_low"] is not None and low < st["cap_low"]:
                self._reset(ticker)
                return None

            # Stage 2 — RSI turned up (divergence) -> we're basing on a higher low
            if st["stage"] == 1 and st["cap_rsi"] is not None and rsi >= st["cap_rsi"] + REVERSAL_RSI_TURN:
                st["stage"] = 2
                st["base_low"] = low
            elif st["stage"] == 2:
                st["base_low"] = min(st["base_low"], low) if st["base_low"] is not None else low

            # Stage 3 — breakout: reclaim the 9-EMA -> BUY
            if st["stage"] == 2 and ema9 is not None and price > ema9:
                stop = st["base_low"] if st["base_low"] is not None else st["cap_low"]
                risk = price - stop if stop is not None else 0.0
                target = price + REVERSAL_TARGET_R * risk if risk > 0 else None
                sig = Signal(ticker, BUY, "reversal",
                             "3-stage reversal: reclaimed 9-EMA off a higher low", price)
                sig.stop = round(stop, 2) if stop is not None else None
                sig.target = round(target, 2) if target is not None else None
                self._reset(ticker)
                return sig

        return None
