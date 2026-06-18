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
from modules.overbought_reversal import OverboughtReversalStrategy  # noqa: E402
from modules.confirmation import ConfirmationOverlay     # noqa: E402
from modules.paper_engine import PaperBook, PaperEngine  # noqa: E402
from modules.strategy import BUY, SELL, SHORT, Signal    # noqa: E402


def _frame(closes):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2026-01-02 09:30", periods=len(closes), freq="1min", tz="America/New_York")
    return pd.DataFrame({"open": closes, "high": closes * 1.001, "low": closes * 0.999,
                         "close": closes, "volume": 1000.0, "vwap": closes}, index=idx)


def _ohlc(rows):
    closes = [r[0] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    idx = pd.date_range("2026-01-02 09:30", periods=len(rows), freq="1min", tz="America/New_York")
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes,
                         "volume": 1000.0, "vwap": closes}, index=idx)


def _flat(c):
    return (c, c + 0.1, c - 0.1)


class TestReversalStrategy(unittest.TestCase):
    def _run(self, rows):
        frame = _ohlc(rows)
        strat = ReversalStrategy()
        sigs = []
        for i in range(42, len(frame) + 1):          # strategy needs >= 2*lookback+2 bars
            s = strat.evaluate("X", frame.iloc[:i])
            if s:
                sigs.append(s)
        return sigs

    def _base(self):
        rows = [_flat(c) for c in np.linspace(100, 92.5, 22)]           # rejection (lower lows)
        for i in range(22):                                            # tight consolidation ~91.2/92.3
            rows.append(_flat(92.3 if i % 2 else 91.2))
        rows.append((93.5, 93.6, 93.3))                                # breakout above ~92.4
        rows += [_flat(c) for c in (93.8, 93.9, 93.7)]                 # holds above the breakout
        return rows

    def test_fires_on_break_and_retest(self):
        rows = self._base()
        rows.append((92.7, 93.0, 92.5))     # pullback taps old resistance (~92.4) and holds
        rows.append((93.0, 93.2, 92.7))     # turns back up -> ENTRY
        buys = [s for s in self._run(rows) if s.action == BUY]
        self.assertTrue(buys, "expected a break-and-retest BUY")
        b = buys[0]
        self.assertEqual(b.strategy, "reversal")
        self.assertLess(b.stop, b.price)            # stop below new support
        self.assertGreater(b.target, b.price)       # 2R target above entry

    def test_no_entry_if_breakout_fails(self):
        rows = self._base()
        rows.append((91.5, 91.7, 91.3))     # sells back off below the breakout -> setup voided
        rows.append((91.6, 91.8, 91.4))
        buys = [s for s in self._run(rows) if s.action == BUY]
        self.assertEqual(buys, [])

    def test_no_entry_on_pure_decline(self):
        rows = [_flat(c) for c in np.linspace(100, 70, 60)]   # never consolidates/reverses
        self.assertEqual([s for s in self._run(rows) if s.action == BUY], [])


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


class TestOverboughtReversal(unittest.TestCase):
    def _run(self, closes):
        frame = _frame(closes)
        strat = OverboughtReversalStrategy()
        sigs = []
        for i in range(config.MIN_CANDLES, len(frame) + 1):
            s = strat.evaluate("X", frame.iloc[:i])
            if s:
                sigs.append(s)
        return sigs

    def test_shorts_overbought_rollover(self):
        closes = (list(np.linspace(80, 95, 50)) + [96, 97.5, 99, 101, 102]   # rally into overbought
                  + [99.5, 96.5, 93.5])                                       # sharp roll-over
        shorts = [s for s in self._run(closes) if s.action == SHORT]
        self.assertTrue(shorts, "expected an overbought SHORT on the roll-over")
        s = shorts[0]
        self.assertEqual(s.strategy, "overbought_short")
        self.assertGreater(s.stop, s.price)      # stop ABOVE entry (short)
        self.assertLess(s.target, s.price)       # target BELOW entry

    def test_no_short_on_steady_uptrend(self):
        closes = list(np.linspace(80, 100, 60))  # rallies but never rolls over
        self.assertEqual([s for s in self._run(closes) if s.action == SHORT], [])


class TestPaperShort(unittest.TestCase):
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

    def _short(self, stop, target):
        s = Signal("X", SHORT, "ob", "", 100.0)
        s.stop, s.target = stop, target
        return s

    def setUp(self):
        config.PAPER_ENGINE_ENABLED = True

    def test_short_profits_when_price_falls(self):
        book = PaperBook("ob", self._ScriptStrat([self._short(103, 94), None]), 10000)
        book.on_bar("X", self._bar(100.0))     # open short @100, qty 100
        book.on_bar("X", self._bar(94.0))      # fell to target -> cover
        self.assertEqual(book.pnls, [600.0])   # (100-94)*100

    def test_short_stops_out_when_price_rises(self):
        book = PaperBook("ob", self._ScriptStrat([self._short(103, 90), None]), 10000)
        book.on_bar("X", self._bar(100.0))
        book.on_bar("X", self._bar(104.0))     # rose above stop -> cover loss
        self.assertEqual(book.pnls, [-400.0])  # (100-104)*100


if __name__ == "__main__":
    unittest.main(verbosity=2)
