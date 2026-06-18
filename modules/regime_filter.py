"""
regime_filter.py  —  Module 1: the master switch on entries.

WHY
---
If the bot has *historically lost money* in the regime the market is in right
now, the single most profitable thing it can do is not trade. This filter sits
in front of every entry: it classifies the live market every few minutes, looks
up the bot's own realised expectancy in that regime (from the journal), and
BLOCKS new entries when that expectancy is negative — while never touching any
existing safety rail.

Two safeguards against fooling ourselves:
  - Minimum sample: a regime needs REGIME_MIN_SAMPLE closed trades before its
    expectancy is trusted. Until then the bot trades at HALF size, not zero, so
    it can actually gather the evidence.
  - Toggle: REGIME_FILTER_ENABLED lets you A/B the bot with and without the
    filter on the very same live tape.

It maintains data/regime_performance.json as a fast, human-readable snapshot of
per-regime stats (the journal remains the source of truth).
"""

import json
import logging
import os
import threading
import time
from typing import Optional

import config
from modules import journal, market_classifier
from modules import structured_log as slog

logger = logging.getLogger(__name__)


class RegimeFilter:
    def __init__(self, data_feed):
        self._feed = data_feed
        self._regime = market_classifier.CHOPPY
        self._detail = {"regime": self._regime, "reason": "startup"}
        self._last_classified = 0.0
        self._lock = threading.Lock()

    # ── Classification (self-throttling to every REGIME_RECLASSIFY_SECONDS) ────

    def reclassify_if_due(self, now: Optional[float] = None) -> str:
        now = now if now is not None else time.time()
        if now - self._last_classified < config.REGIME_RECLASSIFY_SECONDS:
            return self._regime
        return self.reclassify(now)

    def reclassify(self, now: Optional[float] = None) -> str:
        """Classify the market off the NASDAQ proxy (QQQ) candle history."""
        qqq = self._feed.get_candles(config.QQQ_TICKER)
        detail = market_classifier.classify_detail(qqq)
        with self._lock:
            prev = self._regime
            self._regime = detail["regime"]
            self._detail = detail
            self._last_classified = now if now is not None else time.time()
        if detail["regime"] != prev:
            slog.log_event("regime_change", to=detail["regime"], frm=prev,
                           trend=detail.get("trend"), vol_ratio=detail.get("vol_ratio"))
            logger.info("Regime: %s -> %s (trend=%s vol_ratio=%s)",
                        prev, detail["regime"], detail.get("trend"), detail.get("vol_ratio"))
        return self._regime

    def current(self) -> str:
        return self._regime

    # ── The master switch ─────────────────────────────────────────────────────

    def entry_decision(self, ticker: str, regime: Optional[str] = None) -> dict:
        """
        Decide whether an entry is allowed in the current (or given) regime.
        Returns {allow, size_mult, reason, regime, expectancy, sample}.
        size_mult is multiplied into the position size by the sizer.
        """
        regime = regime or self._regime
        stats = journal.regime_stats().get(regime, {})
        sample = int(stats.get("n", 0))
        expectancy = float(stats.get("expectancy", 0.0))

        base = {"regime": regime, "expectancy": expectancy, "sample": sample}

        if not config.REGIME_FILTER_ENABLED:
            return {"allow": True, "size_mult": 1.0, "reason": "filter_off", **base}

        # Not enough evidence yet → trade small, keep learning.
        if sample < config.REGIME_MIN_SAMPLE:
            mult = 0.5 if config.REGIME_HALF_SIZE_UNTIL_TRUSTED else 1.0
            reason = "insufficient_sample(%d<%d)_half_size" % (sample, config.REGIME_MIN_SAMPLE)
            if mult != 1.0:
                slog.log_decision("regime_half_size", ticker, regime=regime, sample=sample)
            return {"allow": True, "size_mult": mult, "reason": reason, **base}

        # Enough evidence and it's negative → BLOCK.
        if expectancy < config.REGIME_MIN_EXPECTANCY:
            reason = "REGIME BLOCKED: expectancy $%.2f < $%.2f in %s" % (
                expectancy, config.REGIME_MIN_EXPECTANCY, regime)
            slog.log_block("regime", ticker, reason, regime=regime,
                           expectancy=expectancy, sample=sample)
            return {"allow": False, "size_mult": 0.0, "reason": reason, **base}

        return {"allow": True, "size_mult": 1.0, "reason": "regime_ok", **base}

    # ── Snapshot for dashboard / external tools ───────────────────────────────

    def update_performance_snapshot(self) -> dict:
        """Recompute per-regime stats from the journal and persist a JSON snapshot."""
        snapshot = {
            "updated": _now_iso(),
            "current_regime": self._regime,
            "filter_enabled": config.REGIME_FILTER_ENABLED,
            "min_sample": config.REGIME_MIN_SAMPLE,
            "min_expectancy": config.REGIME_MIN_EXPECTANCY,
            "regimes": journal.regime_stats(),
        }
        try:
            os.makedirs(os.path.dirname(config.REGIME_PERF_FILE) or ".", exist_ok=True)
            with open(config.REGIME_PERF_FILE, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)
        except Exception as exc:
            logger.error("regime snapshot write failed: %s", exc)
        return snapshot

    def status(self) -> dict:
        """Live status for the dashboard (READ-ONLY — never logs, so it's safe
        to poll on the 2s dashboard refresh)."""
        regime = self._regime
        stats = journal.regime_stats().get(regime, {})
        sample = int(stats.get("n", 0))
        expectancy = float(stats.get("expectancy", 0.0))
        blocked = (config.REGIME_FILTER_ENABLED
                   and sample >= config.REGIME_MIN_SAMPLE
                   and expectancy < config.REGIME_MIN_EXPECTANCY)
        return {
            "regime": regime,
            "detail": self._detail,
            "blocked": blocked,
            "expectancy": expectancy,
            "sample": sample,
            "enabled": config.REGIME_FILTER_ENABLED,
        }


def _now_iso() -> str:
    import pytz
    from datetime import datetime
    return datetime.now(pytz.timezone("America/New_York")).isoformat()
