"""
Unit tests for the Phase-1 research modules (Modules 1, 2, 8 foundation).
Run:  ./venv/bin/python -m unittest tests.test_research_modules -v

No network, no broker, no new dependencies. Journal/structured-log paths are
redirected to a temp dir so tests never touch real research data.
"""

import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

# Redirect all file-writing side effects to a throwaway dir for the whole run.
_TMP = tempfile.mkdtemp(prefix="bot_tests_")
config.STRUCTURED_LOG_FILE = os.path.join(_TMP, "events.jsonl")
config.JOURNAL_DB_FILE = os.path.join(_TMP, "journal.db")
config.REGIME_PERF_FILE = os.path.join(_TMP, "regime_perf.json")
config.RESEARCH_MODE = False  # quieter logs during tests

from modules import market_classifier as mc      # noqa: E402
from modules import position_sizer as ps          # noqa: E402
from modules import journal                        # noqa: E402
from modules.regime_filter import RegimeFilter     # noqa: E402


def _candles(closes):
    """Build a minimal OHLCV frame from a list of closes."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2026-01-02 09:30", periods=len(closes), freq="1min", tz="America/New_York")
    return pd.DataFrame({
        "open": closes, "high": closes * 1.001, "low": closes * 0.999,
        "close": closes, "volume": np.full(len(closes), 1000.0), "vwap": closes,
    }, index=idx)


class FakeFeed:
    def __init__(self, candles=None):
        self._c = candles if candles is not None else _candles([100] * 130)
    def get_candles(self, sym):
        return self._c


# ───────────────────────── Market classifier ─────────────────────────────────

class TestMarketClassifier(unittest.TestCase):
    def test_insufficient_data_is_choppy(self):
        d = mc.classify_detail(_candles([100] * 10))
        self.assertEqual(d["regime"], mc.CHOPPY)
        self.assertEqual(d["reason"], "insufficient_data")

    def test_trending_up(self):
        closes = np.linspace(100, 112, 140)  # steady, low-noise rise
        self.assertEqual(mc.classify(_candles(closes)), mc.TRENDING_UP)

    def test_trending_down(self):
        closes = np.linspace(112, 100, 140)
        self.assertEqual(mc.classify(_candles(closes)), mc.TRENDING_DOWN)

    def test_choppy_flat(self):
        closes = 100 + 0.01 * np.sin(np.arange(140))  # tiny oscillation, no trend
        self.assertEqual(mc.classify(_candles(closes)), mc.CHOPPY)

    def test_volatile_up(self):
        rng = np.random.RandomState(42)
        calm = 0.0003 * rng.randn(110)
        wild = 0.0015 + 0.012 * rng.randn(30)   # higher drift + much higher vol
        rets = np.concatenate([calm, wild])
        closes = 100 * np.cumprod(1 + rets)
        d = mc.classify_detail(_candles(closes))
        self.assertTrue(d["elevated"], "recent vol should be elevated vs baseline")
        self.assertEqual(d["regime"], mc.VOLATILE_UP)


# ───────────────────────────── Journal ───────────────────────────────────────

class TestJournal(unittest.TestCase):
    def setUp(self):
        # fresh DB per test
        config.JOURNAL_DB_FILE = os.path.join(_TMP, "j_%s.db" % self._testMethodName)
        journal.init()

    def test_entry_exit_and_regime_stats(self):
        tid = journal.record_entry("AAPL", "momentum", 100.0, 10,
                                    regime="TRENDING_UP", sizing_model="fixed", size_chosen=10)
        self.assertIsNotNone(tid)
        journal.record_exit(tid, 101.5, exit_reason="vwap")
        stats = journal.regime_stats()
        self.assertEqual(stats["TRENDING_UP"]["n"], 1)
        self.assertAlmostEqual(stats["TRENDING_UP"]["expectancy"], 15.0, places=2)
        self.assertEqual(stats["TRENDING_UP"]["win_rate"], 1.0)

    def test_win_label_uses_excursion(self):
        # Closes green but never reached +1.5% target -> should be labelled a loss(0)
        tid = journal.record_entry("MSFT", "momentum", 100.0, 10, regime="CHOPPY")
        journal.update_excursion(tid, 0.007)   # best it ever got was +0.7%
        journal.record_exit(tid, 100.3)         # closed +0.3%
        row = journal.closed_trades()[0]
        self.assertEqual(row["win"], 0)
        self.assertGreater(row["pnl"], 0)       # still green, but not a "win" by target

    def test_consecutive_losses_and_winrate(self):
        for px in (99.0, 98.0, 101.0, 97.0, 96.0):  # W/L pattern, newest last
            tid = journal.record_entry("AMD", "momentum", 100.0, 1)
            journal.record_exit(tid, px)
        # newest two (97, 96) are losses -> streak 2
        self.assertEqual(journal.consecutive_losses(), 2)
        win_rate, ratio, n = journal.recent_winrate_and_ratio(50)
        self.assertEqual(n, 5)
        self.assertAlmostEqual(win_rate, 1 / 5)
        self.assertGreater(ratio, 0)


# ──────────────────────────── Regime filter ──────────────────────────────────

class TestRegimeFilter(unittest.TestCase):
    def setUp(self):
        config.JOURNAL_DB_FILE = os.path.join(_TMP, "rf_%s.db" % self._testMethodName)
        journal.init()
        self.rf = RegimeFilter(FakeFeed())

    def _load_regime(self, regime, pnls):
        for p in pnls:
            tid = journal.record_entry("AAPL", "momentum", 100.0, 1, regime=regime)
            journal.record_exit(tid, 100.0 + p)  # pnl == p (qty 1)

    def test_filter_off_always_allows(self):
        config.REGIME_FILTER_ENABLED = False
        self.rf._regime = mc.CHOPPY
        d = self.rf.entry_decision("AAPL")
        self.assertTrue(d["allow"])
        self.assertEqual(d["size_mult"], 1.0)

    def test_half_size_below_min_sample(self):
        config.REGIME_FILTER_ENABLED = True
        self.rf._regime = mc.TRENDING_UP
        self._load_regime(mc.TRENDING_UP, [1.0, -0.5, 1.0])  # only 3 trades
        d = self.rf.entry_decision("AAPL")
        self.assertTrue(d["allow"])
        self.assertEqual(d["size_mult"], 0.5)

    def test_blocks_negative_expectancy_with_enough_sample(self):
        config.REGIME_FILTER_ENABLED = True
        self.rf._regime = mc.VOLATILE_DOWN
        # 25 trades, net negative expectancy
        self._load_regime(mc.VOLATILE_DOWN, [-1.0] * 18 + [0.5] * 7)
        d = self.rf.entry_decision("AAPL")
        self.assertFalse(d["allow"])
        self.assertIn("REGIME BLOCKED", d["reason"])

    def test_allows_positive_expectancy_with_enough_sample(self):
        config.REGIME_FILTER_ENABLED = True
        self.rf._regime = mc.TRENDING_UP
        self._load_regime(mc.TRENDING_UP, [1.0] * 18 + [-0.5] * 7)
        d = self.rf.entry_decision("AAPL")
        self.assertTrue(d["allow"])
        self.assertEqual(d["size_mult"], 1.0)


# ─────────────────────────── Position sizer ──────────────────────────────────

class TestPositionSizer(unittest.TestCase):
    EQ = 100_000.0

    def test_fixed_is_ten_percent(self):
        r = ps.compute_size("fixed", 100.0, self.EQ)
        self.assertEqual(r["qty"], 100)              # 10% of 100k / $100
        self.assertEqual(r["model_used"], "fixed")

    def test_vol_adjusted_sizes_down_volatile_names(self):
        # high ATR -> wider stop -> fewer shares, but ~constant dollar risk
        r = ps.compute_size("vol_adjusted", 100.0, self.EQ, atr=4.0, atr_avg=2.0)
        self.assertLess(r["qty"], 100)               # smaller than the fixed cap
        risk_at_stop = r["qty"] * config.ATR_STOP_MULT * 4.0
        self.assertAlmostEqual(risk_at_stop, self.EQ * config.ACCOUNT_RISK_PER_TRADE_PCT, delta=120)

    def test_kelly_falls_back_below_min_sample(self):
        r = ps.compute_size("kelly", 100.0, self.EQ, atr=4.0, atr_avg=2.0,
                            win_rate=0.6, win_loss_ratio=2.0, sample=10)
        self.assertEqual(r["model_used"], "vol_adjusted")

    def test_kelly_no_edge_no_bet(self):
        r = ps.compute_size("kelly", 100.0, self.EQ, win_rate=0.50, win_loss_ratio=1.0, sample=50)
        self.assertEqual(r["qty"], 0)                # f* <= 0 -> stand aside

    def test_kelly_small_edge_sizes_in(self):
        r = ps.compute_size("kelly", 100.0, self.EQ, win_rate=0.52, win_loss_ratio=1.0, sample=50)
        self.assertEqual(r["model_used"], "kelly")
        self.assertGreater(r["qty"], 0)
        self.assertLessEqual(r["qty"], 100)          # never above the 10% cap

    def test_consecutive_loss_halves_size(self):
        r = ps.compute_size("fixed", 100.0, self.EQ, consecutive_losses=2)
        self.assertEqual(r["qty"], 50)               # 10% halved -> 5%

    def test_never_exceeds_position_cap(self):
        r = ps.compute_size("vol_adjusted", 100.0, self.EQ, atr=0.1, atr_avg=0.1)
        self.assertLessEqual(r["qty"], 100)

    def test_total_exposure_cap(self):
        r = ps.compute_size("fixed", 100.0, self.EQ, current_exposure_value=24_000.0)
        self.assertEqual(r["qty"], 10)               # only $1k room left -> 10 sh

    def test_regime_multiplier_applied(self):
        r = ps.compute_size("fixed", 100.0, self.EQ, regime_size_mult=0.5)
        self.assertEqual(r["qty"], 50)

    def test_invalid_inputs_safe(self):
        self.assertEqual(ps.compute_size("fixed", 0.0, self.EQ)["qty"], 0)
        self.assertEqual(ps.compute_size("fixed", 100.0, 0.0)["qty"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
