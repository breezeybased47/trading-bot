"""
overbought_reversal.py  —  Course setup: "overbought reversals" (SHORT). PAPER ONLY.

The teachable core of the lesson (the rest of that video is a paid-tool ad): short
a stock that has rallied so hard it's "screaming overbought" AND is now showing
signs of an ACTIVE sell-off, then cover the same day. Implemented as pure
technicals — no paid "fair value" data:

  Overbought + extended : RSI >= RSI_OVERBOUGHT (70) AND price stretched at/above
                          the upper Bollinger band.
  Active sell-off = SHORT: after being overbought, RSI rolls back DOWN through 70
                          and the bar closes lower (the rally is breaking).
  Stop  = just above the overbought peak. Target = OVERBOUGHT_TARGET_R x risk down.

IMPORTANT: the live bot is long-only (it gets downside only by BUYING SQQQ), so
this runs exclusively as a PAPER challenger — it never sends a real order. It also
fires rarely on stable mega-caps; it's built for the pump-and-dump names the lesson
targets (of the bot's universe, TSLA is the closest fit).
"""

import logging
from typing import Dict, Optional

from config import (MIN_CANDLES, RSI_OVERBOUGHT, OVERBOUGHT_LOOKBACK,
                    OVERBOUGHT_TARGET_R, OVERBOUGHT_STOP_BUFFER)
from modules.indicators import compute, latest
from modules.strategy import SHORT, Signal

logger = logging.getLogger(__name__)


class OverboughtReversalStrategy:
    def __init__(self):
        self._st: Dict[str, dict] = {}

    def _reset(self, ticker: str):
        self._st[ticker] = {"overbought_age": 0, "peak": None}

    def evaluate(self, ticker: str, candles) -> Optional[Signal]:
        if candles is None or len(candles) < MIN_CANDLES:
            return None
        df = compute(candles)
        if df is None:
            return None
        v = latest(df)
        rsi, rsi_prev = v.get("rsi"), v.get("rsi_prev")
        price, bb_upper = v.get("price"), v.get("bb_upper")
        if rsi is None or price is None:
            return None
        try:
            prev_close = float(candles["close"].iloc[-2])
            high = float(candles["high"].iloc[-1])
        except Exception:
            return None

        st = self._st.setdefault(ticker, {"overbought_age": 0, "peak": None})

        # Overbought & extended -> remember it, track the peak.
        extended = (bb_upper is None) or (price >= bb_upper)
        if rsi >= RSI_OVERBOUGHT and extended:
            st["overbought_age"] = OVERBOUGHT_LOOKBACK
            st["peak"] = max(st["peak"], high) if st["peak"] is not None else high
            return None

        # After being overbought, wait for the active sell-off (RSI rolls below 70
        # on a down bar) within the lookback window.
        if st["overbought_age"] > 0:
            st["overbought_age"] -= 1
            if rsi_prev is not None and rsi_prev >= RSI_OVERBOUGHT > rsi and price < prev_close:
                peak = st["peak"] if st["peak"] is not None else high
                stop = round(peak * (1 + OVERBOUGHT_STOP_BUFFER), 2)
                risk = stop - price
                target = round(price - OVERBOUGHT_TARGET_R * risk, 2) if risk > 0 else None
                sig = Signal(ticker, SHORT, "overbought_short",
                             "overbought (RSI>%d, >upper BB) + active sell-off" % RSI_OVERBOUGHT, price)
                sig.stop = stop
                sig.target = target
                self._reset(ticker)
                return sig

        return None
