"""
shadow_engine.py  —  Module 9: shadow mode (champion / challenger).

WHY
---
The safest way to evolve a live strategy is to let variants compete on the SAME
live tape without risking a cent. The running bot is the "champion"; each
"challenger" is the champion with one config change (a different RSI threshold,
scaling on, the regime filter on, ...). Challengers NEVER send orders — they only
paper-log what they WOULD have done, so after a meaningful sample you can see
whether a change actually helps before you ever flip it on for real.

HOW (honest about the method)
-----------------------------
When the champion closes a trade, that trade — with its full context and price
path (entry, exit, pnl, max-favorable-excursion) — is handed to the shadow
engine on a SEPARATE THREAD (a queue keeps the trading hot-path fast). Each
challenger then answers two questions as a counterfactual on that trade:
  1. would I have TAKEN it?  (entry-rule / regime-filter difference)
  2. what P&L would MY exits have produced?  (e.g. scaling banks partial gains)
It reuses the real config thresholds and the scaling tiers — no strategy logic is
forked. Known limit: it can't invent trades the champion never took, so it best
evaluates variants that are a subset (tighter entry) or differ only on exits.

Decisions are advisory: recommendation() flags a winner but NEVER auto-switches.
"""

import logging
import queue
import statistics
import threading
from typing import List, Optional

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)


class ShadowEngine:
    def __init__(self, challengers: Optional[List[dict]] = None):
        self._challengers = challengers if challengers is not None else config.SHADOW_CHALLENGERS
        self._tallies = {c["name"]: [] for c in self._challengers}  # name -> [pnl,...]
        self._champion_pnls: List[float] = []
        self._lock = threading.Lock()
        self._q: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ── Threaded intake (keeps the trading hot-path fast) ─────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, name="shadow-engine", daemon=True)
        self._thread.start()
        logger.info("Shadow engine started with challengers: %s",
                    [c["name"] for c in self._challengers])

    def stop(self) -> None:
        self._running = False
        self._q.put(None)  # unblock the worker
        if self._thread:
            self._thread.join(timeout=2)

    def submit(self, trade: dict) -> None:
        """Hot-path call from the champion: enqueue a closed trade, return instantly."""
        self._q.put(trade)

    def _worker(self) -> None:
        while self._running:
            try:
                trade = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if trade is None:
                break
            try:
                self.on_champion_trade(trade)
            except Exception as exc:
                logger.error("shadow worker error: %s", exc)

    # ── Core counterfactual (synchronous & unit tested) ───────────────────────

    def on_champion_trade(self, trade: dict) -> dict:
        pnl = float(trade.get("pnl") or 0.0)
        with self._lock:
            self._champion_pnls.append(pnl)
        results = {}
        for c in self._challengers:
            d = self._challenger_decision(c, trade)
            results[c["name"]] = d
            if d["take"]:
                with self._lock:
                    self._tallies[c["name"]].append(d["pnl"])
                slog.log_event("shadow_trade", challenger=c["name"],
                               ticker=trade.get("ticker"), pnl=round(d["pnl"], 2))
            else:
                slog.log_event("shadow_skip", challenger=c["name"],
                               ticker=trade.get("ticker"), reason=d["reason"])
        return results

    def _challenger_decision(self, c: dict, trade: dict) -> dict:
        signals = trade.get("signals") or {}

        # 1) entry difference
        if "RSI_BUY" in c:
            rsi, rsi_prev = signals.get("rsi"), signals.get("rsi_prev")
            hist = signals.get("macd_hist") or 0
            thr = c["RSI_BUY"]
            take = (rsi is not None and rsi_prev is not None
                    and rsi_prev < thr <= rsi and hist > 0)
            if not take:
                return {"take": False, "pnl": 0.0, "reason": "rsi_cross_%s_not_met" % thr}
        elif c.get("REGIME_FILTER_ENABLED"):
            if trade.get("regime_blocked"):
                return {"take": False, "pnl": 0.0, "reason": "regime_blocked"}

        # 2) exit difference
        if c.get("SCALING_ENABLED"):
            return {"take": True, "pnl": self._scaled_pnl(trade), "reason": "scaled"}
        return {"take": True, "pnl": float(trade.get("pnl") or 0.0), "reason": "same_as_champion"}

    @staticmethod
    def _scaled_pnl(trade: dict) -> float:
        """Estimate the trade's P&L under the scaling-out rules, from its excursion."""
        entry = trade.get("entry_price") or trade.get("entry")
        qty = trade.get("qty") or 0
        mfe = trade.get("max_favorable_pct")
        final = trade.get("pnl_pct")
        if not entry or not qty or final is None:
            return float(trade.get("pnl") or 0.0)

        t1, t2 = config.SCALE_TIER1_TRIGGER_PCT, config.SCALE_TIER2_TRIGGER_PCT
        f1, f2 = config.SCALE_TIER1_SELL_FRAC, config.SCALE_TIER2_SELL_FRAC
        if mfe is None or mfe < t1:
            return float(trade.get("pnl") or 0.0)            # never scaled
        if mfe >= t2:
            rem = 1 - f1 - f2
            blended = f1 * t1 + f2 * t2 + rem * final
        else:
            rem = 1 - f1
            blended = f1 * t1 + rem * max(final, 0.0)        # stop moved to breakeven
        return blended * entry * qty

    # ── Comparison & recommendation ───────────────────────────────────────────

    @staticmethod
    def _stats(pnls: List[float]) -> dict:
        n = len(pnls)
        total = sum(pnls)
        mean = total / n if n else 0.0
        sd = statistics.stdev(pnls) if n >= 2 else 0.0
        wins = sum(1 for p in pnls if p > 0)
        return {"n": n, "total": round(total, 2), "expectancy": round(mean, 2),
                "win_rate": round(wins / n, 3) if n else 0.0,
                "sharpe": round(mean / sd, 3) if sd > 0 else None}

    def comparison(self) -> dict:
        with self._lock:
            out = {"champion": self._stats(self._champion_pnls)}
            for name, pnls in self._tallies.items():
                out[name] = self._stats(pnls)
        return out

    def recommendation(self, min_sample: int = 20) -> List[str]:
        comp = self.comparison()
        champ = comp["champion"]
        recs = []
        for name, s in comp.items():
            if name == "champion":
                continue
            beats_total = s["total"] > champ["total"]
            beats_risk = (s["sharpe"] or -9) > (champ["sharpe"] or -9)
            if s["n"] >= min_sample and beats_total and beats_risk:
                recs.append("Challenger '%s' beat champion: $%.2f vs $%.2f, "
                            "sharpe %s vs %s over %d trades — REVIEW (no auto-switch)."
                            % (name, s["total"], champ["total"], s["sharpe"], champ["sharpe"], s["n"]))
        return recs

    def report(self) -> str:
        comp = self.comparison()
        L = ["🥊 CHAMPION vs CHALLENGERS"]
        champ = comp["champion"]
        L.append("  champion     n=%-3d total $%-9.2f exp $%-7.2f win%% %3.0f sharpe %s"
                 % (champ["n"], champ["total"], champ["expectancy"], champ["win_rate"] * 100, champ["sharpe"]))
        for name, s in comp.items():
            if name == "champion":
                continue
            L.append("  %-12s n=%-3d total $%-9.2f exp $%-7.2f win%% %3.0f sharpe %s"
                     % (name, s["n"], s["total"], s["expectancy"], s["win_rate"] * 100, s["sharpe"]))
        for r in self.recommendation():
            L.append("  ⭐ " + r)
        return "\n".join(L)
