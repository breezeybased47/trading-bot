"""
Tests for broker pending-order hygiene: filled orders leave the pending set, and
cancelling an already-filled order is treated as a benign no-op (not an error).
Run:  ./venv/bin/python -m unittest tests.test_broker -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.broker import Broker


class _FakeOrder:
    def __init__(self, filled_avg_price):
        self.filled_avg_price = filled_avg_price


class _FakeClient:
    def __init__(self, order=None, cancel_exc=None):
        self._order = order
        self._cancel_exc = cancel_exc
        self.cancelled = []

    def get_order_by_id(self, oid):
        return self._order

    def cancel_order_by_id(self, oid):
        if self._cancel_exc:
            raise Exception(self._cancel_exc)
        self.cancelled.append(oid)


def _broker(client):
    b = Broker.__new__(Broker)        # bypass __init__ (no TradingClient / network)
    b._client = client
    b._pending = {}
    return b


class TestBrokerPending(unittest.TestCase):
    def test_fill_price_clears_pending(self):
        b = _broker(_FakeClient(order=_FakeOrder("101.5")))
        b._pending = {"o1": {"ticker": "AAPL"}}
        self.assertEqual(b.fill_price("o1"), 101.5)
        self.assertNotIn("o1", b._pending)          # filled -> no longer tracked

    def test_fill_price_keeps_unfilled(self):
        b = _broker(_FakeClient(order=_FakeOrder(None)))
        b._pending = {"o1": {"ticker": "AAPL"}}
        self.assertIsNone(b.fill_price("o1"))
        self.assertIn("o1", b._pending)             # still open -> still tracked

    def test_cancel_already_filled_is_benign(self):
        exc = '{"code":42210000,"message":"order is already in \\"filled\\" state"}'
        b = _broker(_FakeClient(cancel_exc=exc))
        b._pending = {"o2": {"ticker": "AAPL"}}
        self.assertFalse(b.cancel("o2"))            # returns False, does not raise
        self.assertNotIn("o2", b._pending)

    def test_cancel_success(self):
        client = _FakeClient()
        b = _broker(client)
        b._pending = {"o3": {"ticker": "AAPL"}}
        self.assertTrue(b.cancel("o3"))
        self.assertNotIn("o3", b._pending)
        self.assertIn("o3", client.cancelled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
