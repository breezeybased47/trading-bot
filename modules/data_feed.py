"""
data_feed.py
Connects to Alpaca's websocket for real-time 1-minute bars and quotes.
Also fetches historical candles on startup to warm up indicators.
"""

import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd
import pytz

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, MIN_CANDLES, QQQ_TICKER, TICKERS
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

        # Callbacks fired on each new bar — strategy registers here
        self._bar_callbacks: List[Callable] = []

        # All symbols we stream (trading universe + QQQ for trend)
        self.all_symbols = TICKERS + [QQQ_TICKER]

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
        """Update mid-price from bid/ask tick."""
        sym = quote.symbol
        self.latest_prices[sym] = (quote.bid_price + quote.ask_price) / 2

    # ── Stream lifecycle ──────────────────────────────────────────────────────

    async def start(self):
        """Subscribe to bars + quotes for all symbols and run forever."""
        logger.info(f"Starting live stream: {self.all_symbols}")
        self.stream.subscribe_bars(self._on_bar, *self.all_symbols)
        self.stream.subscribe_quotes(self._on_quote, *self.all_symbols)
        await self.stream._run_forever()

    def stop(self):
        logger.info("Stopping data stream")
        self.stream.stop()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "vwap"])
