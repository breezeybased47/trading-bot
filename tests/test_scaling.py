"""
Tests for Module 6 (scaling out). Run:
  ./venv/bin/python -m unittest tests.test_scaling -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="scale_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules import scaling                          # noqa: E402


class TestDecide(unittest.TestCase):
    def setUp(self):
        config.SCALING_ENABLED = True

    def test_disabled(self):
        config.SCALING_ENABLED = False
        self.assertEqual(scaling.decide(100, 105, 100, 100, set())["action"], "none")

    def test_below_tier1_does_nothing(self):
        self.assertEqual(scaling.decide(100, 101.0, 100, 100, set())["action"], "none")  # +1%

    def test_tier1_sells_half_and_breakeven(self):
        a = scaling.decide(100, 101.5, 100, 100, set())   # +1.5%
        self.assertEqual(a["tier"], "tier1")
        self.assertEqual(a["sell_qty"], 50)
        self.assertEqual(a["new_stop"], 100.0)
        self.assertEqual(a["stop_type"], "breakeven")

    def test_gap_to_tier2_still_does_tier1_first(self):
        a = scaling.decide(100, 103.0, 100, 100, set())   # +3% but no tiers hit yet
        self.assertEqual(a["tier"], "tier1")              # bank the 50% first

    def test_tier2_after_tier1(self):
        a = scaling.decide(100, 103.0, 100, 50, {"tier1"})  # tier1 already done, 50 left
        self.assertEqual(a["tier"], "tier2")
        self.assertEqual(a["sell_qty"], 25)               # 25% of original
        self.assertEqual(a["stop_type"], "tight_trail")
        self.assertLess(a["new_stop"], 103.0)

    def test_no_retrigger_when_both_hit(self):
        self.assertEqual(scaling.decide(100, 105, 100, 25, {"tier1", "tier2"})["action"], "none")

    def test_tiny_position_cannot_scale(self):
        a = scaling.decide(100, 101.5, 1, 1, set())       # round(0.5) -> 0
        self.assertEqual(a["action"], "none")
        self.assertEqual(a["reason"], "tier1_qty<1")


class TestScalingManager(unittest.TestCase):
    def setUp(self):
        config.SCALING_ENABLED = True
        self.m = scaling.ScalingManager()

    def test_full_lifecycle(self):
        self.m.on_open("AAPL", 100)
        a1 = self.m.check("AAPL", 100, 101.5, 100)        # tier1
        self.assertEqual(a1["tier"], "tier1")
        again = self.m.check("AAPL", 100, 101.6, 50)      # tier1 already fired
        self.assertEqual(again["action"], "none")
        a2 = self.m.check("AAPL", 100, 103.0, 50)         # tier2
        self.assertEqual(a2["tier"], "tier2")
        self.assertEqual(self.m.tiers_hit("AAPL"), {"tier1", "tier2"})
        self.m.on_close("AAPL")
        self.assertEqual(self.m.check("AAPL", 100, 105, 25)["reason"], "untracked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
