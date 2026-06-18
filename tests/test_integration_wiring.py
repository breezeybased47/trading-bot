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
from modules import journal                            # noqa: E402
from modules.strategy import Signal, BUY               # noqa: E402

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
