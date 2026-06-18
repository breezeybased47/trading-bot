"""
latency_monitor.py  —  Module 11: latency & execution-quality monitor.

WHY
---
The exit engine reacts on sub-second order-flow, but that precision is a fantasy
if it takes 3 seconds to actually get filled. This monitor timestamps every stage
of an order —

    signal generated -> order submitted -> order acknowledged -> order filled

— tracks the round-trip latency distribution, and alerts when p95 blows past
LATENCY_P95_ALERT_MS. Crucially, if latency degrades it tells the exit engine to
WIDEN its thresholds (a slow bot should be less twitchy — reacting fast is
pointless and harmful when your fills lag), via exit_threshold_multiplier().

Pure percentile math is unit tested; timestamps are injected so tests never wait.
"""

import logging
import threading
import time
from collections import deque
from typing import Optional

import config
from modules import structured_log as slog
from modules.liquidity_guard import _append_jsonl

logger = logging.getLogger(__name__)


def _percentile(sorted_vals, q: float) -> Optional[float]:
    """Linear-interpolation percentile (q in [0,1]). Expects a sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


class LatencyMonitor:
    def __init__(self, maxlen: int = 500):
        self._roundtrips = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._alerted = False

    def record(self, signal_ts: float, submit_ts: float,
               ack_ts: Optional[float], fill_ts: Optional[float],
               ticker: Optional[str] = None) -> dict:
        """Record one order's stage timestamps (epoch seconds). Returns the stage durations (ms)."""
        def ms(a, b):
            return round((b - a) * 1000.0, 1) if (a is not None and b is not None) else None

        stages = {
            "ticker": ticker,
            "signal_to_submit_ms": ms(signal_ts, submit_ts),
            "submit_to_ack_ms": ms(submit_ts, ack_ts),
            "ack_to_fill_ms": ms(ack_ts, fill_ts),
            "roundtrip_ms": ms(signal_ts, fill_ts),
        }
        rt = stages["roundtrip_ms"]
        if rt is not None:
            with self._lock:
                self._roundtrips.append(rt)
        slog.log_event("latency", **stages)
        _append_jsonl(config.LATENCY_LOG_FILE, stages)

        if self.is_degraded() and not self._alerted:
            self._alerted = True
            p = self.percentiles()
            logger.warning("LATENCY DEGRADED: p95 %.0fms > %dms threshold — exits widened x%.2f",
                           p.get("p95") or 0, config.LATENCY_P95_ALERT_MS, self.exit_threshold_multiplier())
        elif not self.is_degraded():
            self._alerted = False
        return stages

    def percentiles(self) -> dict:
        with self._lock:
            vals = sorted(self._roundtrips)
        return {
            "n": len(vals),
            "p50": _round(_percentile(vals, 0.50)),
            "p95": _round(_percentile(vals, 0.95)),
            "p99": _round(_percentile(vals, 0.99)),
            "max": _round(vals[-1]) if vals else None,
        }

    def is_degraded(self) -> bool:
        p95 = self.percentiles().get("p95")
        return p95 is not None and p95 > config.LATENCY_P95_ALERT_MS

    def exit_threshold_multiplier(self) -> float:
        """>1 when latency is degraded so the exit engine reacts less aggressively."""
        if config.LATENCY_WIDEN_EXIT_ON_DEGRADE and self.is_degraded():
            return float(config.LATENCY_EXIT_WIDEN_MULT)
        return 1.0

    def daily_report(self) -> str:
        p = self.percentiles()
        if not p["n"]:
            return "⏱  LATENCY — no fills recorded yet."
        flag = "  ⚠️ DEGRADED" if self.is_degraded() else "  ✅"
        return ("⏱  EXECUTION QUALITY  (n=%d fills)\n"
                "   roundtrip p50 %sms | p95 %sms | p99 %sms | max %sms%s\n"
                "   exit threshold multiplier: x%.2f"
                % (p["n"], p["p50"], p["p95"], p["p99"], p["max"], flag,
                   self.exit_threshold_multiplier()))


def _round(v):
    return round(v, 1) if v is not None else None


# Convenience for wiring: a context-style stopwatch using wall clock.
def now() -> float:
    return time.time()
