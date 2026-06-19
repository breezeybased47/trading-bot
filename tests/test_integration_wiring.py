"""
Integration tests for the live-loop wiring in main.py — specifically that the
entry "gauntlet" is BEHAVIOR-PRESERVING with default config (it must reduce to
exactly risk.approve_entry + risk.size), and that each guard blocks when turned
on. Broker/feed/risk are mocked so no network is touched.
Run:  ./venv/bin/python -m unittest tests.test_integration_wiring -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
_TMP = tempfile.mkdtemp(prefix="wire_")
config.STRUCTURED_LOG_FILE = os.path.join(_TMP, "events.jsonl")
config.JOURNAL_DB_FILE = os.path.join(_TMP, "journal.db")
config.REGIME_PERF_FILE = os.path.join(_TMP, "regime.json")
config.RESEARCH_MODE = False

import main                                            # noqa: E402
from modules import journal, risk_manager              # noqa: E402
from modules.strategy import Signal, BUY, SELL         # noqa: E402
from datetime import datetime, timedelta               # noqa: E402
from types import SimpleNamespace                       # noqa: E402
import pytz                                             # noqa: E402

_ET = pytz.timezone("America/New_York")

journal.init()                                         # create the temp journal table


def _default_toggles_off():
    config.REGIME_FILTER_ENABLED = False
    config.CORRELATION_GUARD_ENABLED = False
    config.LIQUIDITY_GUARD_ENABLED = False
    config.ADAPTIVE_COOLDOWN_ENABLED = False
    config.PREMARKET_SCANNER_ENABLED = False
    config.ML_FILTER_ENABLED = False
    config.ECON_GUARD_ENABLED = False
    config.LEVERAGED_ETF_REGIME_GUARD_ENABLED = False
    config.TRADE_LEVERAGED_ETFS = False
    config.SIZING_MODEL = "fixed"


class TestEntryGauntlet(unittest.TestCase):
    def setUp(self):
        _default_toggles_off()
        self.b = main.Bot()
        self.b.risk = mock.Mock()
        self.b.risk.approve_entry.return_value = (True, "OK")
        self.b.risk.size.return_value = 100
        self.b.risk.positions = {}
        self.b.feed.get_quote = lambda t: None
        self.sig = Signal("AAPL", BUY, "momentum", "test entry", 100.0)

    def test_defaults_preserve_size_and_allow(self):
        decision = self.b._approve_and_size(self.sig)
        self.assertIsNotNone(decision)
        qty, tags = decision
        self.assertEqual(qty, 100)                # identical to risk.size()
        self.assertEqual(tags["sizing_model"], "fixed")

    def test_existing_risk_block_still_blocks(self):
        self.b.risk.approve_entry.return_value = (False, "max positions")
        self.assertIsNone(self.b._approve_and_size(self.sig))

    def test_regime_block(self):
        config.REGIME_FILTER_ENABLED = True
        self.b.regime.entry_decision = lambda t: {
            "allow": False, "reason": "REGIME BLOCKED", "regime": "CHOPPY",
            "size_mult": 0.0, "expectancy": -1.0, "sample": 30}
        self.assertIsNone(self.b._approve_and_size(self.sig))

    def test_cooldown_block(self):
        config.ADAPTIVE_COOLDOWN_ENABLED = True
        self.b.cooldowns.is_blocked = lambda t: {"blocked": True, "seconds": 600, "reason": "hot"}
        self.assertIsNone(self.b._approve_and_size(self.sig))

    def test_correlation_block(self):
        config.CORRELATION_GUARD_ENABLED = True
        self.b.correlation.check_entry = lambda t, opens: {"allow": False, "reason": "30d corr 0.9"}
        self.assertIsNone(self.b._approve_and_size(self.sig))

    def test_ml_veto(self):
        config.ML_FILTER_ENABLED = True
        self.b.ml.should_veto = lambda tags: {"veto": True, "p_win": 0.2, "reason": "low_pwin"}
        self.assertIsNone(self.b._approve_and_size(self.sig))

    def test_regime_half_size_multiplier(self):
        config.REGIME_FILTER_ENABLED = True
        self.b.regime.entry_decision = lambda t: {
            "allow": True, "reason": "half", "regime": "TRENDING_UP",
            "size_mult": 0.5, "expectancy": 0.0, "sample": 5}
        qty, _ = self.b._approve_and_size(self.sig)
        self.assertEqual(qty, 50)                 # 100 * 0.5

    def test_leveraged_etf_entries_blocked(self):
        config.TRADE_LEVERAGED_ETFS = False
        sig = Signal("TQQQ", BUY, "etf_rotation", "no leveraged trading", 50.0)
        self.assertIsNone(self.b._approve_and_size(sig))

    def test_qqq_indicator_not_traded(self):
        sig = Signal(config.QQQ_TICKER, BUY, "momentum", "x", 739.0)
        self.assertIsNone(self.b._approve_and_size(sig))

    def test_profit_target_pct(self):
        config.TARGET_PROFIT_PCT, config.TARGET_PROFIT_DOLLARS = 0.02, 0
        self.assertTrue(self.b._profit_target_hit(SimpleNamespace(pnl=0, pnl_pct=0.025)))
        self.assertFalse(self.b._profit_target_hit(SimpleNamespace(pnl=0, pnl_pct=0.01)))

    def test_profit_target_dollars(self):
        config.TARGET_PROFIT_PCT, config.TARGET_PROFIT_DOLLARS = 0, 100
        self.assertTrue(self.b._profit_target_hit(SimpleNamespace(pnl=150, pnl_pct=0.0)))
        self.assertFalse(self.b._profit_target_hit(SimpleNamespace(pnl=50, pnl_pct=0.0)))
        config.TARGET_PROFIT_PCT, config.TARGET_PROFIT_DOLLARS = 0.02, 0   # restore

    def test_min_hold_ignores_early_sell(self):
        config.MIN_HOLD_MINUTES = 10
        self.b.risk.positions = {"AAPL": SimpleNamespace(opened=datetime.now(_ET), strategy="momentum")}
        self.b._close = mock.Mock()
        self.b._handle_signal(Signal("AAPL", SELL, "momentum", "exit", 100.0))
        self.b._close.assert_not_called()          # held < 10 min -> ignored

    def test_min_hold_allows_late_sell(self):
        config.MIN_HOLD_MINUTES = 10
        self.b.risk.positions = {"AAPL": SimpleNamespace(
            opened=datetime.now(_ET) - timedelta(minutes=20), strategy="momentum")}
        self.b._close = mock.Mock()
        self.b._handle_signal(Signal("AAPL", SELL, "momentum", "exit", 100.0))
        self.b._close.assert_called_once()         # matching strategy, held > 10 min -> exits

    def test_mean_rev_exit_does_not_dump_momentum(self):
        # THE churn cure: a mean-reversion "price back at VWAP" SELL must NOT close
        # a momentum position (even past the min-hold window). It rides instead.
        config.MIN_HOLD_MINUTES = 10
        self.b.risk.positions = {"AAPL": SimpleNamespace(
            opened=datetime.now(_ET) - timedelta(minutes=20), strategy="momentum")}
        self.b._close = mock.Mock()
        self.b._handle_signal(Signal("AAPL", SELL, "mean_reversion", "Price reached VWAP", 100.0))
        self.b._close.assert_not_called()          # strategy mismatch -> let it ride


class TestEodFlatten(unittest.TestCase):
    """Day-trading discipline: flatten EVERY position before the close (no overnight holds)."""

    def setUp(self):
        self.rm = risk_manager.RiskManager(mock.Mock())
        self.rm.positions = {"AAPL": object(), "MSFT": object()}
        self._orig = (risk_manager.EOD_FLATTEN_ENABLED, risk_manager.EOD_FLATTEN_TIME)

    def tearDown(self):
        risk_manager.EOD_FLATTEN_ENABLED, risk_manager.EOD_FLATTEN_TIME = self._orig

    def test_flatten_all_when_past_cutoff(self):
        risk_manager.EOD_FLATTEN_ENABLED, risk_manager.EOD_FLATTEN_TIME = True, "00:00"
        self.assertEqual(set(self.rm.positions_to_flatten()), {"AAPL", "MSFT"})

    def test_no_flatten_before_cutoff(self):
        risk_manager.EOD_FLATTEN_ENABLED, risk_manager.EOD_FLATTEN_TIME = True, "23:59"
        self.assertEqual(self.rm.positions_to_flatten(), [])

    def test_disabled_never_flattens(self):
        risk_manager.EOD_FLATTEN_ENABLED, risk_manager.EOD_FLATTEN_TIME = False, "00:00"
        self.assertEqual(self.rm.positions_to_flatten(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
