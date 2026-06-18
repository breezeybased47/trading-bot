"""
Tests for the course-derived guards: Economic Event Guard + leveraged-ETF decay.
Run:  ./venv/bin/python -m unittest tests.test_course_modules -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="course_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules import economic_calendar as ec          # noqa: E402
from modules import leverage_guard as lg              # noqa: E402

ET = pytz.timezone("America/New_York")
FOMC = ET.localize(datetime(2026, 6, 18, 14, 0))
CPI = ET.localize(datetime(2026, 6, 18, 8, 30))
DEFAULTS = dict(before_min=15, after_min=30, caution_min=60, fomc_after_min=90)


class TestSchedule(unittest.TestCase):
    def test_first_friday_is_a_friday(self):
        for m in range(1, 13):
            d = ec.first_friday(2026, m)
            self.assertEqual(d.weekday(), 4)      # Friday
            self.assertLessEqual(d.day, 7)
            self.assertEqual(d.month, m)

    def test_nfp_events_at_830_first_friday(self):
        evs = ec.nfp_events(ET.localize(datetime(2026, 6, 10)))
        dt, name, impact = evs[0]
        self.assertEqual((dt.hour, dt.minute), (8, 30))
        self.assertEqual(dt.weekday(), 4)
        self.assertEqual(impact, "high")
        self.assertIn("NFP", name)


class TestEventStatus(unittest.TestCase):
    def test_blackout_just_before_event(self):
        s = ec.event_status(FOMC - timedelta(minutes=5), [(FOMC, "FOMC", "high")], **DEFAULTS)
        self.assertEqual(s["state"], ec.NO_TOUCH)

    def test_fomc_long_tail(self):
        s = ec.event_status(FOMC + timedelta(minutes=60), [(FOMC, "FOMC", "high")], **DEFAULTS)
        self.assertEqual(s["state"], ec.NO_TOUCH)      # 90-min FOMC tail still active

    def test_standard_event_short_tail(self):
        s = ec.event_status(CPI + timedelta(minutes=45), [(CPI, "CPI", "high")], **DEFAULTS)
        self.assertEqual(s["state"], ec.CLEAR)          # 30-min tail expired

    def test_caution_window(self):
        s = ec.event_status(FOMC - timedelta(minutes=30), [(FOMC, "FOMC", "high")], **DEFAULTS)
        self.assertEqual(s["state"], ec.CAUTION)        # before blackout, inside caution buffer

    def test_clear_far_away(self):
        s = ec.event_status(FOMC - timedelta(hours=5), [(FOMC, "FOMC", "high")], **DEFAULTS)
        self.assertEqual(s["state"], ec.CLEAR)


class TestEconGuard(unittest.TestCase):
    def _cal(self, events, now):
        cal = ec.EconomicCalendar()
        cal._events = events
        cal._built_day = now.date()
        return cal

    def test_filter_off(self):
        config.ECON_GUARD_ENABLED = False
        cal = self._cal([(FOMC, "FOMC", "high")], FOMC)
        self.assertTrue(cal.check_entry("AAPL", now=FOMC - timedelta(minutes=5))["allow"])

    def test_blocks_in_blackout(self):
        config.ECON_GUARD_ENABLED = True
        cal = self._cal([(FOMC, "FOMC", "high")], FOMC)
        d = cal.check_entry("AAPL", now=FOMC - timedelta(minutes=5))
        self.assertFalse(d["allow"])
        self.assertIn("blackout", d["reason"])

    def test_half_size_in_caution(self):
        config.ECON_GUARD_ENABLED = True
        cal = self._cal([(FOMC, "FOMC", "high")], FOMC)
        d = cal.check_entry("AAPL", now=FOMC - timedelta(minutes=30))
        self.assertTrue(d["allow"])
        self.assertEqual(d["size_mult"], 0.5)


class TestLeverageGuard(unittest.TestCase):
    def setUp(self):
        config.LEVERAGED_ETF_REGIME_GUARD_ENABLED = True

    def test_filter_off(self):
        config.LEVERAGED_ETF_REGIME_GUARD_ENABLED = False
        self.assertTrue(lg.check_entry("TQQQ", "CHOPPY")["allow"])

    def test_blocks_leveraged_in_chop(self):
        self.assertFalse(lg.check_entry("TQQQ", "CHOPPY")["allow"])
        self.assertFalse(lg.check_entry("SQQQ", "VOLATILE_DOWN")["allow"])

    def test_allows_leveraged_in_trend(self):
        self.assertTrue(lg.check_entry("TQQQ", "TRENDING_UP")["allow"])

    def test_ignores_non_leveraged(self):
        self.assertTrue(lg.check_entry("AAPL", "CHOPPY")["allow"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
