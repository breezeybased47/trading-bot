"""
journal_report.py  —  Module 8 analysis: weekly auto-analysis of the journal.

WHY
---
This is the point of the whole exercise. The journal records the conditions of
every trade; this turns that into plain-English answers:

  - win rate & expectancy by REGIME
  - best / worst TIME OF DAY
  - worst-performing TAG COMBINATIONS (e.g. "CHOPPY @ power_hour")
  - and most importantly: it flags any tag whose expectancy is negative with
    enough samples, and SUGGESTS a concrete filter rule you could turn on.

Expectancy = average $ P&L per trade. Each group also gets a t-stat
(mean / standard error) so you can tell a real signal from noise — a -$5
expectancy over 8 trades with t≈-0.4 is noise; the same over 40 trades with
t≈-3 is a habit worth blocking. Stdlib statistics only.
"""

import logging
import math
import statistics
from typing import Callable, List, Optional

import config
from modules import journal

logger = logging.getLogger(__name__)


def _agg(trades: List[dict], key_fn: Callable[[dict], Optional[str]]) -> dict:
    groups = {}
    for t in trades:
        k = key_fn(t)
        if k is None:
            continue
        groups.setdefault(k, []).append(float(t.get("pnl") or 0.0))
    out = {}
    for k, pnls in groups.items():
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        mean = sum(pnls) / n
        sd = statistics.stdev(pnls) if n >= 2 else 0.0
        t_stat = (mean / (sd / math.sqrt(n))) if sd > 0 else None
        out[k] = {"n": n, "wins": wins, "win_rate": wins / n, "expectancy": mean,
                  "total_pnl": sum(pnls), "std": sd, "t_stat": t_stat}
    return out


def analyze(trades: Optional[List[dict]] = None, min_sample: Optional[int] = None) -> dict:
    """Compute the full analysis dict from closed champion trades."""
    trades = journal.closed_trades() if trades is None else trades
    min_sample = min_sample or config.REPORT_MIN_SAMPLE
    if not trades:
        return {"n": 0}

    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)

    by_regime = _agg(trades, lambda t: t.get("regime") or "UNKNOWN")
    by_time = _agg(trades, lambda t: t.get("time_bucket") or "?")
    by_strategy = _agg(trades, lambda t: t.get("strategy") or "?")
    by_sizing = _agg(trades, lambda t: t.get("sizing_model") or "?")
    combos = _agg(trades, lambda t: "%s @ %s" % (t.get("regime") or "?", t.get("time_bucket") or "?"))

    # Flag negative-expectancy tags with enough evidence, suggest a filter.
    flags = []
    for dim, agg in (("regime", by_regime), ("time_of_day", by_time),
                     ("strategy", by_strategy), ("sizing", by_sizing)):
        for val, s in agg.items():
            if s["n"] >= min_sample and s["expectancy"] < 0:
                flags.append({
                    "dimension": dim, "value": val, "n": s["n"],
                    "expectancy": round(s["expectancy"], 2),
                    "win_rate": round(s["win_rate"], 3),
                    "t_stat": round(s["t_stat"], 2) if s["t_stat"] is not None else None,
                    "strength": _strength(s["t_stat"]),
                    "suggestion": "Consider avoiding %s=%s — avg $%.2f/trade over %d trades (win %.0f%%)"
                                  % (dim, val, s["expectancy"], s["n"], s["win_rate"] * 100),
                })
    flags.sort(key=lambda f: f["expectancy"])

    def _best_worst(agg):
        elig = {k: v for k, v in agg.items() if v["n"] >= min_sample} or agg
        if not elig:
            return (None, None)
        best = max(elig, key=lambda k: elig[k]["expectancy"])
        worst = min(elig, key=lambda k: elig[k]["expectancy"])
        return (best, worst)

    best_time, worst_time = _best_worst(by_time)
    worst_combos = sorted(
        [{"combo": k, **v} for k, v in combos.items() if v["n"] >= min_sample],
        key=lambda c: c["expectancy"])[:3]

    return {
        "n": n, "wins": wins, "win_rate": wins / n,
        "total_pnl": sum(pnls), "expectancy": sum(pnls) / n,
        "by_regime": by_regime, "by_time": by_time,
        "by_strategy": by_strategy, "by_sizing": by_sizing,
        "best_time": best_time, "worst_time": worst_time,
        "worst_combos": worst_combos, "flags": flags,
        "min_sample": min_sample,
    }


def _strength(t_stat) -> str:
    if t_stat is None:
        return "n/a"
    a = abs(t_stat)
    if a >= 2.5:
        return "strong"
    if a >= 1.5:
        return "moderate"
    return "weak/noise"


def text_report(analysis: Optional[dict] = None) -> str:
    a = analysis or analyze()
    if a.get("n", 0) == 0:
        return "📓 JOURNAL REPORT — no closed trades yet. Let it run and trade, then check back."

    L = []
    L.append("📓 JOURNAL REPORT  (%d trades, win %.0f%%, total $%.2f, expectancy $%.2f/trade)"
             % (a["n"], a["win_rate"] * 100, a["total_pnl"], a["expectancy"]))

    L.append("\nWin rate & expectancy by REGIME:")
    for k, s in sorted(a["by_regime"].items(), key=lambda kv: kv[1]["expectancy"], reverse=True):
        L.append("  %-14s n=%-3d win%% %3.0f  exp $%7.2f" % (k, s["n"], s["win_rate"] * 100, s["expectancy"]))

    L.append("\nBy TIME OF DAY:")
    for k, s in sorted(a["by_time"].items(), key=lambda kv: kv[1]["expectancy"], reverse=True):
        L.append("  %-12s n=%-3d win%% %3.0f  exp $%7.2f" % (k, s["n"], s["win_rate"] * 100, s["expectancy"]))
    if a["best_time"]:
        L.append("  → best: %s   worst: %s" % (a["best_time"], a["worst_time"]))

    if a["worst_combos"]:
        L.append("\nWorst tag COMBINATIONS (n≥%d):" % a["min_sample"])
        for c in a["worst_combos"]:
            L.append("  %-22s n=%-3d exp $%7.2f" % (c["combo"], c["n"], c["expectancy"]))

    L.append("\n⚑ SUGGESTED FILTERS (negative expectancy, n≥%d):" % a["min_sample"])
    if a["flags"]:
        for f in a["flags"]:
            L.append("  [%s] %s" % (f["strength"], f["suggestion"]))
    else:
        L.append("  none — nothing is losing money with enough evidence yet. ✅")

    return "\n".join(L)
