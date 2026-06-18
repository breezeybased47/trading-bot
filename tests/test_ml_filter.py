"""
Tests for Module 10 (ML veto filter scaffold). No ML library required — a fake
model exercises the decision/calibration logic. Verifies it FAILS OPEN.
Run:  ./venv/bin/python -m unittest tests.test_ml_filter -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
_TMP = tempfile.mkdtemp(prefix="ml_")
config.STRUCTURED_LOG_FILE = os.path.join(_TMP, "events.jsonl")
config.ML_MODEL_FILE = os.path.join(_TMP, "model.pkl")     # nonexistent -> no model loaded
config.JOURNAL_DB_FILE = os.path.join(_TMP, "journal.db")
config.RESEARCH_MODE = False

from modules import ml_filter as mlf                  # noqa: E402
from modules import journal                            # noqa: E402


class FakeModel:
    def __init__(self, p_win):
        self.p = p_win
        self.feature_importances_ = [0.0] * len(mlf.FEATURE_NAMES)
    def predict_proba(self, X):
        return [[1 - self.p, self.p] for _ in X]


TAGS = {"ticker": "AAPL", "regime": "CHOPPY", "time_bucket": "midday",
        "signals": {"rsi": 50, "macd_hist": 0.1, "ema9": 100, "ema21": 99}, "spread_bps": 3}


class TestFeatures(unittest.TestCase):
    def test_vector_length_matches_names(self):
        self.assertEqual(len(mlf.feature_vector(TAGS)), len(mlf.FEATURE_NAMES))

    def test_regime_one_hot(self):
        f = mlf.features_from_tags(TAGS)
        self.assertEqual(f["regime_CHOPPY"], 1.0)
        self.assertEqual(f["regime_TRENDING_UP"], 0.0)
        self.assertAlmostEqual(f["ema9_minus_ema21"], 1.0)   # 100 - 99

    def test_signals_as_json_string(self):
        import json
        t = dict(TAGS, signals=json.dumps(TAGS["signals"]))
        self.assertEqual(mlf.feature_vector(t), mlf.feature_vector(TAGS))


class TestVetoFailsOpen(unittest.TestCase):
    def setUp(self):
        self.mf = mlf.MLFilter()

    def test_filter_off(self):
        config.ML_FILTER_ENABLED = False
        self.assertFalse(self.mf.should_veto(TAGS)["veto"])

    def test_no_model_fails_open(self):
        config.ML_FILTER_ENABLED = True
        self.mf._model = None
        d = self.mf.should_veto(TAGS)
        self.assertFalse(d["veto"])
        self.assertEqual(d["reason"], "no_model")

    def test_low_pwin_vetoes(self):
        config.ML_FILTER_ENABLED = True
        self.mf._model = FakeModel(0.30)     # < 0.45 threshold
        d = self.mf.should_veto(TAGS)
        self.assertTrue(d["veto"])
        self.assertEqual(d["p_win"], 0.3)

    def test_high_pwin_allows(self):
        config.ML_FILTER_ENABLED = True
        self.mf._model = FakeModel(0.80)
        self.assertFalse(self.mf.should_veto(TAGS)["veto"])


class TestCalibration(unittest.TestCase):
    def test_auto_disable_on_bad_calibration(self):
        config.ML_FILTER_ENABLED = True
        mf = mlf.MLFilter()
        mf._model = FakeModel(0.9)
        for _ in range(30):
            mf.record_outcome(0.9, 0)        # predicts 90% win, actually all losses
        self.assertTrue(mf.calibration()["disabled"])
        self.assertEqual(mf.should_veto(TAGS)["reason"], "auto_disabled_calibration")


class TestTrainingGate(unittest.TestCase):
    def setUp(self):
        config.JOURNAL_DB_FILE = os.path.join(_TMP, "journal_train.db")
        journal.init()
        self.mf = mlf.MLFilter()

    def test_cannot_train_without_enough_data(self):
        self.assertFalse(self.mf.can_train())
        res = self.mf.train()
        self.assertFalse(res["trained"])
        self.assertIn("trades", res["reason"])

    def test_report_is_safe_text(self):
        config.ML_FILTER_ENABLED = False
        self.assertIn("ML VETO FILTER", self.mf.report())


if __name__ == "__main__":
    unittest.main(verbosity=2)
