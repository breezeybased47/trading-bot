"""
Tests for Module 8 analysis (journal_report). Trades injected directly — no DB.
Run:  ./venv/bin/python -m unittest tests.test_journal_report -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import journal_report as jr             # noqa: E402


def _trades():
    rows = []
    # CHOPPY @ power_hour: consistent losers (varied so std>0 for a real t-stat)
    for i in range(10):
        rows.append({"regime": "CHOPPY", "time_bucket": "power_hour",
                     "strategy": "momentum", "sizing_model": "fixed",
                     "pnl": -4.0 if i % 2 else -6.0})
    # TRENDING_UP @ morning: consistent winners
    for i in range(10):
        rows.append({"regime": "TRENDING_UP", "time_bucket": "morning",
                     "strategy": "momentum", "sizing_model": "fixed",
                     "pnl": 9.0 if i % 2 else 11.0})
    return rows


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.a = jr.analyze(_trades(), min_sample=8)

    def test_overall(self):
        self.assertEqual(self.a["n"], 20)
        self.assertAlmostEqual(self.a["win_rate"], 0.5)
        self.assertAlmostEqual(self.a["expectancy"], 2.5)

    def test_by_regime(self):
        self.assertAlmostEqual(self.a["by_regime"]["CHOPPY"]["expectancy"], -5.0)
        self.assertAlmostEqual(self.a["by_regime"]["TRENDING_UP"]["expectancy"], 10.0)

    def test_flags_negative_regime(self):
        flagged = [(f["dimension"], f["value"]) for f in self.a["flags"]]
        self.assertIn(("regime", "CHOPPY"), flagged)
        self.assertIn(("time_of_day", "power_hour"), flagged)
        # winning tags are not flagged
        self.assertNotIn(("regime", "TRENDING_UP"), flagged)

    def test_t_stat_strength(self):
        choppy_flag = next(f for f in self.a["flags"]
                           if f["dimension"] == "regime" and f["value"] == "CHOPPY")
        self.assertEqual(choppy_flag["strength"], "strong")   # consistent loss, |t| high

    def test_best_worst_time(self):
        self.assertEqual(self.a["best_time"], "morning")
        self.assertEqual(self.a["worst_time"], "power_hour")

    def test_worst_combos(self):
        combos = [c["combo"] for c in self.a["worst_combos"]]
        self.assertIn("CHOPPY @ power_hour", combos)


class TestTextReport(unittest.TestCase):
    def test_empty(self):
        self.assertIn("no closed trades", jr.text_report({"n": 0}))

    def test_readable(self):
        txt = jr.text_report(jr.analyze(_trades(), min_sample=8))
        self.assertIn("SUGGESTED FILTERS", txt)
        self.assertIn("CHOPPY", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
