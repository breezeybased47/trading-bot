"""
market_classifier.py
Classify the live market into one of five regimes from a candle DataFrame.

WHY
---
The same entry that prints money in a clean trend gets shredded in a choppy or
volatile tape. The regime label is the master context tag the entire research
stack conditions on (expectancy-per-regime, the regime master switch, journal
analysis, shadow variants). The spec asked to import a backtest
`market_classifier.py`; no such file or backtest exists in this repo, so this is
a fresh, self-contained, deterministic implementation meant to be the single
source of truth for both live trading and any future backtest — import it, do
not duplicate it.

METHOD (no lookahead — only closed bars up to "now" are used)
-------------------------------------------------------------
  trend     = % price change over REGIME_TREND_LOOKBACK bars
  vol_ratio = std(returns, REGIME_VOL_LOOKBACK) / std(returns, baseline)
  elevated  = vol_ratio >= REGIME_VOL_HIGH_MULT
  strong    = |trend| >= REGIME_TREND_THRESHOLD

  elevated            -> VOLATILE_UP / VOLATILE_DOWN   (by sign of trend)
  strong & not elev.  -> TRENDING_UP / TRENDING_DOWN
  otherwise           -> CHOPPY

KNOWN LIMITATIONS
-----------------
- 1-minute bars are noisy; thresholds are heuristic and want tuning against real
  logged outcomes (that's what the journal + regime_performance.json are for).
- "Volatile" is defined relative to the name's own recent baseline, not an
  absolute vol level, so a structurally calm name and a wild one are judged on
  their own terms.
"""

import logging
from typing import Optional

import pandas as pd

from config import (
    REGIME_TREND_LOOKBACK, REGIME_VOL_LOOKBACK, REGIME_VOL_BASELINE_LOOKBACK,
    REGIME_TREND_THRESHOLD, REGIME_VOL_HIGH_MULT,
)

logger = logging.getLogger(__name__)

TRENDING_UP   = "TRENDING_UP"
TRENDING_DOWN = "TRENDING_DOWN"
VOLATILE_UP   = "VOLATILE_UP"
VOLATILE_DOWN = "VOLATILE_DOWN"
CHOPPY        = "CHOPPY"

ALL_REGIMES = [TRENDING_UP, TRENDING_DOWN, VOLATILE_UP, VOLATILE_DOWN, CHOPPY]


def classify_detail(df: Optional[pd.DataFrame]) -> dict:
    """
    Return a dict with the regime label plus the measurements behind it
    (useful for the dashboard and for debugging why a regime was chosen).
    """
    need = max(REGIME_TREND_LOOKBACK, REGIME_VOL_BASELINE_LOOKBACK) + 1
    if df is None or len(df) < need or "close" not in df:
        return {"regime": CHOPPY, "reason": "insufficient_data",
                "trend": 0.0, "vol_ratio": 1.0, "bars": 0 if df is None else len(df)}

    close = df["close"].astype(float)

    p0 = close.iloc[-(REGIME_TREND_LOOKBACK + 1)]
    p1 = close.iloc[-1]
    trend = (p1 - p0) / p0 if p0 else 0.0

    rets = close.pct_change().dropna()
    recent_vol = float(rets.tail(REGIME_VOL_LOOKBACK).std() or 0.0)
    baseline_vol = float(rets.tail(REGIME_VOL_BASELINE_LOOKBACK).std() or 0.0)
    vol_ratio = (recent_vol / baseline_vol) if baseline_vol > 0 else 1.0

    elevated = vol_ratio >= REGIME_VOL_HIGH_MULT
    strong = abs(trend) >= REGIME_TREND_THRESHOLD
    up = trend >= 0

    if elevated:
        regime = VOLATILE_UP if up else VOLATILE_DOWN
    elif strong:
        regime = TRENDING_UP if up else TRENDING_DOWN
    else:
        regime = CHOPPY

    return {
        "regime": regime,
        "trend": round(trend, 5),
        "vol_ratio": round(vol_ratio, 3),
        "elevated": elevated,
        "strong": strong,
        "bars": len(df),
        "reason": "ok",
    }


def classify(df: Optional[pd.DataFrame]) -> str:
    """Return just the regime label (one of ALL_REGIMES)."""
    return classify_detail(df)["regime"]
