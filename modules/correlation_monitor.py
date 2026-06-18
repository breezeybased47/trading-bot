"""
correlation_monitor.py  —  Module 3: live correlation guard.

WHY
---
Three "different" positions that all rise and fall together are really ONE
position at 3x size — and the daily-loss limit discovers that the hard way. This
guard refuses entries that are too correlated with what's already open, so the
book stays genuinely diversified instead of secretly concentrated.

Two horizons, because correlation is regime-dependent:
  - 30-day  (CORR_LONG_BLOCK 0.70): the structural relationship.
  - 5-day   (CORR_SHORT_BLOCK 0.85): catches "everything moving together" — the
    selloff condition where normally-uncorrelated names suddenly march in lock-step.

Special rule: TQQQ is 3x long-NASDAQ, i.e. concentrated long-tech beta. If TQQQ
is open, adding another long single-name tech is just stacking the same bet, so
(when CORR_TQQQ_TECH_RULE is on) those entries are blocked too.

Data: daily closes via the REST historical client (NOT the websocket — so this is
unaffected by Alpaca's single live-connection limit). Refreshed once each morning
at CORR_REFRESH_HOUR_ET. The guard FAILS OPEN (allows) when data is missing, so a
cold start or a data hiccup never silently halts all trading — it just logs that
it couldn't check. Every block is logged so we can measure how often it fires.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import pytz

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

# Long single-name tech = the universe minus the leveraged ETFs (TQQQ/SQQQ).
LONG_TECH_NAMES = [t for t in config.TICKERS if t not in config.LEVERAGED_ETFS]


class CorrelationMonitor:
    def __init__(self, hist_client=None, tickers: Optional[List[str]] = None):
        self._hist = hist_client                       # reuse DataFeed.hist_client
        self._tickers = tickers or list(config.TICKERS)
        self.corr_long: Optional[pd.DataFrame] = None   # 30-day correlation matrix
        self.corr_short: Optional[pd.DataFrame] = None  # 5-day correlation matrix
        self.last_refresh = None                        # date of last successful build
        self._lock = threading.Lock()

    # ── Refresh (self-throttling to once per morning) ─────────────────────────

    def refresh_if_due(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(ET)
        if self.last_refresh == now.date():
            return
        # Build immediately if we've never built; otherwise wait for the morning hour.
        if self.corr_long is not None and now.hour < config.CORR_REFRESH_HOUR_ET:
            return
        self.refresh(now)

    def refresh(self, now: Optional[datetime] = None) -> bool:
        returns = self._fetch_daily_returns()
        if returns is None or returns.empty:
            logger.warning("correlation refresh skipped — no return data")
            return False
        long_n = config.CORR_LONG_WINDOW_DAYS
        short_n = config.CORR_SHORT_WINDOW_DAYS
        with self._lock:
            self.corr_long = returns.tail(long_n).corr()
            self.corr_short = returns.tail(short_n).corr()
            self.last_refresh = (now or datetime.now(ET)).date()
        slog.log_event("correlation_refresh", days=len(returns),
                       long_window=long_n, short_window=short_n)
        logger.info("Correlation matrices refreshed (%d days of returns)", len(returns))
        return True

    def _fetch_daily_returns(self) -> Optional[pd.DataFrame]:
        if self._hist is None:
            return None
        end = datetime.now(ET)
        start = end - timedelta(days=config.CORR_LONG_WINDOW_DAYS * 2 + 15)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=self._tickers, timeframe=TimeFrame.Day,
                start=start, end=end, feed="iex",
            )
            bars = self._hist.get_stock_bars(req).df
            return self._returns_from_bars(bars)
        except Exception as exc:
            logger.error("correlation daily fetch failed: %s", exc)
            return None

    @staticmethod
    def _returns_from_bars(bars: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Pivot alpaca's (symbol, timestamp) bars to a wide daily-returns frame."""
        if bars is None or bars.empty or "close" not in bars:
            return None
        df = bars[["close"]].reset_index()           # symbol, timestamp, close
        sym_col = "symbol" if "symbol" in df else df.columns[0]
        ts_col = "timestamp" if "timestamp" in df else df.columns[1]
        wide = df.pivot(index=ts_col, columns=sym_col, values="close").sort_index()
        return wide.pct_change().dropna(how="all")

    # ── The guard ─────────────────────────────────────────────────────────────

    def _pair_corr(self, a: str, b: str, matrix: Optional[pd.DataFrame]) -> Optional[float]:
        try:
            if matrix is None or a not in matrix.columns or b not in matrix.index:
                return None
            v = matrix.loc[a, b]
            v = float(v)
            return None if pd.isna(v) else v
        except Exception:
            return None

    def check_entry(self, ticker: str, open_tickers: List[str]) -> dict:
        """
        Decide whether `ticker` may be opened given what's already held.
        Returns {allow, reason, worst_corr_30, worst_corr_5, with_ticker}.
        """
        base = {"worst_corr_30": None, "worst_corr_5": None, "with_ticker": None}

        if not config.CORRELATION_GUARD_ENABLED:
            return {"allow": True, "reason": "filter_off", **base}

        self.refresh_if_due()
        others = [o for o in open_tickers if o != ticker]
        if not others:
            return {"allow": True, "reason": "no_open_positions", **base}

        # Special TQQQ long-tech concentration rule.
        if (config.CORR_TQQQ_TECH_RULE and "TQQQ" in others
                and ticker in LONG_TECH_NAMES):
            reason = "TQQQ open — blocking additional long-tech (%s) to avoid stacked beta" % ticker
            slog.log_block("correlation_tqqq", ticker, reason, open_tickers=others)
            return {"allow": False, "reason": reason, **base}

        worst30 = worst5 = None
        worst30_with = worst5_with = None
        for o in others:
            c30 = self._pair_corr(ticker, o, self.corr_long)
            c5 = self._pair_corr(ticker, o, self.corr_short)
            if c30 is not None and (worst30 is None or c30 > worst30):
                worst30, worst30_with = c30, o
            if c5 is not None and (worst5 is None or c5 > worst5):
                worst5, worst5_with = c5, o

        if worst30 is None and worst5 is None:
            return {"allow": True, "reason": "no_correlation_data", **base}

        snapshot = {"worst_corr_30": _r(worst30), "worst_corr_5": _r(worst5)}

        # 5-day first: it's the regime-shift early warning.
        if worst5 is not None and worst5 > config.CORR_SHORT_BLOCK:
            reason = "5d corr %.2f > %.2f with %s (everything-moving-together)" % (
                worst5, config.CORR_SHORT_BLOCK, worst5_with)
            slog.log_block("correlation_5d", ticker, reason,
                           corr=round(worst5, 3), with_ticker=worst5_with)
            return {"allow": False, "reason": reason, "with_ticker": worst5_with, **snapshot}

        if worst30 is not None and worst30 > config.CORR_LONG_BLOCK:
            reason = "30d corr %.2f > %.2f with %s" % (
                worst30, config.CORR_LONG_BLOCK, worst30_with)
            slog.log_block("correlation_30d", ticker, reason,
                           corr=round(worst30, 3), with_ticker=worst30_with)
            return {"allow": False, "reason": reason, "with_ticker": worst30_with, **snapshot}

        return {"allow": True, "reason": "corr_ok", "with_ticker": worst30_with, **snapshot}

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def heatmap_data(self) -> dict:
        """Serialise both matrices for the dashboard heatmap."""
        def to_dict(m):
            if m is None:
                return {}
            return {a: {b: _r(float(m.loc[a, b])) for b in m.columns} for a in m.index}
        with self._lock:
            return {
                "tickers": self._tickers,
                "long": to_dict(self.corr_long),
                "short": to_dict(self.corr_short),
                "updated": str(self.last_refresh) if self.last_refresh else None,
                "enabled": config.CORRELATION_GUARD_ENABLED,
            }


def _r(v: Optional[float]):
    return round(v, 3) if v is not None else None
