"""
position_sizer.py  —  Module 2: dynamic position sizing.

WHY
---
Fixed 10%-of-equity sizing risks wildly different dollar amounts depending on how
volatile a name is, and ignores how well the strategy is actually doing. Two
better models, selectable via SIZING_MODEL:

  vol_adjusted : target a CONSTANT dollar-risk per trade. Size = risk$ / stop$,
                 where the stop distance widens with ATR — so a name that is
                 unusually volatile right now is sized DOWN to keep risk flat.
  kelly        : bet more when the edge is real. Fractional (half) Kelly from the
                 bot's own rolling win rate and win/loss ratio. Full Kelly is far
                 too aggressive, so we scale it down and cap hard.

SAFETY (these are non-negotiable and always applied last)
  - never exceed MAX_POSITION_PCT (10%) per position
  - never exceed MAX_TOTAL_EXPOSURE_PCT (25%) across all positions
  - halve size after CONSEC_LOSS_TRIGGER consecutive losses
  - Kelly falls back to vol_adjusted below KELLY_MIN_SAMPLE trades
  - every size decision logs its full reasoning (structured + trade log)

The core `compute_size` is a pure function (no I/O) so it is exhaustively unit
tested; the `PositionSizer` class just feeds it live numbers.
"""

import logging
from typing import Optional

import config
from modules import indicators, journal
from modules import structured_log as slog

logger = logging.getLogger(__name__)


# ── Pure core (no I/O — fully unit tested) ────────────────────────────────────

def _vol_adjusted_dollars(price: float, equity: float,
                          atr: Optional[float], atr_avg: Optional[float]):
    """Constant dollar-risk sizing. Returns (dollars, note)."""
    risk_dollars = equity * config.ACCOUNT_RISK_PER_TRADE_PCT
    hard_stop_dist = price * config.STOP_LOSS_PCT          # the REAL stop the bot uses
    atr_dist = (config.ATR_STOP_MULT * atr) if atr else 0.0
    # Size against the WIDER of the two so we never under-stop and over-risk.
    stop_dist = max(hard_stop_dist, atr_dist)
    if stop_dist <= 0:
        return (equity * config.MAX_POSITION_PCT, "vol_adjusted: no stop distance, used max%")
    shares = risk_dollars / stop_dist
    dollars = shares * price
    vr = ("%.2f" % (atr / atr_avg)) if (atr and atr_avg) else "n/a"
    note = ("vol_adjusted: risk $%.2f / stop $%.3f -> $%.2f (ATR=%s, ATR/avg=%s)"
            % (risk_dollars, stop_dist, dollars, ("%.3f" % atr) if atr else "n/a", vr))
    return (dollars, note)


def _kelly_dollars(equity: float, win_rate: float, win_loss_ratio: float):
    """Fractional-Kelly dollar allocation. Returns (dollars, note)."""
    if win_loss_ratio <= 0:  # undefined edge (no losses yet) — stay conservative
        d = equity * config.ACCOUNT_RISK_PER_TRADE_PCT
        return (d, "kelly: R undefined, used risk%% ($%.2f)" % d)
    f = win_rate - (1.0 - win_rate) / win_loss_ratio      # Kelly fraction f*
    f = max(0.0, f)                                        # no edge -> no bet
    frac = f * config.KELLY_FRACTION                       # half/quarter Kelly
    dollars = equity * frac
    note = ("kelly f*=%.3f x%.2f -> %.1f%% ($%.2f) [W=%.2f R=%.2f]"
            % (f, config.KELLY_FRACTION, frac * 100, dollars, win_rate, win_loss_ratio))
    return (dollars, note)


def compute_size(model: str, price: float, equity: float,
                 atr: Optional[float] = None, atr_avg: Optional[float] = None,
                 current_exposure_value: float = 0.0,
                 win_rate: float = 0.0, win_loss_ratio: float = 0.0, sample: int = 0,
                 consecutive_losses: int = 0, regime_size_mult: float = 1.0) -> dict:
    """
    Pure sizing decision. Returns a dict with qty, the model actually used,
    dollar allocation, and a human-readable reasoning string.
    """
    if price <= 0 or equity <= 0:
        return {"qty": 0, "model_used": model, "dollars": 0.0,
                "reasoning": "invalid price/equity (price=%s equity=%s)" % (price, equity)}

    notes = []
    model_used = model

    if model == "vol_adjusted":
        dollars, note = _vol_adjusted_dollars(price, equity, atr, atr_avg)
        notes.append(note)
    elif model == "kelly":
        if sample < config.KELLY_MIN_SAMPLE:
            model_used = "vol_adjusted"
            notes.append("kelly sample %d<%d -> fallback vol_adjusted"
                         % (sample, config.KELLY_MIN_SAMPLE))
            dollars, note = _vol_adjusted_dollars(price, equity, atr, atr_avg)
            notes.append(note)
        else:
            dollars, note = _kelly_dollars(equity, win_rate, win_loss_ratio)
            notes.append(note)
    else:
        model_used = "fixed"
        dollars = equity * config.MAX_POSITION_PCT
        notes.append("fixed %.0f%% of equity = $%.2f" % (config.MAX_POSITION_PCT * 100, dollars))

    # ── multipliers ──
    if regime_size_mult != 1.0:
        dollars *= regime_size_mult
        notes.append("regime x%.2f" % regime_size_mult)
    if consecutive_losses >= config.CONSEC_LOSS_TRIGGER:
        dollars *= config.CONSEC_LOSS_SIZE_FACTOR
        notes.append("%d consec losses x%.2f" % (consecutive_losses, config.CONSEC_LOSS_SIZE_FACTOR))

    # ── hard caps (always last) ──
    max_pos_dollars = equity * config.MAX_POSITION_PCT
    if dollars > max_pos_dollars:
        dollars = max_pos_dollars
        notes.append("capped @ %.0f%% max position" % (config.MAX_POSITION_PCT * 100))

    room = equity * config.MAX_TOTAL_EXPOSURE_PCT - current_exposure_value
    if room <= 0:
        notes.append("BLOCKED: %.0f%% total-exposure cap reached" % (config.MAX_TOTAL_EXPOSURE_PCT * 100))
        return {"qty": 0, "model_used": model_used, "dollars": 0.0,
                "reasoning": "; ".join(notes)}
    if dollars > room:
        dollars = room
        notes.append("capped by total-exposure room $%.2f" % room)

    qty = int(dollars / price)
    if qty < 1:
        notes.append("computed <1 share -> no trade")
        qty = 0

    reasoning = "%s -> %d sh @ $%.2f ($%.2f) | %s" % (model_used, qty, price, dollars, "; ".join(notes))
    return {"qty": qty, "model_used": model_used, "dollars": dollars,
            "reasoning": reasoning, "win_rate": win_rate,
            "win_loss_ratio": win_loss_ratio, "sample": sample}


# ── Live wrapper ──────────────────────────────────────────────────────────────

class PositionSizer:
    """Feeds live broker / feed / journal numbers into compute_size()."""

    def __init__(self, broker, data_feed):
        self._broker = broker
        self._feed = data_feed

    def size(self, ticker: str, price: float, regime_size_mult: float = 1.0,
             candles=None) -> dict:
        equity = self._broker.portfolio_value()
        try:
            exposure = sum(abs(p.get("market_value", 0.0)) for p in self._broker.open_positions())
        except Exception:
            exposure = 0.0
        candles = candles if candles is not None else self._feed.get_candles(ticker)
        atr, atr_avg = indicators.atr_with_average(candles)
        win_rate, ratio, sample = journal.recent_winrate_and_ratio(config.KELLY_LOOKBACK)
        consec = journal.consecutive_losses()

        result = compute_size(
            config.SIZING_MODEL, price, equity,
            atr=atr, atr_avg=atr_avg, current_exposure_value=exposure,
            win_rate=win_rate, win_loss_ratio=ratio, sample=sample,
            consecutive_losses=consec, regime_size_mult=regime_size_mult,
        )

        slog.log_event("size_decision", ticker=ticker, qty=result["qty"],
                       model=result["model_used"], dollars=round(result["dollars"], 2),
                       reasoning=result["reasoning"])
        logger.info("SIZE %s | %s", ticker, result["reasoning"])
        return result
