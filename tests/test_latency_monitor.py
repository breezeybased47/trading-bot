"""
Tests for Module 11 (latency monitor). Timestamps injected — no waiting.
Run:  ./venv/bin/python -m unittest tests.test_latency_monitor -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
_TMP = tempfile.mkdtemp(prefix="lat_")
config.STRUCTURED_LOG_FILE = os.path.join(_TMP, "events.jsonl")
config.LATENCY_LOG_FILE = os.path.join(_TMP, "latency.jsonl")
config.RESEARCH_MODE = False

from modules import latency_monitor as lm             # noqa: E402


class TestPercentile(unittest.TestCase):
    def test_interpolation(self):
        v = [100, 200, 300]
        self.assertEqual(lm._percentile(v, 0.0), 100)
        self.assertEqual(lm._percentile(v, 0.5), 200)
        self.assertEqual(lm._percentile(v, 1.0), 300)

    def test_empty(self):
        self.assertIsNone(lm._percentile([], 0.5))


class TestLatencyMonitor(unittest.TestCase):
    def setUp(self):
        config.LATENCY_P95_ALERT_MS = 1500
        config.LATENCY_WIDEN_EXIT_ON_DEGRADE = True
        self.m = lm.LatencyMonitor()

    def test_stage_durations(self):
        s = self.m.record(1000.0, 1000.05, 1000.10, 1000.20, ticker="AAPL")
        self.assertAlmostEqual(s["signal_to_submit_ms"], 50.0, delta=0.1)
        self.assertAlmostEqual(s["submit_to_ack_ms"], 50.0, delta=0.1)
        self.assertAlmostEqual(s["ack_to_fill_ms"], 100.0, delta=0.1)
        self.assertAlmostEqual(s["roundtrip_ms"], 200.0, delta=0.1)

    def test_not_degraded_when_fast(self):
        for fill in (0.1, 0.2, 0.15):
            self.m.record(0.0, 0.01, 0.02, fill)
        self.assertFalse(self.m.is_degraded())
        self.assertEqual(self.m.exit_threshold_multiplier(), 1.0)

    def test_degraded_widens_exits(self):
        self.m.record(0.0, 0.1, 0.5, 2.0)     # 2000ms roundtrip
        self.assertTrue(self.m.is_degraded())
        self.assertEqual(self.m.exit_threshold_multiplier(), 1.5)

    def test_percentiles_values(self):
        for fill in (0.1, 0.2, 0.3):          # 100, 200, 300 ms
            self.m.record(0.0, 0.0, 0.0, fill)
        p = self.m.percentiles()
        self.assertEqual(p["n"], 3)
        self.assertAlmostEqual(p["p50"], 200.0, delta=1)

    def test_report_text(self):
        self.m.record(0.0, 0.0, 0.0, 0.2)
        self.assertIn("EXECUTION QUALITY", self.m.daily_report())

    def test_missing_ack_safe(self):
        s = self.m.record(0.0, 0.05, None, 0.2)   # never acked
        self.assertIsNone(s["submit_to_ack_ms"])
        self.assertAlmostEqual(s["roundtrip_ms"], 200.0, delta=0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
