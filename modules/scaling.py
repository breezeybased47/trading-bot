"""
scaling.py  —  Module 6: partial profit taking / scaling out.

WHY
---
Round-trip "all in / all out" trading throws away the most reliable edge a
momentum book has: banking part of a winner reduces variance hugely while still
letting a runner run. The plan:

  +1.5% unrealised : sell 50%, move the stop to BREAKEVEN on the rest
                     (the trade is now risk-free — worst case is scratch)
  +3.0% unrealised : sell another 25%, TIGHTEN the trailing stop on the last 25%
  last 25%         : rides the existing exit engine + tightened trail

Each partial is logged separately so the journal/backtest can measure whether
scaling actually improved total return for THIS strategy, or just capped winners.
Gated by SCALING_ENABLED (default OFF) for clean A/B testing. Order execution and
the actual stop move happen in the integration pass; this module is the pure,
tested decision layer.
"""

import logging
from typing import Optional, Set

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)


def decide(entry: float, current_price: float, original_qty: int,
           remaining_qty: int, tiers_hit: Set[str]) -> dict:
    """
    Pure scaling decision. Returns an action dict:
      {"action": "none"|"sell_partial", "tier", "sell_qty", "new_stop",
       "stop_type", "pnl_pct", "reason"}.
    Only ONE tier is actioned per call; tier1 always precedes tier2 (a gap
    straight to +3% still banks the 50% first, then tightens next tick).
    """
    if not config.SCALING_ENABLED:
        return {"action": "none", "reason": "filter_off"}
    if entry <= 0 or remaining_qty <= 0 or original_qty <= 0:
        return {"action": "none", "reason": "empty_position"}

    pnl_pct = (current_price - entry) / entry

    if "tier1" not in tiers_hit and pnl_pct >= config.SCALE_TIER1_TRIGGER_PCT:
        qty = min(remaining_qty, int(round(original_qty * config.SCALE_TIER1_SELL_FRAC)))
        if qty < 1:
            return {"action": "none", "reason": "tier1_qty<1"}
        return {"action": "sell_partial", "tier": "tier1", "sell_qty": qty,
                "new_stop": round(entry, 2), "stop_type": "breakeven",
                "pnl_pct": round(pnl_pct, 4),
                "reason": "+%.2f%% -> tier1: sell %d (%.0f%%), stop to breakeven"
                          % (pnl_pct * 100, qty, config.SCALE_TIER1_SELL_FRAC * 100)}

    if "tier2" not in tiers_hit and pnl_pct >= config.SCALE_TIER2_TRIGGER_PCT:
        qty = min(remaining_qty, int(round(original_qty * config.SCALE_TIER2_SELL_FRAC)))
        if qty < 1:
            return {"action": "none", "reason": "tier2_qty<1"}
        new_stop = current_price * (1 - config.SCALE_TIER2_TRAIL_PCT)
        return {"action": "sell_partial", "tier": "tier2", "sell_qty": qty,
                "new_stop": round(new_stop, 2), "stop_type": "tight_trail",
                "pnl_pct": round(pnl_pct, 4),
                "reason": "+%.2f%% -> tier2: sell %d (%.0f%%), tighten trail to %.2f"
                          % (pnl_pct * 100, qty, config.SCALE_TIER2_SELL_FRAC * 100, new_stop)}

    return {"action": "none", "reason": "no_tier"}


class ScalingManager:
    """Tracks original size and which tiers have fired, per open position."""

    def __init__(self):
        self._state = {}   # ticker -> {"original_qty": int, "tiers": set()}

    def on_open(self, ticker: str, qty: int) -> None:
        self._state[ticker] = {"original_qty": qty, "tiers": set()}

    def on_close(self, ticker: str) -> None:
        self._state.pop(ticker, None)

    def tiers_hit(self, ticker: str) -> Set[str]:
        st = self._state.get(ticker)
        return set(st["tiers"]) if st else set()

    def check(self, ticker: str, entry: float, current_price: float,
              remaining_qty: int) -> dict:
        st = self._state.get(ticker)
        if not st:
            return {"action": "none", "reason": "untracked"}
        action = decide(entry, current_price, st["original_qty"], remaining_qty, st["tiers"])
        if action["action"] == "sell_partial":
            st["tiers"].add(action["tier"])
            slog.log_event("partial_exit", ticker=ticker, tier=action["tier"],
                           sell_qty=action["sell_qty"], new_stop=action["new_stop"],
                           stop_type=action["stop_type"], pnl_pct=action["pnl_pct"])
            logger.info("PARTIAL %s | %s", ticker, action["reason"])
        return action
