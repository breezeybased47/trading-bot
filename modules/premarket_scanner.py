"""
premarket_scanner.py  —  Module 7: pre-market gap & news scanner.

WHY
---
The strategies are intraday momentum/mean-reversion — they have NO opinion about
a stock that gapped 6% overnight on an earnings surprise. Trading into that is
how a clean system gets blindsided. So each morning, before the bell, classify
every name into a "do I touch this today?" status:

  earnings within 24h            -> NO-TOUCH (size 0)
  gap > 4% WITH news             -> NO-TOUCH (a real catalyst is moving it)
  gap > 4% no news               -> CAUTION  (big move, unknown cause -> half size)
  gap 2-4%                       -> CAUTION  (half size, demand more confirmation)
  otherwise                      -> NORMAL

FREE / NO-SIGNUP BY DEFAULT
---------------------------
Gap detection uses Alpaca data you already have (prev close vs pre-market price)
and works with zero setup. News + earnings need a (free) Finnhub key in
FINNHUB_API_KEY; if it's absent the scanner simply runs gap-only and says so in
the briefing — it never blocks and never costs anything. A 9:15 briefing is
emitted for the dashboard / Telegram.
"""

import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pytz

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

NO_TOUCH = "NO-TOUCH"
CAUTION = "CAUTION"
NORMAL = "NORMAL"


# ── Pure decision helpers (unit tested) ───────────────────────────────────────

def compute_gap(prev_close: Optional[float], premarket_price: Optional[float]) -> Optional[float]:
    """Overnight gap as a signed fraction, or None if inputs are missing."""
    if not prev_close or prev_close <= 0 or not premarket_price or premarket_price <= 0:
        return None
    return (premarket_price - prev_close) / prev_close


def classify_gap(gap_pct: Optional[float], has_news: bool, has_earnings: bool) -> dict:
    """Map gap + catalysts to a trading status for the day."""
    if has_earnings:
        return {"status": NO_TOUCH, "size_mult": 0.0, "reason": "earnings within 24h"}
    if gap_pct is None:
        return {"status": NORMAL, "size_mult": 1.0, "reason": "no gap data"}

    g = abs(gap_pct)
    pct = gap_pct * 100
    if g > config.GAP_NOTOUCH_PCT:
        if has_news:
            return {"status": NO_TOUCH, "size_mult": 0.0,
                    "reason": "gap %+.1f%% with news (catalyst)" % pct}
        return {"status": CAUTION, "size_mult": 0.5,
                "reason": "gap %+.1f%% >4%% but no news (unknown cause)" % pct}
    if g >= config.GAP_CAUTION_PCT:
        tag = "with news" if has_news else "no news"
        return {"status": CAUTION, "size_mult": 0.5, "reason": "gap %+.1f%% (2-4%%, %s)" % (pct, tag)}
    return {"status": NORMAL, "size_mult": 1.0, "reason": "gap %+.1f%% normal" % pct}


# ── Scanner ───────────────────────────────────────────────────────────────────

class PremarketScanner:
    def __init__(self, hist_client=None, price_fn: Optional[Callable[[str], Optional[float]]] = None,
                 tickers: Optional[List[str]] = None):
        self._hist = hist_client            # StockHistoricalDataClient (REST)
        self._price_fn = price_fn           # e.g. data_feed.get_latest_price
        self._tickers = tickers or list(config.TICKERS)
        self.briefing: Dict[str, dict] = {}
        self.run_date = None
        self.news_available = bool(config.FINNHUB_API_KEY)

    # public accessors used by the entry path -------------------------------

    def status_for(self, ticker: str) -> dict:
        return self.briefing.get(ticker, {"status": NORMAL, "size_mult": 1.0, "reason": "not scanned"})

    def is_no_touch(self, ticker: str) -> bool:
        return self.status_for(ticker)["status"] == NO_TOUCH

    def size_mult(self, ticker: str) -> float:
        return float(self.status_for(ticker).get("size_mult", 1.0))

    # main run --------------------------------------------------------------

    def run(self, now: Optional[datetime] = None,
            notify: Optional[Callable[[str], None]] = None) -> Dict[str, dict]:
        now = now or datetime.now(ET)
        self.news_available = bool(config.FINNHUB_API_KEY)
        briefing = {}
        for t in self._tickers:
            gap = self._fetch_gap(t, now)
            headlines = self._fetch_news(t, now) if self.news_available else []
            has_news = len(headlines) > 0
            has_earnings = self._fetch_earnings(t, now) if self.news_available else False
            decision = classify_gap(gap, has_news, has_earnings)
            briefing[t] = {
                "gap_pct": round(gap, 4) if gap is not None else None,
                "has_news": has_news, "has_earnings": has_earnings,
                "headlines": headlines[:3],
                **decision,
            }
        self.briefing = briefing
        self.run_date = now.date()
        slog.log_event("premarket_scan", date=str(now.date()),
                       no_touch=[t for t, b in briefing.items() if b["status"] == NO_TOUCH],
                       caution=[t for t, b in briefing.items() if b["status"] == CAUTION],
                       news_available=self.news_available)
        text = self.format_briefing()
        logger.info("\n%s", text)
        if notify:
            try:
                notify(text)
            except Exception as exc:
                logger.error("premarket notify failed: %s", exc)
        return briefing

    def format_briefing(self) -> str:
        if not self.briefing:
            return "Pre-market briefing: (not run yet)"
        lines = ["📋 PRE-MARKET BRIEFING — %s" % (self.run_date or "")]
        if not self.news_available:
            lines.append("  (news/earnings unchecked — no Finnhub key; gap-only)")
        order = {NO_TOUCH: 0, CAUTION: 1, NORMAL: 2}
        for t in sorted(self.briefing, key=lambda x: order.get(self.briefing[x]["status"], 9)):
            b = self.briefing[t]
            icon = {NO_TOUCH: "⛔", CAUTION: "⚠️", NORMAL: "✅"}.get(b["status"], "•")
            lines.append("  %s %-5s %-9s %s" % (icon, t, b["status"], b["reason"]))
        return "\n".join(lines)

    # data fetch (defensive — never raises) ---------------------------------

    def _fetch_gap(self, ticker: str, now: datetime) -> Optional[float]:
        prev_close = self._prev_close(ticker, now)
        price = self._price_fn(ticker) if self._price_fn else None
        if price is None:
            price = self._latest_close(ticker, now)
        return compute_gap(prev_close, price)

    def _prev_close(self, ticker: str, now: datetime) -> Optional[float]:
        if self._hist is None:
            return None
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day,
                                   start=now - timedelta(days=7), end=now, feed="iex")
            df = self._hist.get_stock_bars(req).df
            closes = df["close"] if "close" in df else None
            if closes is None or len(closes) == 0:
                return None
            # last fully-closed session (exclude today's forming bar if present)
            return float(closes.iloc[-2]) if len(closes) >= 2 else float(closes.iloc[-1])
        except Exception as exc:
            logger.error("prev_close fetch failed (%s): %s", ticker, exc)
            return None

    def _latest_close(self, ticker: str, now: datetime) -> Optional[float]:
        if self._hist is None:
            return None
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                                   start=now - timedelta(hours=20), end=now, feed="iex")
            df = self._hist.get_stock_bars(req).df
            return float(df["close"].iloc[-1]) if ("close" in df and len(df)) else None
        except Exception as exc:
            logger.error("latest_close fetch failed (%s): %s", ticker, exc)
            return None

    def _fetch_news(self, ticker: str, now: datetime) -> List[str]:
        """Recent headlines via Finnhub (optional, free tier). Returns [] on any issue."""
        try:
            import requests
            frm = (now - timedelta(hours=config.NEWS_LOOKBACK_HOURS)).strftime("%Y-%m-%d")
            to = now.strftime("%Y-%m-%d")
            r = requests.get("https://finnhub.io/api/v1/company-news",
                             params={"symbol": ticker, "from": frm, "to": to,
                                     "token": config.FINNHUB_API_KEY}, timeout=8)
            if r.status_code != 200:
                return []
            cutoff = (now - timedelta(hours=config.NEWS_LOOKBACK_HOURS)).timestamp()
            return [item.get("headline", "") for item in r.json()
                    if item.get("datetime", 0) >= cutoff and item.get("headline")]
        except Exception as exc:
            logger.error("news fetch failed (%s): %s", ticker, exc)
            return []

    def _fetch_earnings(self, ticker: str, now: datetime) -> bool:
        """True if earnings fall within the next 24h (Finnhub calendar, optional)."""
        try:
            import requests
            to = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            frm = now.strftime("%Y-%m-%d")
            r = requests.get("https://finnhub.io/api/v1/calendar/earnings",
                             params={"from": frm, "to": to, "symbol": ticker,
                                     "token": config.FINNHUB_API_KEY}, timeout=8)
            if r.status_code != 200:
                return False
            return len(r.json().get("earningsCalendar", [])) > 0
        except Exception as exc:
            logger.error("earnings fetch failed (%s): %s", ticker, exc)
            return False
