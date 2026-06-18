"""
Tests for RiskManager.sync_from_broker — the position-reconciliation fix that
makes MAX_OPEN_POSITIONS actually hold across restarts.
Run:  ./venv/bin/python -m unittest tests.test_risk_sync -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from modules.risk_manager import RiskManager        # noqa: E402


class FakeBroker:
    def __init__(self, positions):
        self._positions = positions
    def open_positions(self):
        return self._positions
    def portfolio_value(self):
        return 100000.0


def _pos(ticker, qty, entry, current):
    return {"ticker": ticker, "qty": qty, "entry": entry, "current": current,
            "market_value": qty * current, "pnl": (current - entry) * qty, "pnl_pct": 0.0}


class TestSyncFromBroker(unittest.TestCase):
    def test_adopts_existing_longs(self):
        rm = RiskManager(FakeBroker([_pos("AAPL", 99, 297.79, 297.19),
                                     _pos("MSFT", 104, 379.22, 377.37)]))
        adopted = rm.sync_from_broker()
        self.assertEqual(adopted, 2)
        self.assertEqual(set(rm.positions), {"AAPL", "MSFT"})
        self.assertEqual(rm.positions["AAPL"].qty, 99)
        self.assertAlmostEqual(rm.positions["AAPL"].current_price, 297.19)

    def test_is_idempotent(self):
        rm = RiskManager(FakeBroker([_pos("AAPL", 10, 100, 101)]))
        rm.sync_from_broker()
        again = rm.sync_from_broker()      # second call adopts nothing new
        self.assertEqual(again, 0)
        self.assertEqual(len(rm.positions), 1)

    def test_cap_now_sees_synced_positions(self):
        # 3 pre-existing positions -> at MAX_OPEN_POSITIONS, so the count reflects them
        rm = RiskManager(FakeBroker([_pos("AAPL", 10, 100, 100),
                                     _pos("MSFT", 10, 200, 200),
                                     _pos("NVDA", 10, 50, 50)]))
        rm.sync_from_broker()
        self.assertGreaterEqual(len(rm.positions), config.MAX_OPEN_POSITIONS)

    def test_skips_shorts(self):
        rm = RiskManager(FakeBroker([_pos("SQQQ", -50, 37, 37)]))  # negative qty
        self.assertEqual(rm.sync_from_broker(), 0)

    def test_broker_error_is_safe(self):
        class Boom:
            def open_positions(self):
                raise RuntimeError("api down")
        rm = RiskManager(Boom())
        self.assertEqual(rm.sync_from_broker(), 0)   # never raises into startup


if __name__ == "__main__":
    unittest.main(verbosity=2)
