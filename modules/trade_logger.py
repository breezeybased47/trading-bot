"""
trade_logger.py
Logs every trade to a CSV file and computes today's stats for the dashboard.
"""

import csv
import logging
import os
from datetime import datetime
from typing import Optional

import pytz

from config import TRADES_LOG_FILE

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

_HEADERS = [
    "timestamp", "ticker", "action", "strategy",
    "qty", "entry_price", "exit_price", "pnl", "pnl_pct", "reason",
]


def init():
    """Create log directory and CSV file with headers if they don't exist."""
    os.makedirs(os.path.dirname(TRADES_LOG_FILE), exist_ok=True)
    if not os.path.exists(TRADES_LOG_FILE):
        with open(TRADES_LOG_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=_HEADERS).writeheader()
        logger.info(f"Trade log created: {TRADES_LOG_FILE}")


def record(
    ticker:      str,
    action:      str,
    strategy:    str,
    qty:         int,
    entry_price: float,
    exit_price:  Optional[float] = None,
    reason:      str = "",
):
    """Append one row to the CSV trade log."""
    pnl     = (exit_price - entry_price) * qty if exit_price else 0.0
    pnl_pct = (exit_price - entry_price) / entry_price * 100 if exit_price else 0.0

    row = {
        "timestamp":   datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "ticker":      ticker,
        "action":      action,
        "strategy":    strategy,
        "qty":         qty,
        "entry_price": f"{entry_price:.2f}",
        "exit_price":  f"{exit_price:.2f}" if exit_price else "",
        "pnl":         f"{pnl:.2f}",
        "pnl_pct":     f"{pnl_pct:.2f}%",
        "reason":      reason,
    }

    with open(TRADES_LOG_FILE, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=_HEADERS).writerow(row)


def today_stats() -> dict:
    """Read today's closed trades and return aggregated win/loss/P&L stats."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    closes = []

    if not os.path.exists(TRADES_LOG_FILE):
        return _empty_stats()

    with open(TRADES_LOG_FILE, "r") as f:
        for row in csv.DictReader(f):
            if row["timestamp"].startswith(today) and row["action"] == "SELL":
                closes.append(float(row["pnl"]))

    if not closes:
        return _empty_stats()

    wins   = sum(1 for p in closes if p > 0)
    losses = len(closes) - wins
    return {
        "total_trades": len(closes),
        "wins":         wins,
        "losses":       losses,
        "win_rate":     wins / len(closes) * 100,
        "total_pnl":    sum(closes),
    }


def _empty_stats() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0}
