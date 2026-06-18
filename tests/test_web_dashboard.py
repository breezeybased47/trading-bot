"""
Tests that the web dashboard template renders (Jinja-valid) with research panels
and degrades gracefully when there's no research data. No network.
Run:  ./venv/bin/python -m unittest tests.test_web_dashboard -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import render_template_string                # noqa: E402
from modules import web_dashboard as wd                 # noqa: E402

_RESEARCH = {
    "updated": "10:00:00 ET",
    "toggles": {"REGIME_FILTER_ENABLED": True, "SIZING_MODEL": "fixed", "SCALING_ENABLED": False},
    "regime": {"regime": "TRENDING_UP", "blocked": False, "expectancy": 12.5, "sample": 25},
    "latency": {"p50": 120.0, "p95": 300.0, "degraded": False, "exit_mult": 1.0},
    "slippage": {"n": 10, "mean_actual_bps": 3.2, "mean_predicted_bps": 2.5},
    "ml": {"enabled": False, "calibration": {"n": 0, "mean_predicted": None, "mean_actual": None}},
    "cooldowns": {"AAPL": {"cooldown_seconds": 300, "cooldown_minutes": 5.0, "heat": 1.5, "consec_losses": 1}},
    "shadow": {"comparison": {
        "champion": {"n": 10, "total": 120.0, "win_rate": 0.6, "sharpe": 0.8},
        "scaling_on": {"n": 10, "total": 150.0, "win_rate": 0.6, "sharpe": 1.2}},
        "recommendations": ["Challenger 'scaling_on' beat champion"]},
    "correlation": {"tickers": ["AAPL", "MSFT"],
                    "long": {"AAPL": {"AAPL": 1.0, "MSFT": 0.8}, "MSFT": {"AAPL": 0.8, "MSFT": 1.0}},
                    "short": {}, "updated": "2026-06-17"},
    "degraded_feed": True,
}


def _render(research):
    with wd.app.app_context():
        return render_template_string(
            wd.TEMPLATE, portfolio="100,000.00", buying_power="50,000.00",
            pnl=10.0, pnl_str="10.00", positions=[], orders=[], paper=True,
            now="now", et=wd.ET, research=research)


class TestDashboardRender(unittest.TestCase):
    def test_renders_research_panels(self):
        html = _render(_RESEARCH)
        for needle in ("Research Modules", "TRENDING_UP", "Correlation",
                       "CHAMPION", "Heat", "Latency"):
            self.assertIn(needle, html)

    def test_degraded_feed_note(self):
        self.assertIn("data feed degraded", _render(_RESEARCH))

    def test_empty_research_is_graceful(self):
        html = _render({})            # standalone / before first snapshot
        self.assertNotIn("Research Modules", html)
        self.assertIn("Open Positions", html)   # base dashboard still renders


if __name__ == "__main__":
    unittest.main(verbosity=2)
