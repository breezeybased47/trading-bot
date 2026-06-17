"""
main.py
Entry point — wires all modules together and runs the bot.

Start with:  python main.py
Stop with:   Ctrl+C
"""

import asyncio
import logging
import signal
import sys
import time
from typing import Dict

import pytz
from rich.live import Live

import config
from modules.alerts import daily_limit_hit, stop_hit, trade_closed, trade_opened
from modules.broker import Broker
from modules.dashboard import render
from modules.data_feed import DataFeed
from modules.indicators import compute, latest as ind_latest
from modules.risk_manager import RiskManager
from modules.strategy import BUY, SELL, Signal, StrategyEngine
from modules.trade_logger import init as init_log, record as log_trade
from modules.web_dashboard import start as start_web

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")
ET = pytz.timezone("America/New_York")

# ── Shared state (written by data thread, read by dashboard) ──────────────────
_prices:     Dict[str, float] = {}
_indicators: Dict[str, dict]  = {}


class Bot:
    def __init__(self):
        self.broker   = Broker()
        self.feed     = DataFeed()
        self.strategy = StrategyEngine(self.feed)
        self.risk     = RiskManager(self.broker)
        self.running  = False

    # ── Core bar handler (called on every new 1-min candle) ───────────────────

    def on_bar(self, ticker: str, candles):
        # 1. Update shared price + indicator state
        price = self.feed.get_latest_price(ticker)
        if price:
            _prices[ticker] = price

        df = compute(candles)
        if df is not None:
            _indicators[ticker] = ind_latest(df)

        # 2. Update position stops; execute if triggered
        if price:
            result = self.risk.tick_all(ticker, price)
            if result:
                stop_ticker, reason = result
                self._stop_out(stop_ticker, reason)

        # 3. Force-close leveraged ETFs near EOD
        for etf in self.risk.etfs_to_force_close():
            logger.info(f"Force-close {etf} — approaching {config.LEVERAGED_ETF_CLOSE_TIME}")
            self._close(etf, reason="End-of-day forced close")

        # 4. Cancel orders open too long
        self.broker.cancel_stale()

        # 5. Check daily loss halt
        if self.risk.halted:
            return

        # 6. Ask strategy for a signal
        sig = self.strategy.evaluate(ticker, candles)
        if sig:
            self._handle_signal(sig)

    # ── Signal execution ──────────────────────────────────────────────────────

    def _handle_signal(self, sig: Signal):
        if sig.action == BUY:
            ok, reason = self.risk.approve_entry(sig.ticker)
            if not ok:
                logger.debug(f"BUY blocked ({sig.ticker}): {reason}")
                return
            self._open(sig)

        elif sig.action == SELL:
            if sig.ticker in self.risk.positions:
                self._close(sig.ticker, reason=sig.reason)

    def _open(self, sig: Signal):
        qty = self.risk.size(sig.price)
        oid = self.broker.market_buy(sig.ticker, qty)
        if not oid:
            return

        filled = self.broker.wait_for_fill(oid) or sig.price
        self.risk.record_open(sig.ticker, filled, qty, sig.strategy)
        log_trade(sig.ticker, "BUY", sig.strategy, qty, filled, reason=sig.reason)
        trade_opened(sig.ticker, "BUY", qty, filled, sig.strategy)
        logger.info(f"OPENED {sig.ticker} x{qty} @ ${filled:.2f} [{sig.strategy}]")

    def _close(self, ticker: str, reason: str = ""):
        pos = self.risk.positions.get(ticker)
        if not pos:
            return

        oid = self.broker.market_sell(ticker, pos.qty)
        if not oid:
            return

        exit_p = self.broker.wait_for_fill(oid) or pos.current_price
        pnl    = (exit_p - pos.entry) * pos.qty

        self.risk.record_close(ticker)
        log_trade(ticker, "SELL", pos.strategy, pos.qty, pos.entry, exit_p, reason)
        trade_closed(ticker, pos.entry, exit_p, pnl, reason)
        logger.info(f"CLOSED {ticker} x{pos.qty} @ ${exit_p:.2f}  P&L ${pnl:+.2f}")

    def _stop_out(self, ticker: str, reason: str):
        pos = self.risk.positions.get(ticker)
        if pos:
            stop_hit(ticker, pos.current_price, pos.pnl)
            logger.warning(f"STOP OUT: {ticker} — {reason}")
        self._close(ticker, reason=f"STOP: {reason}")

    # ── Dashboard loop (async) ────────────────────────────────────────────────

    async def _run_dashboard(self):
        with Live(refresh_per_second=1, screen=True) as live:
            while self.running:
                panel = render(
                    positions=self.risk.positions,
                    portfolio=self.broker.portfolio_value(),
                    cash=self.broker.buying_power(),
                    prices=_prices,
                    indicators=_indicators,
                    halted=self.risk.halted,
                )
                live.update(panel)
                await asyncio.sleep(2)

    # ── Entry point ───────────────────────────────────────────────────────────

    async def run(self):
        mode = "PAPER" if config.PAPER_TRADING else "LIVE"
        logger.info("=" * 60)
        logger.info(f"  Trading Bot starting — {mode} MODE")
        logger.info(f"  Universe: {config.TICKERS}")
        logger.info("=" * 60)

        if self.broker.is_blocked():
            logger.error("Alpaca account is blocked — exiting")
            return

        init_log()
        start_web()
        self.feed.register_bar_callback(self.on_bar)
        self.feed.load_history()
        self.running = True

        await asyncio.gather(
            self.feed.start(),
            self._run_dashboard(),
        )

    def stop(self, *_):
        logger.info("Shutting down...")
        self.running = False
        self.feed.stop()
        sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot = Bot()
    signal.signal(signal.SIGINT,  bot.stop)
    signal.signal(signal.SIGTERM, bot.stop)
    asyncio.run(bot.run())
