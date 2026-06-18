"""
Tests for Module 3 (correlation guard). No network — matrices are injected.
Run:  ./venv/bin/python -m unittest tests.test_correlation_monitor -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

import pandas as pd
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="corr_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules.correlation_monitor import CorrelationMonitor, LONG_TECH_NAMES  # noqa: E402

ET = pytz.timezone("America/New_York")
TKRS = ["AAPL", "MSFT", "TSLA", "TQQQ", "SQQQ"]


def _matrix(pairs):
    """Symmetric correlation matrix, diagonal 1.0, off-diagonal from `pairs`."""
    df = pd.DataFrame(0.0, index=TKRS, columns=TKRS)
    for t in TKRS:
        df.loc[t, t] = 1.0
    for (a, b), v in pairs.items():
        df.loc[a, b] = v
        df.loc[b, a] = v
    return df


def _monitor(long_pairs=None, short_pairs=None):
    m = CorrelationMonitor(hist_client=None, tickers=TKRS)
    m.corr_long = _matrix(long_pairs or {})
    m.corr_short = _matrix(short_pairs or {})
    m.last_refresh = datetime.now(ET).date()   # prevent any network refresh
    return m


class TestCorrelationGuard(unittest.TestCase):
    def setUp(self):
        config.CORRELATION_GUARD_ENABLED = True
        config.CORR_TQQQ_TECH_RULE = True

    def test_filter_off_allows(self):
        config.CORRELATION_GUARD_ENABLED = False
        self.assertTrue(_monitor().check_entry("AAPL", ["MSFT"])["allow"])

    def test_no_open_positions_allows(self):
        self.assertTrue(_monitor().check_entry("AAPL", [])["allow"])

    def test_blocks_high_30d_corr(self):
        m = _monitor(long_pairs={("AAPL", "MSFT"): 0.80}, short_pairs={("AAPL", "MSFT"): 0.50})
        d = m.check_entry("AAPL", ["MSFT"])
        self.assertFalse(d["allow"])
        self.assertIn("30d corr", d["reason"])
        self.assertEqual(d["with_ticker"], "MSFT")

    def test_blocks_high_5d_corr_even_if_30d_ok(self):
        m = _monitor(long_pairs={("AAPL", "MSFT"): 0.40}, short_pairs={("AAPL", "MSFT"): 0.90})
        d = m.check_entry("AAPL", ["MSFT"])
        self.assertFalse(d["allow"])
        self.assertIn("5d corr", d["reason"])

    def test_allows_when_below_thresholds(self):
        m = _monitor(long_pairs={("AAPL", "MSFT"): 0.30}, short_pairs={("AAPL", "MSFT"): 0.30})
        d = m.check_entry("AAPL", ["MSFT"])
        self.assertTrue(d["allow"])
        self.assertEqual(d["reason"], "corr_ok")
        self.assertEqual(d["worst_corr_30"], 0.3)

    def test_tqqq_blocks_additional_long_tech(self):
        m = _monitor()  # matrices irrelevant — rule fires first
        d = m.check_entry("AAPL", ["TQQQ"])
        self.assertFalse(d["allow"])
        self.assertIn("TQQQ", d["reason"])

    def test_tqqq_rule_does_not_block_inverse_sqqq(self):
        # SQQQ is an inverse ETF, not long tech — TQQQ rule must not catch it.
        self.assertNotIn("SQQQ", LONG_TECH_NAMES)
        m = _monitor(long_pairs={("SQQQ", "TQQQ"): -0.95}, short_pairs={("SQQQ", "TQQQ"): -0.9})
        self.assertTrue(m.check_entry("SQQQ", ["TQQQ"])["allow"])

    def test_fails_open_with_no_data(self):
        m = CorrelationMonitor(hist_client=None, tickers=TKRS)  # no matrices
        m.last_refresh = datetime.now(ET).date()
        d = m.check_entry("AAPL", ["MSFT"])
        self.assertTrue(d["allow"])
        self.assertEqual(d["reason"], "no_correlation_data")


class TestReturnsPivot(unittest.TestCase):
    def test_pivots_multiindex_bars(self):
        idx = pd.MultiIndex.from_product(
            [["AAPL", "MSFT"], pd.date_range("2026-01-02", periods=4, freq="D")],
            names=["symbol", "timestamp"])
        bars = pd.DataFrame({"close": [100, 101, 102, 103, 50, 51, 52, 53]}, index=idx)
        returns = CorrelationMonitor._returns_from_bars(bars)
        self.assertEqual(sorted(returns.columns), ["AAPL", "MSFT"])
        self.assertEqual(len(returns), 3)          # 4 closes -> 3 returns

    def test_empty_bars_safe(self):
        self.assertIsNone(CorrelationMonitor._returns_from_bars(pd.DataFrame()))


class TestHeatmap(unittest.TestCase):
    def test_heatmap_structure(self):
        m = _monitor(long_pairs={("AAPL", "MSFT"): 0.5})
        h = m.heatmap_data()
        self.assertEqual(h["tickers"], TKRS)
        self.assertEqual(h["long"]["AAPL"]["MSFT"], 0.5)
        self.assertIn("short", h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
