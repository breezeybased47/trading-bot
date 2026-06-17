"""
indicators.py
Calculates RSI, MACD, EMA 9/21/50, VWAP, and Bollinger Bands using the `ta` library.
"""

import logging
from typing import Optional

import pandas as pd
import ta
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands

from config import (
    BB_PERIOD, BB_STD, EMA_LONG, EMA_MED, EMA_SHORT,
    MACD_FAST, MACD_SIGNAL, MACD_SLOW, RSI_PERIOD,
)

logger = logging.getLogger(__name__)


def compute(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Add all indicator columns to a copy of the candle DataFrame.
    Returns None when there aren't enough bars to compute EMA-50.
    """
    if df is None or len(df) < EMA_LONG:
        return None

    df = df.copy()

    try:
        close = df["close"]

        # RSI
        df["rsi"] = RSIIndicator(close=close, window=RSI_PERIOD).rsi()

        # MACD
        macd_obj = MACD(
            close=close,
            window_fast=MACD_FAST,
            window_slow=MACD_SLOW,
            window_sign=MACD_SIGNAL,
        )
        df["macd"]      = macd_obj.macd()
        df["macd_sig"]  = macd_obj.macd_signal()
        df["macd_hist"] = macd_obj.macd_diff()   # histogram = MACD - signal

        # Exponential Moving Averages
        df["ema9"]  = EMAIndicator(close=close, window=EMA_SHORT).ema_indicator()
        df["ema21"] = EMAIndicator(close=close, window=EMA_MED).ema_indicator()
        df["ema50"] = EMAIndicator(close=close, window=EMA_LONG).ema_indicator()

        # Session VWAP (resets daily)
        df["vwap_ind"] = _session_vwap(df)

        # Bollinger Bands
        bb = BollingerBands(close=close, window=BB_PERIOD, window_dev=BB_STD)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_mid"]   = bb.bollinger_mavg()
        df["bb_lower"] = bb.bollinger_lband()

        return df

    except Exception as exc:
        logger.error(f"Indicator compute error: {exc}")
        return None


def latest(df: pd.DataFrame) -> dict:
    """
    Return a flat dict of the most recent indicator values.
    Strategy logic reads from this dict.
    """
    if df is None or df.empty:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    return {
        "price":          float(last.get("close", 0)),
        "rsi":            _f(last.get("rsi")),
        "rsi_prev":       _f(prev.get("rsi")),
        "macd":           _f(last.get("macd")),
        "macd_hist":      _f(last.get("macd_hist")),
        "macd_hist_prev": _f(prev.get("macd_hist")),
        "ema9":           _f(last.get("ema9")),
        "ema21":          _f(last.get("ema21")),
        "ema50":          _f(last.get("ema50")),
        "vwap":           _f(last.get("vwap_ind")),
        "bb_upper":       _f(last.get("bb_upper")),
        "bb_mid":         _f(last.get("bb_mid")),
        "bb_lower":       _f(last.get("bb_lower")),
        "volume":         _f(last.get("volume")),
    }


def nasdaq_is_bearish(qqq_df: pd.DataFrame) -> bool:
    """
    Returns True when QQQ closes below its 50-period EMA —
    signals a bearish NASDAQ environment (rotate to SQQQ).
    """
    if qqq_df is None or len(qqq_df) < EMA_LONG:
        return False
    calc = compute(qqq_df)
    if calc is None:
        return False
    row = calc.iloc[-1]
    price = row.get("close", 0)
    ema50 = row.get("ema50", 0)
    return bool(price and ema50 and price < ema50)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP that resets at the start of each calendar day."""
    df = df.copy()
    df["tp"]     = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]

    try:
        dates = df.index.date
    except AttributeError:
        dates = pd.to_datetime(df.index).date

    vwap_vals  = []
    cum_tp_vol: dict = {}
    cum_vol:    dict = {}

    for i, d in enumerate(dates):
        cum_tp_vol[d] = cum_tp_vol.get(d, 0) + df["tp_vol"].iloc[i]
        cum_vol[d]    = cum_vol.get(d, 0)    + df["volume"].iloc[i]
        v = cum_tp_vol[d] / cum_vol[d] if cum_vol[d] else df["close"].iloc[i]
        vwap_vals.append(v)

    return pd.Series(vwap_vals, index=df.index)


def _f(val) -> Optional[float]:
    """Safely convert to float; return None for NaN/None."""
    try:
        v = float(val)
        import math
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None
