"""
Tests for Module: long-term market-direction gauge (QQQ daily trend). No network.
Run:  ./venv/bin/python -m unittest tests.test_market_direction -v
"""

import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="md_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules import market_direction as md          # noqa: E402


class TestClassify(unittest.TestCase):
    def test_bull_uptrend(self):
        d, det = md.classify(list(np.linspace(100, 200, 250)), 50, 200)
        self.assertEqual(d, md.BULL)
        self.assertGreater(det["sma_fast"], det["sma_slow"])   # 50d above 200d
        self.assertGreater(det["price"], det["sma_fast"])

    def test_bear_downtrend(self):
        d, _ = md.classify(list(np.linspace(200, 100, 250)), 50, 200)
        self.assertEqual(d, md.BEAR)

    def test_neutral_flat(self):
        d, _ = md.classify([150.0] * 250, 50, 200)
        self.assertEqual(d, md.NEUTRAL)

    def test_neutral_insufficient_data(self):
        d, det = md.classify(list(np.linspace(100, 110, 50)), 50, 200)   # < 200 bars
        self.assertEqual(d, md.NEUTRAL)
        self.assertEqual(det, {})


class TestSizeGate(unittest.TestCase):
    def setUp(self):
        config.MARKET_DIRECTION_ENABLED = True

    def test_bear_sizes_down(self):
        m = md.MarketDirection(hist_client=None)
        m._direction = md.BEAR
        self.assertEqual(m.size_mult(), config.MARKET_BEAR_SIZE_MULT)

    def test_bull_full_size(self):
        m = md.MarketDirection(hist_client=None)
        m._direction = md.BULL
        self.assertEqual(m.size_mult(), 1.0)

    def test_neutral_full_size(self):
        m = md.MarketDirection(hist_client=None)
        m._direction = md.NEUTRAL
        self.assertEqual(m.size_mult(), 1.0)

    def test_disabled_no_change(self):
        config.MARKET_DIRECTION_ENABLED = False
        m = md.MarketDirection(hist_client=None)
        m._direction = md.BEAR
        self.assertEqual(m.size_mult(), 1.0)

    def test_status_shape(self):
        config.MARKET_DIRECTION_ENABLED = True
        m = md.MarketDirection(hist_client=None)
        m._direction = md.BULL
        m._detail = {"price": 300.0, "sma_fast": 290.0, "sma_slow": 280.0}
        s = m.status()
        self.assertEqual(s["direction"], md.BULL)
        self.assertEqual(s["price"], 300.0)
        self.assertEqual(s["size_mult"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
