"""
risk_manager.py
Enforces ALL risk rules:
  - Max position size (10% of portfolio)
  - Hard stop loss (2% below entry)
  - Trailing stop (1.5%, activates when up 1%)
  - Daily loss limit (halt at -5%)
  - Max 3 concurrent positions
  - Market hours gating
  - Force-close leveraged ETFs by 3:45 PM
"""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import pytz

from config import (
    DAILY_LOSS_LIMIT_PCT, LEVERAGED_ETF_CLOSE_TIME, LEVERAGED_ETFS,
    MARKET_CLOSE, MARKET_OPEN, MAX_OPEN_POSITIONS, MAX_POSITION_PCT,
    STOP_LOSS_PCT, TRAILING_STOP_PCT, TRAILING_STOP_TRIGGER,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


class Position:
    """Tracks a single live position and manages its stop levels."""

    def __init__(self, ticker: str, entry: float, qty: int, strategy: str):
        self.ticker   = ticker
        self.entry    = entry
        self.qty      = qty
        self.strategy = strategy
        self.opened   = datetime.now(ET)

        self.hard_stop       = entry * (1 - STOP_LOSS_PCT)
        self.trailing_stop:  Optional[float] = None
        self.trailing_active = False
        self.peak_price      = entry
        self.current_price   = entry

    @property
    def pnl(self) -> float:
        return (self.current_price - self.entry) * self.qty

    @property
    def pnl_pct(self) -> float:
        return (self.current_price - self.entry) / self.entry

    def tick(self, price: float):
        """Update current price and ratchet trailing stop upward."""
        self.current_price = price

        # Activate trailing stop once we're up enough
        if not self.trailing_active and self.pnl_pct >= TRAILING_STOP_TRIGGER:
            self.trailing_active = True
            self.trailing_stop = price * (1 - TRAILING_STOP_PCT)
            logger.info(f"Trailing stop ON for {self.ticker} @ {self.trailing_stop:.2f}")

        # Ratchet upward — never move it down
        if self.trailing_active and price > self.peak_price:
            self.peak_price    = price
            self.trailing_stop = price * (1 - TRAILING_STOP_PCT)

    def stop_triggered(self) -> Tuple[bool, str]:
        """Return (triggered, reason) if any stop level was hit."""
        if self.current_price <= self.hard_stop:
            return True, f"Hard stop @ {self.hard_stop:.2f} (entry {self.entry:.2f})"
        if self.trailing_active and self.trailing_stop and self.current_price <= self.trailing_stop:
            return True, f"Trailing stop @ {self.trailing_stop:.2f}"
        return False, ""

    def __repr__(self):
        return (f"<Position {self.ticker} x{self.qty} "
                f"entry={self.entry:.2f} now={self.current_price:.2f} "
                f"pnl={self.pnl:+.2f}>")


class RiskManager:
    """Gate-keeper for every order the bot wants to place."""

    def __init__(self, broker):
        self._broker              = broker
        self.positions:    Dict[str, Position] = {}
        self.halted:       bool  = False        # True when daily loss limit hit
        self._day:         Optional[date] = None
        self._day_start_value: Optional[float] = None

    # ── Market / session guards ───────────────────────────────────────────────

    def market_is_open(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        t = now.strftime("%H:%M")
        return MARKET_OPEN <= t < MARKET_CLOSE

    def etf_cutoff_passed(self) -> bool:
        return datetime.now(ET).strftime("%H:%M") >= LEVERAGED_ETF_CLOSE_TIME

    # ── Daily reset ───────────────────────────────────────────────────────────

    def _daily_check(self):
        today = datetime.now(ET).date()
        if self._day != today:
            self._day             = today
            self.halted           = False
            self._day_start_value = self._broker.portfolio_value()
            logger.info(f"New day — starting equity ${self._day_start_value:,.2f}")

    # ── Trade gate ────────────────────────────────────────────────────────────

    def approve_entry(self, ticker: str) -> Tuple[bool, str]:
        """
        Returns (True, "OK") or (False, reason).
        Called before every BUY order.
        """
        self._daily_check()

        if self.halted:
            return False, "Daily loss limit — trading halted"

        if not self.market_is_open():
            return False, "Outside market hours"

        if ticker in LEVERAGED_ETFS and self.etf_cutoff_passed():
            return False, f"Past {LEVERAGED_ETF_CLOSE_TIME} — no new leveraged ETF positions"

        if len(self.positions) >= MAX_OPEN_POSITIONS:
            return False, f"Already at max {MAX_OPEN_POSITIONS} open positions"

        if ticker in self.positions:
            return False, f"Already holding {ticker}"

        # Check running P&L
        self._check_daily_loss()
        if self.halted:
            return False, "Daily loss limit just triggered"

        return True, "OK"

    # ── P&L monitoring ────────────────────────────────────────────────────────

    def _check_daily_loss(self):
        if self._day_start_value is None:
            return
        current = self._broker.portfolio_value()
        loss    = (self._day_start_value - current) / self._day_start_value
        if loss >= DAILY_LOSS_LIMIT_PCT:
            self.halted = True
            logger.warning(f"DAILY LOSS LIMIT: down {loss:.1%} — halting all trading")

    # ── Position sizing ───────────────────────────────────────────────────────

    def size(self, price: float) -> int:
        """Return whole-share count that equals MAX_POSITION_PCT of portfolio."""
        equity = self._broker.portfolio_value()
        shares = int((equity * MAX_POSITION_PCT) / price)
        return max(1, shares)

    # ── Position lifecycle ────────────────────────────────────────────────────

    def record_open(self, ticker: str, price: float, qty: int, strategy: str):
        self.positions[ticker] = Position(ticker, price, qty, strategy)
        logger.info(f"Position recorded: {self.positions[ticker]}")

    def record_close(self, ticker: str) -> Optional[Position]:
        pos = self.positions.pop(ticker, None)
        if pos:
            logger.info(f"Position removed: {pos}")
        return pos

    def tick_all(self, ticker: str, price: float) -> Optional[Tuple[str, str]]:
        """
        Update price for a position and return (ticker, reason) if a stop fired.
        """
        if ticker not in self.positions:
            return None
        self.positions[ticker].tick(price)
        triggered, reason = self.positions[ticker].stop_triggered()
        if triggered:
            return ticker, reason
        return None

    def etfs_to_force_close(self) -> List[str]:
        """Return leveraged ETF positions that must be closed before end of day."""
        if not self.etf_cutoff_passed():
            return []
        return [t for t in self.positions if t in LEVERAGED_ETFS]
