"""
paper_engine.py  —  runs independent strategies in PAPER (no real orders).

WHY
---
The shadow engine (Module 9) evaluates config *variants of the champion's own
trades*; it can't test a strategy that takes ENTIRELY DIFFERENT trades, like the
reversal setup. This engine fills that gap: each PaperBook runs a full strategy
on the live bar stream, opens/exits its own simulated positions (honouring the
strategy's own stop/target when it provides them), and tracks P&L — but NEVER
sends an order. That lets a brand-new strategy compete head-to-head with the live
champion on the same live data, at zero risk, before it's ever trusted.
"""

import logging
import statistics
from typing import List, Optional

import config
from modules import structured_log as slog
from modules.strategy import BUY, SELL, SHORT, COVER

logger = logging.getLogger(__name__)


class PaperBook:
    """One strategy's simulated portfolio. Paper only."""

    def __init__(self, name: str, strategy, dollars: float):
        self.name = name
        self.strategy = strategy
        self.dollars = dollars
        self.positions = {}     # ticker -> {entry, qty, stop, target}
        self.pnls: List[float] = []

    def on_bar(self, ticker: str, candles) -> None:
        try:
            price = float(candles["close"].iloc[-1])
        except Exception:
            return
        if price <= 0:
            return

        sig = None
        try:
            sig = self.strategy.evaluate(ticker, candles)
        except Exception as exc:
            logger.error("paper strategy %s eval error (%s): %s", self.name, ticker, exc)

        pos = self.positions.get(ticker)
        if pos is not None:
            reason, pnl = None, None
            if pos["side"] == "long":
                if pos["stop"] is not None and price <= pos["stop"]:
                    reason = "stop"
                elif pos["target"] is not None and price >= pos["target"]:
                    reason = "target"
                elif sig is not None and sig.action == SELL:
                    reason = "signal"
                if reason:
                    pnl = (price - pos["entry"]) * pos["qty"]
            else:  # short — profits when price FALLS; stop is above, target below
                if pos["stop"] is not None and price >= pos["stop"]:
                    reason = "stop"
                elif pos["target"] is not None and price <= pos["target"]:
                    reason = "target"
                elif sig is not None and sig.action in (BUY, COVER):
                    reason = "cover"
                if reason:
                    pnl = (pos["entry"] - price) * pos["qty"]
            if reason:
                self.pnls.append(pnl)
                slog.log_event("paper_trade", strategy=self.name, ticker=ticker, side=pos["side"],
                               pnl=round(pnl, 2), reason=reason, entry=pos["entry"], exit=price)
                del self.positions[ticker]
            return

        if sig is not None and sig.action in (BUY, SHORT):
            qty = max(1, int(self.dollars / price))
            side = "long" if sig.action == BUY else "short"
            self.positions[ticker] = {
                "side": side, "entry": price, "qty": qty,
                "stop": getattr(sig, "stop", None),
                "target": getattr(sig, "target", None),
            }
            slog.log_event("paper_entry", strategy=self.name, ticker=ticker, side=side,
                           price=price, stop=getattr(sig, "stop", None),
                           target=getattr(sig, "target", None))

    def stats(self) -> dict:
        n = len(self.pnls)
        total = sum(self.pnls)
        mean = total / n if n else 0.0
        sd = statistics.stdev(self.pnls) if n >= 2 else 0.0
        wins = sum(1 for p in self.pnls if p > 0)
        return {"n": n, "total": round(total, 2), "expectancy": round(mean, 2),
                "win_rate": round(wins / n, 3) if n else 0.0,
                "sharpe": round(mean / sd, 3) if sd > 0 else None}


class PaperEngine:
    def __init__(self, books: Optional[List[PaperBook]] = None):
        self.books = books or []

    def on_bar(self, ticker: str, candles) -> None:
        if not config.PAPER_ENGINE_ENABLED:
            return
        for b in self.books:
            try:
                b.on_bar(ticker, candles)
            except Exception as exc:
                logger.error("paper book %s error: %s", b.name, exc)

    def comparison(self) -> dict:
        return {b.name: b.stats() for b in self.books}
