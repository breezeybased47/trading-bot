"""
broker.py
Thin wrapper around alpaca-py for order placement, cancellation,
portfolio queries, and position management.
"""

import logging
import time
from typing import Dict, List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, PAPER_TRADING, UNFILLED_ORDER_TIMEOUT,
)

logger = logging.getLogger(__name__)


class Broker:
    """Handles all communication with Alpaca for order execution."""

    def __init__(self):
        self._client = TradingClient(
            ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=PAPER_TRADING
        )
        # Pending orders: {order_id: {ticker, submitted_at, qty, side}}
        self._pending: Dict[str, dict] = {}

    # ── Account ───────────────────────────────────────────────────────────────

    def portfolio_value(self) -> float:
        try:
            return float(self._client.get_account().portfolio_value)
        except Exception as exc:
            logger.error(f"portfolio_value error: {exc}")
            return 0.0

    def buying_power(self) -> float:
        try:
            return float(self._client.get_account().buying_power)
        except Exception as exc:
            logger.error(f"buying_power error: {exc}")
            return 0.0

    def is_blocked(self) -> bool:
        try:
            a = self._client.get_account()
            return a.trading_blocked or a.account_blocked
        except Exception as exc:
            logger.error(f"is_blocked error: {exc}")
            return True

    # ── Orders ────────────────────────────────────────────────────────────────

    def market_buy(self, ticker: str, qty: int) -> Optional[str]:
        return self._place_market(ticker, qty, OrderSide.BUY)

    def market_sell(self, ticker: str, qty: int) -> Optional[str]:
        return self._place_market(ticker, qty, OrderSide.SELL)

    def limit_buy(self, ticker: str, qty: int, limit: float) -> Optional[str]:
        return self._place_limit(ticker, qty, OrderSide.BUY, limit)

    def limit_sell(self, ticker: str, qty: int, limit: float) -> Optional[str]:
        return self._place_limit(ticker, qty, OrderSide.SELL, limit)

    def _place_market(self, ticker: str, qty: int, side: OrderSide) -> Optional[str]:
        try:
            req = MarketOrderRequest(
                symbol=ticker, qty=qty, side=side, time_in_force=TimeInForce.DAY
            )
            order = self._client.submit_order(req)
            oid = str(order.id)
            self._pending[oid] = {"ticker": ticker, "submitted_at": time.time(), "qty": qty}
            logger.info(f"Market {side.value} {qty}x {ticker} [id={oid}]")
            return oid
        except Exception as exc:
            logger.error(f"market order failed ({ticker}): {exc}")
            return None

    def _place_limit(self, ticker: str, qty: int, side: OrderSide, limit: float) -> Optional[str]:
        try:
            req = LimitOrderRequest(
                symbol=ticker, qty=qty, side=side,
                time_in_force=TimeInForce.DAY, limit_price=round(limit, 2)
            )
            order = self._client.submit_order(req)
            oid = str(order.id)
            self._pending[oid] = {"ticker": ticker, "submitted_at": time.time(), "qty": qty}
            logger.info(f"Limit {side.value} {qty}x {ticker} @ {limit:.2f} [id={oid}]")
            return oid
        except Exception as exc:
            logger.error(f"limit order failed ({ticker}): {exc}")
            return None

    def cancel(self, order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(order_id)
            self._pending.pop(order_id, None)
            logger.info(f"Cancelled order {order_id}")
            return True
        except Exception as exc:
            logger.error(f"cancel failed ({order_id}): {exc}")
            return False

    def cancel_stale(self):
        """Cancel any order that has been open longer than UNFILLED_ORDER_TIMEOUT seconds."""
        now  = time.time()
        stale = [oid for oid, info in self._pending.items()
                 if now - info["submitted_at"] > UNFILLED_ORDER_TIMEOUT]
        for oid in stale:
            logger.warning(f"Stale order {oid} ({self._pending[oid]['ticker']}) — cancelling")
            self.cancel(oid)

    # ── Order status ──────────────────────────────────────────────────────────

    def fill_price(self, order_id: str) -> Optional[float]:
        """Return average fill price if the order is filled, else None."""
        try:
            order = self._client.get_order_by_id(order_id)
            return float(order.filled_avg_price) if order.filled_avg_price else None
        except Exception as exc:
            logger.error(f"fill_price error ({order_id}): {exc}")
            return None

    def wait_for_fill(self, order_id: str, timeout: float = 5.0) -> Optional[float]:
        """Poll for a fill price, waiting up to `timeout` seconds."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            price = self.fill_price(order_id)
            if price:
                return price
            time.sleep(0.5)
        return None

    # ── Positions ─────────────────────────────────────────────────────────────

    def open_positions(self) -> List[dict]:
        try:
            return [
                {
                    "ticker":       p.symbol,
                    "qty":          int(p.qty),
                    "entry":        float(p.avg_entry_price),
                    "current":      float(p.current_price),
                    "market_value": float(p.market_value),
                    "pnl":          float(p.unrealized_pl),
                    "pnl_pct":      float(p.unrealized_plpc),
                }
                for p in self._client.get_all_positions()
            ]
        except Exception as exc:
            logger.error(f"open_positions error: {exc}")
            return []

    def close_all(self):
        """Emergency: flatten everything immediately."""
        try:
            self._client.close_all_positions(cancel_orders=True)
            logger.warning("Emergency close — all positions flattened")
        except Exception as exc:
            logger.error(f"close_all error: {exc}")
