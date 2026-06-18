"""
Tests for Module 5 (adaptive cooldowns + heat). Time is injected so no real
waiting is needed.
Run:  ./venv/bin/python -m unittest tests.test_cooldowns -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="cd_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules import cooldowns as cd                # noqa: E402

ET = pytz.timezone("America/New_York")
T0 = ET.localize(datetime(2026, 6, 17, 10, 0, 0))


class TestClassify(unittest.TestCase):
    def test_clean_win(self):
        self.assertEqual(cd.classify_outcome(50, "Price reached VWAP", 600), "clean_win")

    def test_stop_loss(self):
        self.assertEqual(cd.classify_outcome(-30, "STOP: Hard stop", 600), "stop_loss")

    def test_whipsaw(self):
        self.assertEqual(cd.classify_outcome(-30, "STOP: Hard stop", 60), "whipsaw")

    def test_win_via_trailing_stop_is_neutral(self):
        self.assertEqual(cd.classify_outcome(40, "STOP: Trailing stop", 600), "neutral")


class TestCooldownTiers(unittest.TestCase):
    def setUp(self):
        config.ADAPTIVE_COOLDOWN_ENABLED = True
        self.m = cd.CooldownManager()

    def test_clean_win_7min(self):
        d = self.m.register_close("AAPL", 50, "Price reached VWAP", 600, now=T0)
        self.assertEqual(d["minutes"], 7.0)
        self.assertTrue(self.m.is_blocked("AAPL", now=T0 + timedelta(minutes=5))["blocked"])
        self.assertFalse(self.m.is_blocked("AAPL", now=T0 + timedelta(minutes=8))["blocked"])

    def test_stop_loss_adds_heat(self):
        d = self.m.register_close("AAPL", -30, "STOP: Hard stop", 600, now=T0)
        self.assertEqual(d["minutes"], 35.0)        # 30 base + heat(1)*5
        self.assertEqual(d["heat"], 1.0)

    def test_whipsaw_60min_plus_heat(self):
        d = self.m.register_close("TSLA", -30, "STOP: Hard stop", 60, now=T0)
        self.assertEqual(d["minutes"], 70.0)        # 60 base + heat(2)*5
        self.assertEqual(d["outcome"], "whipsaw")

    def test_consecutive_losses_double(self):
        self.m.register_close("AMD", -10, "STOP: Hard stop", 600, now=T0)
        d2 = self.m.register_close("AMD", -10, "STOP: Hard stop", 600, now=T0 + timedelta(seconds=1))
        self.assertEqual(d2["consec_losses"], 2)
        self.assertGreaterEqual(d2["minutes"], 70.0)   # 30*2 + heat*5

    def test_win_resets_consecutive(self):
        self.m.register_close("NVDA", -10, "STOP: Hard stop", 600, now=T0)
        self.m.register_close("NVDA", 20, "Price reached VWAP", 600, now=T0 + timedelta(minutes=1))
        d3 = self.m.register_close("NVDA", -10, "STOP: Hard stop", 600, now=T0 + timedelta(minutes=2))
        self.assertEqual(d3["consec_losses"], 1)       # streak was reset by the win


class TestHeatDecay(unittest.TestCase):
    def setUp(self):
        config.ADAPTIVE_COOLDOWN_ENABLED = True

    def test_heat_decays_over_time(self):
        m = cd.CooldownManager()
        m.register_close("AAPL", -30, "STOP: Hard stop", 600, now=T0)   # heat -> 1.0
        self.assertAlmostEqual(m.heat("AAPL", now=T0), 1.0, places=2)
        # HEAT_DECAY_PER_HOUR=0.5 -> after 2h heat is gone
        self.assertAlmostEqual(m.heat("AAPL", now=T0 + timedelta(hours=2)), 0.0, places=2)


class TestToggle(unittest.TestCase):
    def test_filter_off_never_blocks(self):
        config.ADAPTIVE_COOLDOWN_ENABLED = False
        m = cd.CooldownManager()
        m.register_close("AAPL", -30, "STOP: Hard stop", 600, now=T0)
        res = m.is_blocked("AAPL", now=T0 + timedelta(minutes=1))
        self.assertFalse(res["blocked"])
        self.assertEqual(res["reason"], "filter_off")

    def test_status_reports_heat_and_cooldown(self):
        config.ADAPTIVE_COOLDOWN_ENABLED = True
        m = cd.CooldownManager()
        m.register_close("AAPL", -30, "STOP: Hard stop", 600, now=T0)
        st = m.status(now=T0 + timedelta(minutes=1))
        self.assertIn("AAPL", st)
        self.assertGreater(st["AAPL"]["cooldown_seconds"], 0)
        self.assertAlmostEqual(st["AAPL"]["heat"], 1.0, places=1)   # ~0.99 after 1 min decay


if __name__ == "__main__":
    unittest.main(verbosity=2)
