"""
Tests for Module 9 (shadow champion/challenger). Run:
  ./venv/bin/python -m unittest tests.test_shadow_engine -v
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="shadow_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules.shadow_engine import ShadowEngine        # noqa: E402


def _trade(pnl, pnl_pct, mfe, signals=None, regime_blocked=False):
    return {"ticker": "AAPL", "pnl": pnl, "pnl_pct": pnl_pct, "entry_price": 100.0,
            "qty": 100, "max_favorable_pct": mfe, "signals": signals or {},
            "regime_blocked": regime_blocked}


class TestChallengerEntry(unittest.TestCase):
    def test_rsi60_takes_only_qualifying_cross(self):
        e = ShadowEngine([{"name": "rsi60", "RSI_BUY": 60}])
        took = e._challenger_decision(e._challengers[0],
                                      _trade(50, 0.01, 0.02, {"rsi": 61, "rsi_prev": 59, "macd_hist": 0.2}))
        self.assertTrue(took["take"])
        skipped = e._challenger_decision(e._challengers[0],
                                         _trade(50, 0.01, 0.02, {"rsi": 56, "rsi_prev": 54, "macd_hist": 0.2}))
        self.assertFalse(skipped["take"])

    def test_regime_on_skips_blocked(self):
        e = ShadowEngine([{"name": "regime_on", "REGIME_FILTER_ENABLED": True}])
        d = e._challenger_decision(e._challengers[0], _trade(50, 0.01, 0.02, regime_blocked=True))
        self.assertFalse(d["take"])
        self.assertEqual(d["reason"], "regime_blocked")


class TestScaledPnl(unittest.TestCase):
    def test_scaling_banks_gains_above_final(self):
        e = ShadowEngine([{"name": "scaling_on", "SCALING_ENABLED": True}])
        # reached +3.5% (tier2) but only closed +0.5% -> scaling should beat champion
        d = e._challenger_decision(e._challengers[0], _trade(50, 0.005, 0.035))
        self.assertGreater(d["pnl"], 50.0)

    def test_never_scaled_matches_champion(self):
        e = ShadowEngine([{"name": "scaling_on", "SCALING_ENABLED": True}])
        d = e._challenger_decision(e._challengers[0], _trade(50, 0.005, 0.005))  # never hit +1.5%
        self.assertEqual(d["pnl"], 50.0)


class TestComparisonAndRecommendation(unittest.TestCase):
    def test_recommends_clear_winner(self):
        e = ShadowEngine([{"name": "scaling_on", "SCALING_ENABLED": True}])
        for pnl, final in ((50, 0.005), (-20, -0.002), (100, 0.01)):
            e.on_champion_trade(_trade(pnl, final, 0.035))   # all reached tier2
        comp = e.comparison()
        self.assertEqual(comp["champion"]["n"], 3)
        self.assertGreater(comp["scaling_on"]["total"], comp["champion"]["total"])
        recs = e.recommendation(min_sample=3)
        self.assertTrue(any("scaling_on" in r for r in recs))

    def test_no_recommendation_below_sample(self):
        e = ShadowEngine([{"name": "scaling_on", "SCALING_ENABLED": True}])
        e.on_champion_trade(_trade(50, 0.005, 0.035))
        self.assertEqual(e.recommendation(min_sample=20), [])


class TestThreadedIntake(unittest.TestCase):
    def test_worker_processes_submitted_trades(self):
        e = ShadowEngine([{"name": "regime_on", "REGIME_FILTER_ENABLED": True}])
        e.start()
        try:
            e.submit(_trade(50, 0.01, 0.02))
            deadline = time.time() + 2
            while time.time() < deadline and e.comparison()["champion"]["n"] < 1:
                time.sleep(0.02)
        finally:
            e.stop()
        self.assertEqual(e.comparison()["champion"]["n"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
