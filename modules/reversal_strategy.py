"""
reversal_strategy.py  —  Course entry setup: "3 Stages of a Reversal".

Faithful to the course lesson (a common, public break-and-retest concept). Pure
price structure — no indicators:

  Stage 1 — REJECTION:     price sells off making lower lows (a prior high sits
                           well above the area it later settles into).
  Stage 2 — CONSOLIDATION: it stops dropping and trades a TIGHT range with a
                           roughly parallel support and resistance.
  Stage 3 — CONFIRMATION = ENTRY: price BREAKS above the range's resistance, then
                           PULLS BACK and the old resistance HOLDS as new support
                           (the key rule: it must NOT sell back off into the
                           range), and turns back up.

  Stop  = just below the new support (the broken resistance).
  Target = REVERSAL_TARGET_R x risk (the course doesn't specify a target — this
           is a sensible default; tune it).

NOTE the course teaches this on DAILY/SWING charts; here it runs on the bot's
1-minute intraday bars, so it detects intraday break-and-retests (a faster cousin
of the daily setup). Run as a PAPER challenger — never sends real orders.
"""

import logging
from typing import Dict, Optional

import numpy as np

from config import (
    REVERSAL_LOOKBACK, REVERSAL_RANGE_MAX_PCT, REVERSAL_REJECTION_MIN_PCT,
    REVERSAL_BREAKOUT_BUFFER, REVERSAL_RETEST_TOL, REVERSAL_RETEST_MAX_BARS,
    REVERSAL_STOP_BUFFER, REVERSAL_TARGET_R,
)
from modules.strategy import BUY, Signal

logger = logging.getLogger(__name__)


class ReversalStrategy:
    def __init__(self):
        self._st: Dict[str, dict] = {}

    def _reset(self, ticker: str):
        self._st[ticker] = {"stage": 0, "res": None, "sup": None,
                            "broke": None, "age": 0, "retested": False}

    def stage(self, ticker: str) -> int:
        return self._st.get(ticker, {}).get("stage", 0)

    def evaluate(self, ticker: str, candles) -> Optional[Signal]:
        L = REVERSAL_LOOKBACK
        if candles is None or len(candles) < 2 * L + 2:
            return None
        try:
            highs = candles["high"].to_numpy(dtype=float)
            lows = candles["low"].to_numpy(dtype=float)
            closes = candles["close"].to_numpy(dtype=float)
        except Exception:
            return None

        price = float(closes[-1])
        prev_close = float(closes[-2])
        low = float(lows[-1])
        if price <= 0:
            return None

        # The consolidation window = the L bars BEFORE the current bar (so the
        # current breakout bar never inflates the resistance it must clear).
        res = float(highs[-(L + 1):-1].max())
        sup = float(lows[-(L + 1):-1].min())
        pre_hi = float(highs[-(2 * L + 1):-(L + 1)].max())   # the pre-consolidation high
        rejected = res > 0 and (pre_hi - res) / res >= REVERSAL_REJECTION_MIN_PCT
        range_pct = (res - sup) / price if price > 0 else 1.0

        st = self._st.setdefault(ticker, {"stage": 0, "res": None, "sup": None,
                                          "broke": None, "age": 0, "retested": False})

        # Stages 1-2 — find a tight consolidation that followed a rejection, then a breakout.
        if st["stage"] in (0, 1):
            if range_pct <= REVERSAL_RANGE_MAX_PCT and rejected:
                st["stage"] = 1
                st["res"], st["sup"] = res, sup
            if st["stage"] == 1 and price > st["res"] * (1 + REVERSAL_BREAKOUT_BUFFER):
                st["stage"] = 2
                st["broke"] = st["res"]
                st["age"] = 0
                st["retested"] = False
            return None

        # Stage 3 — await the pullback that holds the old resistance, then turns up.
        if st["stage"] == 2:
            st["age"] += 1
            broke = st["broke"]
            if price < broke:                              # failed breakout — sold back off
                self._reset(ticker)
                return None
            if st["age"] > REVERSAL_RETEST_MAX_BARS:        # never came back to retest
                self._reset(ticker)
                return None
            if low <= broke * (1 + REVERSAL_RETEST_TOL):    # pulled back and tapped old resistance
                st["retested"] = True
            if st["retested"] and price > broke and price >= prev_close:   # held + turning up = ENTRY
                stop = round(broke * (1 - REVERSAL_STOP_BUFFER), 2)
                risk = price - stop
                target = round(price + REVERSAL_TARGET_R * risk, 2) if risk > 0 else None
                sig = Signal(ticker, BUY, "reversal",
                             "break + retest hold (old resistance -> new support)", price)
                sig.stop = stop
                sig.target = target
                self._reset(ticker)
                return sig

        return None
