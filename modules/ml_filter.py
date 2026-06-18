"""
ml_filter.py  —  Module 10: ML confirmation filter (VETO-ONLY). Handle with care.

WHY (and the hard rules around it)
----------------------------------
This is the most dangerous module in the project, so it is the most constrained:

  * VETO-ONLY. It can only ever turn a "yes" into a "no". It NEVER generates a
    buy/sell signal. The strategies own entries; this just occasionally says
    "history says this exact setup is a coin-flip-loser, skip it."
  * It trains on YOUR OWN logged trades — features = journal entry tags, label =
    did the trade reach +target before its stop (journal.win). Garbage in,
    garbage out, so it stays OFF (ML_FILTER_ENABLED=False) until there are at
    least ML_MIN_TRAIN_TRADES (300) real trades.
  * Anti-overfitting: TIME-SERIES split only (never shuffle — that leaks the
    future), walk-forward retrain, feature-importance report, and live
    calibration tracking that AUTO-DISABLES the model if predicted win-rate
    drifts from actual by more than ML_CALIBRATION_TOLERANCE.
  * Fails OPEN. Missing model, missing library, too little data, or bad
    calibration => it allows the trade. It can never block trading by breaking.

Heavy ML libraries (lightgbm/xgboost, shap) are intentionally NOT added to
requirements — you have 0 of the 300 trades needed today. When you're ready:
`pip install lightgbm shap`, let it retrain, and flip ML_FILTER_ENABLED on.
"""

import json
import logging
import os
import pickle
import statistics
import threading
from typing import List, Optional

import config
from modules import journal
from modules import structured_log as slog

logger = logging.getLogger(__name__)

# Stable, explicit feature order so training and inference never misalign.
_REGIMES = ["TRENDING_UP", "TRENDING_DOWN", "VOLATILE_UP", "VOLATILE_DOWN", "CHOPPY"]
_TIME_BUCKETS = ["pre_market", "open_30", "morning", "midday", "afternoon",
                 "power_hour", "after_hours"]
FEATURE_NAMES = (["regime_" + r for r in _REGIMES]
                 + ["tod_" + b for b in _TIME_BUCKETS]
                 + ["rsi", "macd_hist", "ema9_minus_ema21", "spread_bps", "qty"])


def _num(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def features_from_tags(tags: dict) -> dict:
    """Build the (named) feature dict from a journal row / entry-tag dict."""
    signals = tags.get("signals") or {}
    if isinstance(signals, str):
        try:
            signals = json.loads(signals or "{}")
        except Exception:
            signals = {}
    f = {}
    for r in _REGIMES:
        f["regime_" + r] = 1.0 if tags.get("regime") == r else 0.0
    for b in _TIME_BUCKETS:
        f["tod_" + b] = 1.0 if tags.get("time_bucket") == b else 0.0
    ema9 = _num(signals.get("ema9"))
    ema21 = _num(signals.get("ema21"))
    f["rsi"] = _num(signals.get("rsi"))
    f["macd_hist"] = _num(signals.get("macd_hist"))
    f["ema9_minus_ema21"] = ema9 - ema21
    f["spread_bps"] = _num(tags.get("spread_bps"))
    f["qty"] = _num(tags.get("size_chosen") or tags.get("qty"))
    return f


def feature_vector(tags: dict) -> List[float]:
    f = features_from_tags(tags)
    return [f[name] for name in FEATURE_NAMES]


def _gbm_available() -> Optional[str]:
    try:
        import lightgbm  # noqa: F401
        return "lightgbm"
    except Exception:
        pass
    try:
        import xgboost  # noqa: F401
        return "xgboost"
    except Exception:
        pass
    return None


class MLFilter:
    def __init__(self):
        self._model = None
        self._feature_names = list(FEATURE_NAMES)
        self._disabled_by_calibration = False
        self._cal_pred: List[float] = []
        self._cal_actual: List[float] = []
        self._lock = threading.Lock()
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if os.path.exists(config.ML_MODEL_FILE):
                with open(config.ML_MODEL_FILE, "rb") as fh:
                    blob = pickle.load(fh)
                self._model = blob.get("model")
                self._feature_names = blob.get("features", FEATURE_NAMES)
                logger.info("ML model loaded from %s", config.ML_MODEL_FILE)
        except Exception as exc:
            logger.error("ML model load failed (ignoring): %s", exc)
            self._model = None

    def _save(self, importances=None) -> None:
        try:
            os.makedirs(os.path.dirname(config.ML_MODEL_FILE) or ".", exist_ok=True)
            with open(config.ML_MODEL_FILE, "wb") as fh:
                pickle.dump({"model": self._model, "features": self._feature_names,
                             "importances": importances}, fh)
        except Exception as exc:
            logger.error("ML model save failed: %s", exc)

    # ── Inference / veto ──────────────────────────────────────────────────────

    def predict_pwin(self, tags: dict) -> Optional[float]:
        if self._model is None:
            return None
        try:
            vec = [feature_vector(tags)]
            proba = self._model.predict_proba(vec)
            return float(proba[0][1])
        except Exception as exc:
            logger.error("ML predict failed (failing open): %s", exc)
            return None

    def should_veto(self, tags: dict) -> dict:
        """VETO-ONLY decision. Fails open in every uncertain case."""
        if not config.ML_FILTER_ENABLED:
            return {"veto": False, "p_win": None, "reason": "filter_off"}
        if self._disabled_by_calibration:
            return {"veto": False, "p_win": None, "reason": "auto_disabled_calibration"}
        p = self.predict_pwin(tags)
        if p is None:
            return {"veto": False, "p_win": None, "reason": "no_model"}
        if p < config.ML_VETO_THRESHOLD:
            slog.log_block("ml_filter", tags.get("ticker", "?"),
                           "P(win) %.2f < %.2f" % (p, config.ML_VETO_THRESHOLD),
                           p_win=round(p, 3))
            return {"veto": True, "p_win": round(p, 3), "reason": "low_pwin"}
        return {"veto": False, "p_win": round(p, 3), "reason": "pwin_ok"}

    def log_counterfactual(self, tags: dict, p_win: float, actual_win: Optional[int]) -> None:
        """Record what a vetoed trade WOULD have done — to verify the veto helped."""
        slog.log_event("ml_veto_counterfactual", ticker=tags.get("ticker", "?"),
                       p_win=round(p_win, 3), actual_win=actual_win)

    # ── Calibration (live vs predicted) ───────────────────────────────────────

    def record_outcome(self, predicted_pwin: float, actual_win: int) -> None:
        with self._lock:
            self._cal_pred.append(float(predicted_pwin))
            self._cal_actual.append(float(actual_win))
            if len(self._cal_actual) >= 30:
                gap = abs(statistics.mean(self._cal_pred[-100:])
                          - statistics.mean(self._cal_actual[-100:]))
                if gap > config.ML_CALIBRATION_TOLERANCE and not self._disabled_by_calibration:
                    self._disabled_by_calibration = True
                    logger.warning("ML AUTO-DISABLED — calibration gap %.2f > %.2f",
                                   gap, config.ML_CALIBRATION_TOLERANCE)
                    slog.log_event("ml_auto_disabled", calibration_gap=round(gap, 3))

    def calibration(self) -> dict:
        n = len(self._cal_actual)
        return {
            "n": n,
            "mean_predicted": round(statistics.mean(self._cal_pred), 3) if n else None,
            "mean_actual": round(statistics.mean(self._cal_actual), 3) if n else None,
            "disabled": self._disabled_by_calibration,
        }

    # ── Training (walk-forward, time-series only) ─────────────────────────────

    def can_train(self) -> bool:
        return journal.count() >= config.ML_MIN_TRAIN_TRADES and _gbm_available() is not None

    def train(self) -> dict:
        n = journal.count()
        if n < config.ML_MIN_TRAIN_TRADES:
            return {"trained": False,
                    "reason": "need %d trades, have %d" % (config.ML_MIN_TRAIN_TRADES, n)}
        lib = _gbm_available()
        if lib is None:
            return {"trained": False, "reason": "no GBM library — pip install lightgbm shap"}
        try:
            rows = journal.closed_trades()                  # newest first
            rows = [r for r in rows if r.get("win") is not None]
            rows.sort(key=lambda r: r.get("entry_ts") or "")  # TIME ORDER — never shuffle
            X = [feature_vector(r) for r in rows]
            y = [int(r["win"]) for r in rows]
            split = int(len(X) * 0.8)                        # train past, validate future
            X_tr, y_tr, X_val, y_val = X[:split], y[:split], X[split:], y[split:]

            if lib == "lightgbm":
                import lightgbm as gbm
                model = gbm.LGBMClassifier(n_estimators=200, max_depth=4, learning_rate=0.05)
            else:
                import xgboost as gbm
                model = gbm.XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                                          use_label_encoder=False, eval_metric="logloss")
            model.fit(X_tr, y_tr)
            self._model = model
            importances = dict(zip(self._feature_names, getattr(model, "feature_importances_", [])))
            self._save(importances)
            val_acc = (sum(1 for p, a in zip(model.predict(X_val), y_val) if p == a) / len(y_val)
                       if y_val else None)
            slog.log_event("ml_trained", lib=lib, n=len(X), val_n=len(X_val), val_acc=val_acc)
            return {"trained": True, "lib": lib, "n": len(X), "val_n": len(X_val),
                    "val_accuracy": val_acc, "top_features": _top(importances)}
        except Exception as exc:
            logger.error("ML train failed: %s", exc)
            return {"trained": False, "reason": "exception: %s" % exc}

    def report(self) -> str:
        n = journal.count()
        lib = _gbm_available()
        L = ["🤖 ML VETO FILTER"]
        L.append("  enabled: %s | model loaded: %s | library: %s"
                 % (config.ML_FILTER_ENABLED, self._model is not None, lib or "none (pip install lightgbm)"))
        L.append("  trades available: %d / %d required to train" % (n, config.ML_MIN_TRAIN_TRADES))
        cal = self.calibration()
        if cal["n"]:
            L.append("  calibration: predicted %.2f vs actual %.2f over %d (%s)"
                     % (cal["mean_predicted"], cal["mean_actual"], cal["n"],
                        "DISABLED" if cal["disabled"] else "ok"))
        if not config.ML_FILTER_ENABLED:
            L.append("  → currently inert (filter_off) — safe. Veto-only by design.")
        return "\n".join(L)


def _top(importances: dict, k: int = 5):
    if not importances:
        return []
    return sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:k]
