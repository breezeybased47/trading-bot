"""
Tests for Module 7 (pre-market scanner). No network — gap/news/earnings stubbed.
Run:  ./venv/bin/python -m unittest tests.test_premarket_scanner -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="pm_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules import premarket_scanner as pm          # noqa: E402

ET = pytz.timezone("America/New_York")
T0 = ET.localize(datetime(2026, 6, 17, 9, 0, 0))


class TestPureHelpers(unittest.TestCase):
    def test_compute_gap(self):
        self.assertAlmostEqual(pm.compute_gap(100.0, 104.0), 0.04)
        self.assertIsNone(pm.compute_gap(None, 104.0))
        self.assertIsNone(pm.compute_gap(100.0, 0))

    def test_classify_earnings_is_no_touch(self):
        self.assertEqual(pm.classify_gap(0.0, False, True)["status"], pm.NO_TOUCH)

    def test_classify_big_gap_with_news_no_touch(self):
        self.assertEqual(pm.classify_gap(0.05, True, False)["status"], pm.NO_TOUCH)

    def test_classify_big_gap_no_news_caution(self):
        d = pm.classify_gap(0.05, False, False)
        self.assertEqual(d["status"], pm.CAUTION)
        self.assertEqual(d["size_mult"], 0.5)

    def test_classify_mid_gap_caution(self):
        self.assertEqual(pm.classify_gap(0.03, False, False)["status"], pm.CAUTION)

    def test_classify_small_gap_normal(self):
        self.assertEqual(pm.classify_gap(0.01, False, False)["status"], pm.NORMAL)

    def test_classify_no_gap_data_normal(self):
        self.assertEqual(pm.classify_gap(None, False, False)["status"], pm.NORMAL)


class TestScannerRun(unittest.TestCase):
    def test_degrades_gap_only_without_key(self):
        config.FINNHUB_API_KEY = None
        s = pm.PremarketScanner(tickers=["AAPL", "TSLA"])
        s._fetch_gap = lambda t, now: {"AAPL": 0.05, "TSLA": 0.01}[t]
        s.run(now=T0)
        self.assertFalse(s.news_available)
        self.assertEqual(s.status_for("AAPL")["status"], pm.CAUTION)   # 5% no news
        self.assertEqual(s.status_for("TSLA")["status"], pm.NORMAL)
        self.assertEqual(s.size_mult("AAPL"), 0.5)

    def test_no_touch_on_news_gap(self):
        config.FINNHUB_API_KEY = "fake-key"
        s = pm.PremarketScanner(tickers=["NVDA"])
        s._fetch_gap = lambda t, now: 0.06
        s._fetch_news = lambda t, now: ["NVDA jumps on AI demand"]
        s._fetch_earnings = lambda t, now: False
        s.run(now=T0)
        self.assertTrue(s.is_no_touch("NVDA"))
        self.assertEqual(s.size_mult("NVDA"), 0.0)

    def test_no_touch_on_earnings(self):
        config.FINNHUB_API_KEY = "fake-key"
        s = pm.PremarketScanner(tickers=["AMD"])
        s._fetch_gap = lambda t, now: 0.0
        s._fetch_news = lambda t, now: []
        s._fetch_earnings = lambda t, now: True
        s.run(now=T0)
        self.assertTrue(s.is_no_touch("AMD"))

    def test_briefing_text(self):
        config.FINNHUB_API_KEY = None
        s = pm.PremarketScanner(tickers=["AAPL"])
        s._fetch_gap = lambda t, now: 0.03
        s.run(now=T0)
        text = s.format_briefing()
        self.assertIn("AAPL", text)
        self.assertIn("CAUTION", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
