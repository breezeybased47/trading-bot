"""
Tests for the course entry setups: reversal strategy, confirmation overlay, and
the paper engine that A/B-tests them. Run:
  ./venv/bin/python -m unittest tests.test_reversal_paper -v
"""

import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.STRUCTURED_LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="paper_"), "events.jsonl")
config.RESEARCH_MODE = False

from modules.reversal_strategy import ReversalStrategy   # noqa: E402
from modules.confirmation import ConfirmationOverlay     # noqa: E402
from modules.paper_engine import PaperBook, PaperEngine  # noqa: E402
from modules.strategy import BUY, SELL, Signal           # noqa: E402


def _frame(closes):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2026-01-02 09:30", periods=len(closes), freq="1min", tz="America/New_York")
    return pd.DataFrame({"open": closes, "high": closes * 1.001, "low": closes * 0.999,
                         "close": closes, "volume": 1000.0, "vwap": closes}, index=idx)


class TestReversalStrategy(unittest.TestCase):
    def _run(self, closes):
        frame = _frame(closes)
        strat = ReversalStrategy()
        sigs = []
        for i in range(config.MIN_CANDLES, len(frame) + 1):
            s = strat.evaluate("X", frame.iloc[:i])
            if s:
                sigs.append(s)
        return sigs

    def test_fires_buy_on_v_reversal(self):
        # steady decline into oversold, then a sharp V recovery that reclaims the 9-EMA
        closes = list(np.linspace(100, 80, 58)) + list(np.linspace(80.3, 93, 18))
        sigs = self._run(closes)
        buys = [s for s in sigs if s.action == BUY]
        self.assertTrue(buys, "expected a reversal BUY on the recovery")
        b = buys[0]
        self.assertEqual(b.strategy, "reversal")
        self.assertLess(b.stop, b.price)             # stop below entry
        if b.target is not None:
            self.assertGreater(b.target, b.price)    # target above entry

    def test_no_buy_on_continued_decline(self):
        closes = list(np.linspace(100, 70, 76))      # never recovers
        buys = [s for s in self._run(closes) if s.action == BUY]
        self.assertEqual(buys, [])


class TestConfirmationOverlay(unittest.TestCase):
    class _FakeStrat:
        def __init__(self):
            self.calls = 0
        def evaluate(self, ticker, candles):
            self.calls += 1
            if self.calls == 1:
                s = Signal(ticker, BUY, "fake", "trigger", 100.0)
                s.stop, s.target = 98.0, 104.0
                return s
            return None

    @staticmethod
    def _bar(close, high, low):
        return pd.DataFrame({"close": [close], "high": [high], "low": [low]})

    def test_confirms_on_close_above_high(self):
        ov = ConfirmationOverlay(self._FakeStrat())
        self.assertIsNone(ov.evaluate("X", self._bar(100.0, 100.5, 99.5)))   # pending
        sig = ov.evaluate("X", self._bar(101.0, 101.2, 100.5))               # close > 100.5 -> confirm
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action, BUY)
        self.assertIn("confirmed", sig.reason)
        self.assertEqual(sig.stop, 98.0)            # carried through

    def test_cancels_on_close_below_low(self):
        ov = ConfirmationOverlay(self._FakeStrat())
        ov.evaluate("X", self._bar(100.0, 100.5, 99.5))                      # pending
        self.assertIsNone(ov.evaluate("X", self._bar(99.0, 99.4, 98.8)))     # close < 99.5 -> cancel

    def test_times_out(self):
        config.CONFIRMATION_MAX_BARS = 2
        ov = ConfirmationOverlay(self._FakeStrat())
        ov.evaluate("X", self._bar(100.0, 100.5, 99.5))                      # pending
        self.assertIsNone(ov.evaluate("X", self._bar(100.0, 100.4, 99.6)))   # bar1, no confirm
        self.assertIsNone(ov.evaluate("X", self._bar(100.0, 100.4, 99.6)))   # bar2, timeout
        self.assertNotIn("X", ov._pending)


class TestPaperEngine(unittest.TestCase):
    class _ScriptStrat:
        def __init__(self, script):
            self.script, self.i = script, 0
        def evaluate(self, ticker, candles):
            s = self.script[self.i] if self.i < len(self.script) else None
            self.i += 1
            return s

    @staticmethod
    def _bar(close):
        return pd.DataFrame({"close": [close], "high": [close], "low": [close]})

    def _buy(self, stop=None, target=None):
        s = Signal("X", BUY, "fake", "", 100.0)
        s.stop, s.target = stop, target
        return s

    def setUp(self):
        config.PAPER_ENGINE_ENABLED = True

    def test_target_exit_pnl(self):
        strat = self._ScriptStrat([self._buy(stop=98, target=104), None, None])
        book = PaperBook("rev", strat, dollars=10000)
        book.on_bar("X", self._bar(100.0))     # entry @100, qty 100
        book.on_bar("X", self._bar(104.0))     # hits target
        self.assertEqual(book.pnls, [400.0])   # (104-100)*100

    def test_stop_exit_pnl(self):
        strat = self._ScriptStrat([self._buy(stop=98, target=110), None])
        book = PaperBook("rev", strat, dollars=10000)
        book.on_bar("X", self._bar(100.0))
        book.on_bar("X", self._bar(97.0))      # below stop
        self.assertEqual(book.pnls, [-300.0])

    def test_sell_signal_exit(self):
        strat = self._ScriptStrat([self._buy(), Signal("X", SELL, "fake", "", 0)])
        book = PaperBook("rev", strat, dollars=10000)
        book.on_bar("X", self._bar(100.0))
        book.on_bar("X", self._bar(102.0))     # SELL signal -> exit
        self.assertEqual(book.pnls, [200.0])

    def test_engine_comparison_and_toggle(self):
        book = PaperBook("rev", self._ScriptStrat([self._buy(target=104), None]), 10000)
        eng = PaperEngine([book])
        config.PAPER_ENGINE_ENABLED = False
        eng.on_bar("X", self._bar(100.0))      # disabled -> no-op
        self.assertEqual(book.stats()["n"], 0)
        config.PAPER_ENGINE_ENABLED = True
        eng.on_bar("X", self._bar(100.0))
        eng.on_bar("X", self._bar(104.0))
        comp = eng.comparison()
        self.assertEqual(comp["rev"]["n"], 1)
        self.assertEqual(comp["rev"]["total"], 400.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
