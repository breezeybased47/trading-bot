"""
main.py
Entry point — wires all modules together and runs the bot.

Start with:  python main.py
Stop with:   Ctrl+C  (or create a file named KILL_SWITCH to flatten + halt)

Research modules are integrated at the decision points below but every powerful
guard is gated by its config toggle (all default OFF), so with default config the
bot behaves exactly as before. See docs/MODULES.md.
"""

import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

import pytz
from rich.live import Live

import config
from modules import journal
from modules import liquidity_guard
from modules import structured_log as slog
from modules.alerts import daily_limit_hit, stop_hit, trade_closed, trade_opened
from modules.broker import Broker
from modules.cooldowns import CooldownManager
from modules.correlation_monitor import CorrelationMonitor
from modules import dashboard_state
from modules.dashboard import render
from modules.data_feed import DataFeed
from modules.economic_calendar import EconomicCalendar
from modules import leverage_guard
from modules.indicators import compute, latest as ind_latest
from modules.latency_monitor import LatencyMonitor
from modules.liquidity_guard import LiquidityGuard
from modules.market_direction import MarketDirection
from modules.ml_filter import MLFilter
from modules.position_sizer import PositionSizer
from modules.premarket_scanner import PremarketScanner
from modules.reversal_strategy import ReversalStrategy
from modules.overbought_reversal import OverboughtReversalStrategy
from modules.confirmation import ConfirmationOverlay
from modules.paper_engine import PaperEngine, PaperBook
from modules.regime_filter import RegimeFilter
from modules.risk_manager import RiskManager
from modules.scaling import ScalingManager
from modules.shadow_engine import ShadowEngine
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
        # Live champion — optionally tightened with 'wait for confirmation' on entries
        # (course lesson; champ_confirmed cut the per-trade loss in the first day's data).
        _champion     = StrategyEngine(self.feed)
        self.strategy = ConfirmationOverlay(_champion) if config.CHAMPION_CONFIRMATION else _champion
        self.risk     = RiskManager(self.broker)
        self.running  = False

        # ── Research modules (all gated by their config toggles) ──────────────
        self.regime      = RegimeFilter(self.feed)
        self.sizer       = PositionSizer(self.broker, self.feed)
        self.correlation = CorrelationMonitor(self.feed.hist_client)
        self.market      = MarketDirection(self.feed.hist_client)   # long-term NASDAQ direction
        self.liquidity   = LiquidityGuard(self.feed)
        self.cooldowns   = CooldownManager()
        self.scaler      = ScalingManager()
        self.premarket   = PremarketScanner(self.feed.hist_client, self.feed.get_latest_price)
        self.econ        = EconomicCalendar()   # course module: FOMC/CPI/jobs blackout
        self.shadow      = ShadowEngine()
        self.latency     = LatencyMonitor()
        self.ml          = MLFilter()
        # Course entry setups run as PAPER challengers (no real orders), A/B vs champion
        self.paper       = PaperEngine([
            PaperBook("reversal", ReversalStrategy(), config.PAPER_POSITION_DOLLARS),
            PaperBook("champ_confirmed", ConfirmationOverlay(StrategyEngine(self.feed)),
                      config.PAPER_POSITION_DOLLARS),
            PaperBook("overbought_short", OverboughtReversalStrategy(), config.PAPER_POSITION_DOLLARS),
        ])
        self._shadow_on  = config.SHADOW_ENABLED or config.RESEARCH_MODE

        # Per-position bookkeeping for the research layer
        self._trade_ids:      Dict[str, int]   = {}   # ticker -> journal trade id
        self._mfe:            Dict[str, float] = {}   # ticker -> max favorable excursion
        self._entry_signals:  Dict[str, dict]  = {}   # ticker -> entry-time indicator dict
        self._killed          = False
        self._premarket_day   = None

    # ── Core bar handler (called on every new 1-min candle) ───────────────────

    def on_bar(self, ticker: str, candles):
        # 0. Global kill switch (file-based) — flatten + halt instantly
        self._check_kill_switch()
        if self._killed:
            return

        # 1. Update shared price + indicator state
        price = self.feed.get_latest_price(ticker)
        if price:
            _prices[ticker] = price

        df = compute(candles)
        if df is not None:
            _indicators[ticker] = ind_latest(df)

        # Re-classify the market on its own 5-minute cadence (cheap, self-throttled)
        self.regime.reclassify_if_due()
        self.market.refresh_if_due()        # long-term NASDAQ direction (daily, self-throttled)

        # Paper challengers — independent strategies, NO real orders (course A/B test)
        self.paper.on_bar(ticker, candles)

        # Daily pre-market scan (only when enabled)
        if config.PREMARKET_SCANNER_ENABLED:
            self._maybe_run_premarket()

        # 2. Track excursion + update position stops; execute if triggered
        if price and ticker in self.risk.positions:
            pos = self.risk.positions[ticker]
            self._mfe[ticker] = max(self._mfe.get(ticker, 0.0), pos.pnl_pct)
            journal.update_excursion(self._trade_ids.get(ticker), pos.pnl_pct)

            result = self.risk.tick_all(ticker, price)
            if result:
                stop_ticker, reason = result
                self._stop_out(stop_ticker, reason)
            # 2b. Profit target — let it ride, then bank the win at the target
            elif self._profit_target_hit(pos):
                self._close(ticker, reason="profit target +%.2f%%" % (pos.pnl_pct * 100))
            # 2c. Partial profit taking / scaling out (only when enabled, still open)
            elif config.SCALING_ENABLED and ticker in self.risk.positions:
                action = self.scaler.check(ticker, pos.entry, price, pos.qty)
                if action["action"] == "sell_partial":
                    self._scale_out(ticker, action)

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
            decision = self._approve_and_size(sig)
            if decision is None:
                return
            qty, tags = decision
            self._open(sig, qty, tags)

        elif sig.action == SELL:
            pos = self.risk.positions.get(sig.ticker)
            if pos:
                held_min = (datetime.now(ET) - pos.opened).total_seconds() / 60.0
                if config.MIN_HOLD_MINUTES and held_min < config.MIN_HOLD_MINUTES:
                    logger.debug("Holding %s (%.1f<%d min) — ignoring early SELL signal",
                                 sig.ticker, held_min, config.MIN_HOLD_MINUTES)
                    return
                self._close(sig.ticker, reason=sig.reason)

    def _approve_and_size(self, sig: Signal) -> Optional[Tuple[int, dict]]:
        """
        Run every entry guard in turn. Each is gated by its own config toggle, so
        with default config this is exactly: risk.approve_entry + risk.size.
        Returns (qty, tags) to open, or None if blocked.
        """
        ticker, price = sig.ticker, sig.price

        # Existing hard rails first (hours, max positions, daily loss, etc.)
        ok, reason = self.risk.approve_entry(ticker)
        if not ok:
            logger.debug("BUY blocked (%s): %s", ticker, reason)
            return None

        # Index / leverage proxies (QQQ, TQQQ, SQQQ) are direction INDICATORS, not positions
        if ticker == config.QQQ_TICKER or (not config.TRADE_LEVERAGED_ETFS and ticker in config.LEVERAGED_ETFS):
            logger.info("BUY blocked (%s): indicator, not traded", ticker)
            return None

        # Adaptive cooldown / heat
        cd = self.cooldowns.is_blocked(ticker)
        if cd["blocked"]:
            slog.log_block("cooldown", ticker, cd["reason"], seconds=cd["seconds"])
            logger.info("BUY blocked (%s): cooldown %ss", ticker, cd["seconds"])
            return None

        # Pre-market NO-TOUCH / CAUTION
        pm_mult = 1.0
        if config.PREMARKET_SCANNER_ENABLED:
            if self.premarket.is_no_touch(ticker):
                st = self.premarket.status_for(ticker)
                slog.log_block("premarket", ticker, st["reason"])
                logger.info("BUY blocked (%s): %s", ticker, st["reason"])
                return None
            pm_mult = self.premarket.size_mult(ticker)

        # Economic event guard (FOMC / CPI / jobs) — course module
        econ = self.econ.check_entry(ticker)
        if not econ["allow"]:
            logger.info("BUY blocked (%s): %s", ticker, econ["reason"])
            return None

        # Regime master switch
        rd = self.regime.entry_decision(ticker)
        if not rd["allow"]:
            logger.info("BUY blocked (%s): %s", ticker, rd["reason"])
            return None

        # Leveraged-ETF path-dependency guard — course module
        lev = leverage_guard.check_entry(ticker, rd["regime"])
        if not lev["allow"]:
            logger.info("BUY blocked (%s): %s", ticker, lev["reason"])
            return None

        # Correlation guard
        cr = self.correlation.check_entry(ticker, list(self.risk.positions.keys()))
        if not cr["allow"]:
            logger.info("BUY blocked (%s): %s", ticker, cr["reason"])
            return None

        # Build entry tags (used by the ML veto and the journal)
        sigvals = _indicators.get(ticker, {})
        quote = self.feed.get_quote(ticker) if hasattr(self.feed, "get_quote") else None
        spread = liquidity_guard.spread_bps(quote.get("bid"), quote.get("ask")) if quote else None
        tags = {"ticker": ticker, "regime": rd["regime"], "time_bucket": journal.time_bucket(),
                "signals": sigvals, "spread_bps": spread, "strategy": sig.strategy}

        # ML veto (veto-only; fails open)
        ml_decision = self.ml.should_veto(tags)
        if ml_decision["veto"]:
            logger.info("BUY vetoed by ML (%s): p_win=%s", ticker, ml_decision["p_win"])
            return None

        # Sizing — default 'fixed' uses the existing risk.size (behavior-preserving);
        # vol_adjusted / kelly opt into the position_sizer (and its 25% total cap).
        size_mult = rd["size_mult"] * pm_mult * econ["size_mult"] * self.market.size_mult()
        if config.SIZING_MODEL == "fixed":
            qty = max(0, int(self.risk.size(price) * size_mult))
            model_used, size_reason = "fixed", "fixed 10%% x mult %.2f" % size_mult
        else:
            res = self.sizer.size(ticker, price, regime_size_mult=size_mult)
            qty, model_used, size_reason = res["qty"], res["model_used"], res["reasoning"]
        if qty < 1:
            logger.info("BUY skipped (%s): size 0 (%s)", ticker, size_reason)
            return None

        # Liquidity / spread guard (may reduce or reject)
        if config.LIQUIDITY_GUARD_ENABLED:
            liq = self.liquidity.check_entry(ticker, qty)
            if not liq["allow"]:
                logger.info("BUY blocked (%s): %s", ticker, liq["reason"])
                return None
            qty = liq["adjusted_shares"]
            if qty < 1:
                return None

        tags.update({"sizing_model": model_used, "size_chosen": qty, "size_reasoning": size_reason})
        return qty, tags

    def _open(self, sig: Signal, qty: int, tags: dict):
        signal_ts = sig.ts.timestamp()
        submit_ts = time.time()
        oid = self.broker.market_buy(sig.ticker, qty)
        if not oid:
            return

        filled = self.broker.wait_for_fill(oid) or sig.price
        fill_ts = time.time()
        self.risk.record_open(sig.ticker, filled, qty, sig.strategy)

        # Research bookkeeping
        if config.LATENCY_MONITOR_ENABLED:
            self.latency.record(signal_ts, submit_ts, None, fill_ts, ticker=sig.ticker)
        self._mfe[sig.ticker] = 0.0
        self._entry_signals[sig.ticker] = tags.get("signals") or {}
        self._trade_ids[sig.ticker] = journal.record_entry(
            sig.ticker, sig.strategy, filled, qty,
            regime=tags.get("regime"), signals=tags.get("signals"),
            spread_bps=tags.get("spread_bps"), sizing_model=tags.get("sizing_model"),
            size_chosen=qty, size_reasoning=tags.get("size_reasoning"))
        self.scaler.on_open(sig.ticker, qty)
        if config.LIQUIDITY_GUARD_ENABLED:
            self.liquidity.record_fill(sig.ticker, "BUY", sig.price, filled,
                                       self.liquidity.predicted_slippage_bps(sig.ticker))

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
        pnl     = (exit_p - pos.entry) * pos.qty
        pnl_pct = (exit_p - pos.entry) / pos.entry if pos.entry else 0.0
        hold_s  = (datetime.now(ET) - pos.opened).total_seconds()

        self.risk.record_close(ticker)

        # Research bookkeeping (all safe / cheap; cooldown only *enforces* when enabled)
        tid = self._trade_ids.pop(ticker, None)
        if tid is not None:
            journal.record_exit(tid, exit_p, exit_reason=reason)
        self.cooldowns.register_close(ticker, pnl, reason, hold_s)
        self.scaler.on_close(ticker)
        if self._shadow_on:
            self.shadow.submit({
                "ticker": ticker, "pnl": pnl, "pnl_pct": pnl_pct,
                "entry_price": pos.entry, "qty": pos.qty,
                "max_favorable_pct": self._mfe.pop(ticker, pnl_pct),
                "signals": self._entry_signals.pop(ticker, {}), "regime_blocked": False})
        self._mfe.pop(ticker, None)
        self._entry_signals.pop(ticker, None)
        self.regime.update_performance_snapshot()
        if config.LIQUIDITY_GUARD_ENABLED:
            self.liquidity.record_fill(ticker, "SELL", pos.current_price, exit_p)

        log_trade(ticker, "SELL", pos.strategy, pos.qty, pos.entry, exit_p, reason)
        trade_closed(ticker, pos.entry, exit_p, pnl, reason)
        logger.info(f"CLOSED {ticker} x{pos.qty} @ ${exit_p:.2f}  P&L ${pnl:+.2f}")

    def _scale_out(self, ticker: str, action: dict):
        """Execute a partial sell and adjust the stop (Module 6, only when enabled)."""
        pos = self.risk.positions.get(ticker)
        if not pos:
            return
        sell_qty = min(action["sell_qty"], pos.qty)
        if sell_qty < 1:
            return
        oid = self.broker.market_sell(ticker, sell_qty)
        if not oid:
            return
        fill = self.broker.wait_for_fill(oid) or pos.current_price
        pos.qty -= sell_qty
        pos.hard_stop = action["new_stop"]                       # breakeven or tightened
        if action["stop_type"] == "tight_trail":
            pos.trailing_active = True
            pos.trailing_stop = action["new_stop"]
        log_trade(ticker, "SELL", pos.strategy, sell_qty, pos.entry, fill,
                  reason="PARTIAL %s" % action["tier"])
        logger.info("PARTIAL EXIT %s x%d @ $%.2f (%s, stop->%.2f)",
                    ticker, sell_qty, fill, action["tier"], action["new_stop"])
        if pos.qty <= 0:                                         # fully scaled out
            self.risk.record_close(ticker)
            self._trade_ids.pop(ticker, None)
            self.scaler.on_close(ticker)

    def _stop_out(self, ticker: str, reason: str):
        pos = self.risk.positions.get(ticker)
        if pos:
            stop_hit(ticker, pos.current_price, pos.pnl)
            logger.warning(f"STOP OUT: {ticker} — {reason}")
        self._close(ticker, reason=f"STOP: {reason}")

    # ── Kill switch & pre-market trigger ──────────────────────────────────────

    def _profit_target_hit(self, pos) -> bool:
        """True when an open position has reached the take-profit target ($ or %)."""
        if config.TARGET_PROFIT_DOLLARS and pos.pnl >= config.TARGET_PROFIT_DOLLARS:
            return True
        if config.TARGET_PROFIT_PCT and pos.pnl_pct >= config.TARGET_PROFIT_PCT:
            return True
        return False

    def _check_kill_switch(self):
        if self._killed or not os.path.exists(config.KILL_SWITCH_FILE):
            return
        self._killed = True
        self.risk.halted = True
        logger.critical("KILL SWITCH (%s) — flattening all positions and halting",
                        config.KILL_SWITCH_FILE)
        slog.log_event("kill_switch", action="flatten_all")
        self.broker.close_all()

    def _maybe_run_premarket(self):
        now = datetime.now(ET)
        if self._premarket_day == now.date():
            return
        if now.strftime("%H:%M") >= config.PREMARKET_RUN_TIME:
            self._premarket_day = now.date()
            try:
                self.premarket.run(now)
            except Exception as exc:
                logger.error("pre-market scan failed: %s", exc)

    # ── Dashboard loop (async) ────────────────────────────────────────────────

    def _research_snapshot(self) -> dict:
        """Gather live research-module state for the web dashboard bridge."""
        challengers = self.shadow.comparison()
        challengers.update(self.paper.comparison())   # reversal + champ_confirmed paper books
        return {
            "regime": self.regime.status(),
            "market": self.market.status(),
            "cooldowns": self.cooldowns.status(),
            "correlation": self.correlation.heatmap_data(),
            "shadow": {"comparison": self.shadow.comparison(),
                       "recommendations": self.shadow.recommendation()},
            "latency": {**self.latency.percentiles(),
                        "degraded": self.latency.is_degraded(),
                        "exit_mult": self.latency.exit_threshold_multiplier()},
            "ml": {"calibration": self.ml.calibration(),
                   "enabled": config.ML_FILTER_ENABLED},
            "slippage": liquidity_guard.slippage_summary(),
            "premarket": self.premarket.briefing,
            "econ": self.econ.dashboard(),
            "degraded_feed": getattr(self.feed, "degraded", False),
            "toggles": {k: getattr(config, k) for k in (
                "REGIME_FILTER_ENABLED", "CORRELATION_GUARD_ENABLED",
                "LIQUIDITY_GUARD_ENABLED", "ADAPTIVE_COOLDOWN_ENABLED",
                "SCALING_ENABLED", "PREMARKET_SCANNER_ENABLED",
                "ML_FILTER_ENABLED", "SHADOW_ENABLED",
                "ECON_GUARD_ENABLED", "LEVERAGED_ETF_REGIME_GUARD_ENABLED",
                "SIZING_MODEL")},
        }

    async def _run_dashboard(self):
        with Live(refresh_per_second=1, screen=True) as live:
            while self.running:
                try:
                    dashboard_state.update(**self._research_snapshot())
                except Exception as exc:
                    logger.debug("dashboard snapshot failed: %s", exc)
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
        logger.info(f"  Research mode: {config.RESEARCH_MODE}  |  Sizing: {config.SIZING_MODEL}")
        logger.info("=" * 60)

        if self.broker.is_blocked():
            logger.error("Alpaca account is blocked — exiting")
            return

        init_log()
        slog.init()
        journal.init()
        synced = self.risk.sync_from_broker()   # adopt existing account positions so the cap holds
        if synced:
            logger.info("Reconciled %d existing account position(s) — cap now accounts for them", synced)
        self.regime.update_performance_snapshot()
        self.market.refresh()               # initial long-term direction read at startup
        if self._shadow_on:
            self.shadow.start()
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
        self.shadow.stop()
        self.feed.stop()
        sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick bleed-stop: trim any runaway launchd-redirected log and de-spam logging
    # before anything starts writing (see modules/log_setup.py and data_feed watchdog).
    from modules.log_setup import configure_logging, trim_oversized
    trim_oversized("logs/bot_error.log", max_mb=5)
    trim_oversized("logs/bot.log", max_mb=5)
    configure_logging()

    bot = Bot()
    signal.signal(signal.SIGINT,  bot.stop)
    signal.signal(signal.SIGTERM, bot.stop)
    asyncio.run(bot.run())
