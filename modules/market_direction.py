"""
market_direction.py  —  long-term market-direction gauge from the NASDAQ (QQQ).

WHY
---
Don't fight the tape. TQQQ/SQQQ are just 3x leveraged QQQ, so QQQ itself is the
clean signal for "is the market going up or down" over the long term (no leverage
decay/noise). This classifies QQQ's DAILY trend — price vs its 50-day and 200-day
moving averages — into BULL / BEAR / NEUTRAL, and lets the bot trade smaller when
the long-term trend is down:

  BULL    : price > 50-day SMA AND 50-day > 200-day   (aligned up-trend)
  BEAR    : price < 50-day SMA AND 50-day < 200-day   (aligned down-trend)
  NEUTRAL : anything mixed / transitioning

Daily bars via the REST historical client (no extra websocket). Refreshed once a
day. FAILS SAFE to NEUTRAL (no size change) when data is missing.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import pytz

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

BULL = "BULL"
BEAR = "BEAR"
NEUTRAL = "NEUTRAL"


def classify(closes, fast: int, slow: int) -> Tuple[str, dict]:
    """Pure: classify a daily-close series into BULL/BEAR/NEUTRAL + the levels behind it."""
    if closes is None or len(closes) < slow:
        return NEUTRAL, {}
    s = pd.Series(closes).astype(float)
    price = float(s.iloc[-1])
    sma_fast = float(s.tail(fast).mean())
    sma_slow = float(s.tail(slow).mean())
    if price > sma_fast and sma_fast > sma_slow:
        direction = BULL
    elif price < sma_fast and sma_fast < sma_slow:
        direction = BEAR
    else:
        direction = NEUTRAL
    return direction, {"price": round(price, 2),
                       "sma_fast": round(sma_fast, 2), "sma_slow": round(sma_slow, 2)}


class MarketDirection:
    def __init__(self, hist_client=None):
        self._hist = hist_client
        self._direction = NEUTRAL
        self._detail: dict = {}
        self._last_refresh = None
        self._lock = threading.Lock()

    # ── Refresh (once a day) ──────────────────────────────────────────────────

    def refresh_if_due(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(ET)
        if self._last_refresh == now.date():
            return
        self.refresh(now)

    def refresh(self, now: Optional[datetime] = None) -> bool:
        closes = self._fetch_qqq_daily()
        if closes is None or len(closes) < config.MARKET_SMA_SLOW:
            logger.warning("market-direction refresh skipped — need %d QQQ daily bars, got %s",
                           config.MARKET_SMA_SLOW, 0 if closes is None else len(closes))
            return False
        direction, detail = classify(closes, config.MARKET_SMA_FAST, config.MARKET_SMA_SLOW)
        with self._lock:
            prev = self._direction
            self._direction, self._detail = direction, detail
            self._last_refresh = (now or datetime.now(ET)).date()
        if direction != prev:
            slog.log_event("market_direction", to=direction, frm=prev, **detail)
            logger.info("Market direction: %s -> %s %s", prev, direction, detail)
        return True

    def _fetch_qqq_daily(self):
        if self._hist is None:
            return None
        try:
            end = datetime.now(ET)
            start = end - timedelta(days=config.MARKET_SMA_SLOW * 2 + 60)
            req = StockBarsRequest(symbol_or_symbols=config.QQQ_TICKER, timeframe=TimeFrame.Day,
                                   start=start, end=end, feed="iex")
            df = self._hist.get_stock_bars(req).df
            if df is None or df.empty or "close" not in df:
                return None
            return df["close"].to_numpy(dtype=float)
        except Exception as exc:
            logger.error("QQQ daily fetch failed: %s", exc)
            return None

    # ── Use / display ─────────────────────────────────────────────────────────

    def current(self) -> str:
        return self._direction

    def size_mult(self) -> float:
        """Long size multiplier — defensive (smaller) in a confirmed long-term down-trend."""
        if not config.MARKET_DIRECTION_ENABLED:
            return 1.0
        return float(config.MARKET_BEAR_SIZE_MULT) if self._direction == BEAR else 1.0

    def status(self) -> dict:
        return {"direction": self._direction, "enabled": config.MARKET_DIRECTION_ENABLED,
                "size_mult": self.size_mult(), **self._detail}
