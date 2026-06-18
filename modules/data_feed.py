"""
data_feed.py
Connects to Alpaca's websocket for real-time 1-minute bars and quotes.
Also fetches historical candles on startup to warm up indicators.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd
import pytz

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, DATA_CONNECT_TIMEOUT,
    MIN_CANDLES, QQQ_TICKER, TICKERS,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


class DataFeed:
    """
    Manages all market data:
    - Historical OHLCV bars (for indicator warmup)
    - Live 1-minute bar stream via websocket
    - Latest quote prices
    """

    def __init__(self):
        self.stream = StockDataStream(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        self.hist_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

        # Rolling candle store: {ticker: DataFrame}
        self.candles: Dict[str, pd.DataFrame] = {}

        # Most recent price per ticker
        self.latest_prices: Dict[str, float] = {}

        # Most recent top-of-book quote per ticker (for the liquidity guard):
        # {bid, ask, bid_size, ask_size}. IEX is top-of-book only — no L2 depth.
        self.latest_quotes: Dict[str, dict] = {}

        # Callbacks fired on each new bar — strategy registers here
        self._bar_callbacks: List[Callable] = []

        # All symbols we stream (trading universe + QQQ for trend)
        self.all_symbols = TICKERS + [QQQ_TICKER]

        # Set True if the live stream can't be established (e.g. another instance
        # holds Alpaca's single allowed data connection). When degraded, the bot
        # keeps running without a live feed instead of tight-looping on reconnect.
        self.degraded = False

    # ── Public API ────────────────────────────────────────────────────────────

    def register_bar_callback(self, fn: Callable):
        """Register a function to call whenever a new bar closes for any ticker."""
        self._bar_callbacks.append(fn)

    def load_history(self):
        """
        Fetch the last 5 trading days of 1-minute bars for all symbols.
        Needed to calculate EMA-50 and other slow-moving indicators at startup.
        """
        logger.info("Loading historical data for warmup...")
        end   = datetime.now(ET)
        start = end - timedelta(days=5)

        req = StockBarsRequest(
            symbol_or_symbols=self.all_symbols,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed="iex",          # Free IEX feed; upgrade to "sip" for full tape
        )

        try:
            bars = self.hist_client.get_stock_bars(req).df
            for symbol in self.all_symbols:
                if symbol in bars.index.get_level_values(0):
                    df = bars.loc[symbol].copy()
                    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
                    self.candles[symbol] = df.tail(MIN_CANDLES * 4)
                    self.latest_prices[symbol] = float(df["close"].iloc[-1])
                    logger.info(f"  {symbol}: {len(self.candles[symbol])} bars loaded")
                else:
                    logger.warning(f"  {symbol}: no historical data returned")
                    self.candles[symbol] = self._empty_df()
        except Exception as exc:
            logger.error(f"History load failed: {exc}")

    def get_candles(self, symbol: str) -> pd.DataFrame:
        return self.candles.get(symbol, self._empty_df())

    def get_latest_price(self, symbol: str) -> Optional[float]:
        return self.latest_prices.get(symbol)

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Latest top-of-book quote {bid, ask, bid_size, ask_size} or None."""
        return self.latest_quotes.get(symbol)

    # ── Websocket handlers ────────────────────────────────────────────────────

    async def _on_bar(self, bar):
        """Fired by Alpaca when a 1-minute bar closes."""
        sym = bar.symbol
        ts  = pd.Timestamp(bar.timestamp).tz_convert(ET)

        new_row = pd.DataFrame([{
            "open":   bar.open,
            "high":   bar.high,
            "low":    bar.low,
            "close":  bar.close,
            "volume": bar.volume,
            "vwap":   getattr(bar, "vwap", bar.close),
        }], index=pd.DatetimeIndex([ts]))

        # Append and keep a rolling 500-bar window
        if sym not in self.candles:
            self.candles[sym] = new_row
        else:
            self.candles[sym] = pd.concat([self.candles[sym], new_row]).tail(500)

        self.latest_prices[sym] = float(bar.close)

        for cb in self._bar_callbacks:
            try:
                cb(sym, self.candles[sym])
            except Exception as exc:
                logger.error(f"Bar callback error ({sym}): {exc}")

    async def _on_quote(self, quote):
        """Update mid-price and store the full top-of-book quote."""
        sym = quote.symbol
        bid = getattr(quote, "bid_price", None)
        ask = getattr(quote, "ask_price", None)
        if bid and ask and bid > 0 and ask > 0:
            self.latest_prices[sym] = (bid + ask) / 2
        self.latest_quotes[sym] = {
            "bid": bid, "ask": ask,
            "bid_size": getattr(quote, "bid_size", None),
            "ask_size": getattr(quote, "ask_size", None),
        }

    # ── Stream lifecycle ──────────────────────────────────────────────────────

    async def start(self):
        """Subscribe to bars + quotes, then run the supervised stream loop."""
        logger.info(f"Starting live stream: {self.all_symbols}")
        self.stream.subscribe_bars(self._on_bar, *self.all_symbols)
        self.stream.subscribe_quotes(self._on_quote, *self.all_symbols)
        run_task, self.degraded = await _supervise_stream(
            self.stream, DATA_CONNECT_TIMEOUT, self._on_degrade
        )
        await run_task

    def _on_degrade(self):
        self.degraded = True
        logger.error(
            "DATA FEED DEGRADED: could not establish the Alpaca stream within %ss. "
            "This is almost always 'connection limit exceeded' — another instance "
            "(likely the Oracle VM) holds Alpaca's single free data connection. "
            "This instance will run WITHOUT a live feed; run only ONE streaming "
            "instance to fix.", DATA_CONNECT_TIMEOUT)

    def stop(self):
        logger.info("Stopping data stream")
        self.stream.stop()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "vwap"])


# ── Stream supervision (quick bleed-stop for the connection-limit tight loop) ──

async def _supervise_stream(stream, connect_timeout: float, on_degrade):
    """
    Run Alpaca's stream loop, but guard the 'connection limit exceeded' tight
    loop. alpaca-py's _run_forever() retries auth with ZERO backoff and only
    exits cleanly for 'insufficient subscription' errors — any other auth
    failure (notably the single-connection limit) loops forever, spamming the
    error log. If the stream never establishes within `connect_timeout`, we set
    its private `_should_run` flag False (which breaks the loop on its next
    iteration) and invoke `on_degrade`, so the bot degrades to no-feed instead
    of burning CPU/disk.

    Returns (run_task, degraded). The caller awaits run_task: it streams forever
    when healthy, or returns promptly once we've signalled a degraded stop.
    """
    run_task = asyncio.ensure_future(stream._run_forever())
    deadline = time.time() + connect_timeout
    poll = min(1.0, max(0.01, connect_timeout / 5.0))
    degraded = False
    while not run_task.done():
        if getattr(stream, "_running", False):
            break  # connected — let it run for the long haul
        if time.time() > deadline:
            degraded = True
            try:
                stream._should_run = False  # breaks _run_forever on its next iteration
            except Exception:
                pass
            on_degrade()
            break
        await asyncio.sleep(poll)
    return run_task, degraded
