"""
strategy.py
Three strategies:
  1. Momentum    — RSI crossover + MACD histogram confirmation
  2. Mean Rev    — oversold RSI + price at lower Bollinger Band
  3. ETF Rotate  — TQQQ when NASDAQ bullish, SQQQ when bearish
"""

import logging
from datetime import datetime
from typing import Dict, Optional

import pandas as pd
import pytz

from modules.indicators import compute, latest, nasdaq_is_bearish
from config import (
    LEVERAGED_ETFS, MIN_CANDLES, QQQ_TICKER,
    RSI_BUY, RSI_OVERSOLD, RSI_SELL,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

BUY  = "BUY"
SELL = "SELL"
HOLD = "HOLD"


class Signal:
    """A trade instruction produced by one of the strategy functions."""

    def __init__(self, ticker: str, action: str, strategy: str, reason: str, price: float):
        self.ticker   = ticker
        self.action   = action      # BUY | SELL
        self.strategy = strategy    # "momentum" | "mean_reversion" | "etf_rotation"
        self.reason   = reason
        self.price    = price
        self.ts       = datetime.now(ET)

    def __repr__(self):
        return f"Signal({self.action} {self.ticker} @{self.price:.2f} [{self.strategy}])"


class StrategyEngine:
    """
    Receives candle DataFrames from the data feed and produces BUY/SELL signals.
    Keeps per-ticker state only for RSI crossover detection.
    """

    def __init__(self, data_feed):
        self._feed = data_feed
        self._prev_rsi: Dict[str, float] = {}

    def evaluate(self, ticker: str, candles: pd.DataFrame) -> Optional[Signal]:
        """
        Main entry — called on every new 1-minute bar.
        Returns a Signal or None.
        """
        if len(candles) < MIN_CANDLES:
            return None

        df = compute(candles)
        if df is None:
            return None

        vals = latest(df)
        if not vals or vals.get("rsi") is None:
            return None

        # Route to the right strategy
        if ticker in LEVERAGED_ETFS:
            sig = self._etf_rotation(ticker, vals)
        else:
            sig = self._momentum(ticker, vals) or self._mean_reversion(ticker, vals)

        # Always update previous RSI after evaluation
        self._prev_rsi[ticker] = vals["rsi"]
        return sig

    # ── Strategy 1: Momentum ──────────────────────────────────────────────────

    def _momentum(self, ticker: str, v: dict) -> Optional[Signal]:
        """
        BUY  — RSI crosses above RSI_BUY AND MACD histogram is positive
        SELL — RSI crosses below RSI_SELL
        """
        rsi      = v.get("rsi")
        rsi_prev = self._prev_rsi.get(ticker)
        hist     = v.get("macd_hist")
        price    = v.get("price")

        if rsi is None or price is None or rsi_prev is None:
            return None

        # Bullish crossover
        if rsi_prev < RSI_BUY <= rsi and hist is not None and hist > 0:
            return Signal(ticker, BUY, "momentum",
                          f"RSI {rsi_prev:.1f}→{rsi:.1f} crossed {RSI_BUY}, MACD hist {hist:+.3f}",
                          price)

        # Bearish crossover
        if rsi_prev >= RSI_SELL > rsi:
            return Signal(ticker, SELL, "momentum",
                          f"RSI {rsi_prev:.1f}→{rsi:.1f} crossed below {RSI_SELL}",
                          price)

        return None

    # ── Strategy 2: Mean Reversion ────────────────────────────────────────────

    def _mean_reversion(self, ticker: str, v: dict) -> Optional[Signal]:
        """
        BUY  — RSI oversold (<30) AND price at/below lower Bollinger Band
        EXIT — price reaches VWAP (mean reversion target)
        """
        rsi      = v.get("rsi")
        price    = v.get("price")
        bb_lower = v.get("bb_lower")
        vwap     = v.get("vwap")

        if rsi is None or price is None:
            return None

        if bb_lower and rsi < RSI_OVERSOLD and price <= bb_lower * 1.005:
            return Signal(ticker, BUY, "mean_reversion",
                          f"RSI oversold {rsi:.1f}, price {price:.2f} at lower BB {bb_lower:.2f}",
                          price)

        if vwap and price >= vwap:
            return Signal(ticker, SELL, "mean_reversion",
                          f"Price {price:.2f} reached VWAP {vwap:.2f}",
                          price)

        return None

    # ── Strategy 3: TQQQ / SQQQ Rotation ─────────────────────────────────────

    def _etf_rotation(self, ticker: str, v: dict) -> Optional[Signal]:
        """
        TQQQ — only hold when NASDAQ is in a bullish regime (QQQ > EMA-50)
        SQQQ — only hold when NASDAQ is in a bearish regime (QQQ < EMA-50)
        Uses momentum filters for entries.
        """
        bearish = nasdaq_is_bearish(self._feed.get_candles(QQQ_TICKER))
        rsi      = v.get("rsi")
        rsi_prev = self._prev_rsi.get(ticker)
        hist     = v.get("macd_hist")
        price    = v.get("price")

        if price is None:
            return None

        if ticker == "TQQQ":
            if bearish:
                return Signal(ticker, SELL, "etf_rotation",
                              "NASDAQ below EMA-50 — exiting TQQQ", price)
            # Bullish regime: enter on momentum
            if rsi and rsi_prev and rsi_prev < RSI_BUY <= rsi and hist and hist > 0:
                return Signal(ticker, BUY, "etf_rotation",
                              f"Bullish NASDAQ + RSI momentum {rsi:.1f}", price)

        if ticker == "SQQQ":
            if not bearish:
                return Signal(ticker, SELL, "etf_rotation",
                              "NASDAQ above EMA-50 — exiting SQQQ", price)
            if rsi and rsi_prev and rsi_prev < RSI_BUY <= rsi and hist and hist > 0:
                return Signal(ticker, BUY, "etf_rotation",
                              f"Bearish NASDAQ + RSI momentum {rsi:.1f}", price)

        return None
