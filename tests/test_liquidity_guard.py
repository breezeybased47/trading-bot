"""
Tests for Module 4 (liquidity guard). No network — quotes/candles injected.
Run:  ./venv/bin/python -m unittest tests.test_liquidity_guard -v
"""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
_TMP = tempfile.mkdtemp(prefix="liq_")
config.STRUCTURED_LOG_FILE = os.path.join(_TMP, "events.jsonl")
config.SLIPPAGE_LOG_FILE = os.path.join(_TMP, "slippage.jsonl")
config.RESEARCH_MODE = False

from modules import liquidity_guard as lg          # noqa: E402


class FakeFeed:
    def __init__(self, quote=None, candles=None):
        self._quote = quote
        self._candles = candles
    def get_quote(self, t):
        return self._quote
    def get_candles(self, t):
        return self._candles


class TestPureHelpers(unittest.TestCase):
    def test_spread_bps(self):
        self.assertAlmostEqual(lg.spread_bps(100.0, 100.05), 5.0, delta=0.05)
        self.assertIsNone(lg.spread_bps(0, 100))
        self.assertIsNone(lg.spread_bps(100, 99))      # crossed/invalid

    def test_max_spread_bps_per_ticker(self):
        self.assertEqual(lg.max_spread_bps("AAPL"), 5)
        self.assertEqual(lg.max_spread_bps("TQQQ"), 15)
        self.assertEqual(lg.max_spread_bps("ZZZZ"), config.SPREAD_MAX_BPS_DEFAULT)

    def test_slippage_sign(self):
        self.assertAlmostEqual(lg.slippage_bps("BUY", 100.0, 100.05), 5.0, delta=0.05)   # paid up
        self.assertAlmostEqual(lg.slippage_bps("SELL", 100.0, 99.95), 5.0, delta=0.05)   # sold low
        self.assertAlmostEqual(lg.slippage_bps("BUY", 100.0, 99.95), -5.0, delta=0.05)   # price improve


class TestAssess(unittest.TestCase):
    def test_wide_spread_blocks(self):
        r = lg.assess("AAPL", 10, bid=100.0, ask=100.2, depth_proxy=1e6)  # ~20bps > 5
        self.assertFalse(r["allow"])
        self.assertIn("spread", r["reason"])

    def test_ok_when_tight_and_liquid(self):
        r = lg.assess("AAPL", 100, bid=100.0, ask=100.02, depth_proxy=1e6)
        self.assertTrue(r["allow"])
        self.assertEqual(r["adjusted_shares"], 100)

    def test_depth_reject(self):
        r = lg.assess("AAPL", 200, bid=100.0, ask=100.02, depth_proxy=1000)  # 20% > 15%
        self.assertFalse(r["allow"])
        self.assertIn("reject", r["reason"])

    def test_depth_reduce(self):
        r = lg.assess("AAPL", 100, bid=100.0, ask=100.02, depth_proxy=1000)  # 10% -> reduce to 5%
        self.assertTrue(r["allow"])
        self.assertEqual(r["adjusted_shares"], 50)

    def test_no_quote_fails_open(self):
        r = lg.assess("AAPL", 100, bid=None, ask=None, depth_proxy=1000)
        self.assertTrue(r["allow"])
        self.assertEqual(r["reason"], "no_quote_data")


class TestLiveWrapper(unittest.TestCase):
    def setUp(self):
        config.LIQUIDITY_GUARD_ENABLED = True

    def test_filter_off(self):
        config.LIQUIDITY_GUARD_ENABLED = False
        g = lg.LiquidityGuard(FakeFeed())
        self.assertTrue(g.check_entry("AAPL", 100)["allow"])

    def test_uses_volume_proxy_from_candles(self):
        candles = pd.DataFrame({"volume": [50_000] * 20})
        feed = FakeFeed(quote={"bid": 100.0, "ask": 100.02, "bid_size": 100, "ask_size": 100},
                        candles=candles)
        g = lg.LiquidityGuard(feed)
        r = g.check_entry("AAPL", 100)   # 100 vs 50k volume proxy -> fine
        self.assertTrue(r["allow"])

    def test_predicted_slippage_half_spread(self):
        feed = FakeFeed(quote={"bid": 100.0, "ask": 100.10})  # ~10 bps spread
        g = lg.LiquidityGuard(feed)
        self.assertAlmostEqual(g.predicted_slippage_bps("AAPL"), 5.0, delta=0.1)

    def test_record_fill_writes_and_summarises(self):
        g = lg.LiquidityGuard(FakeFeed())
        g.record_fill("AAPL", "BUY", 100.0, 100.04, predicted_bps=2.0)
        g.record_fill("MSFT", "BUY", 200.0, 200.04, predicted_bps=1.0)
        s = lg.slippage_summary()
        self.assertEqual(s["n"], 2)
        self.assertIsNotNone(s["mean_actual_bps"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
