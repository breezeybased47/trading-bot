"""
leverage_guard.py  —  Course module: leveraged-ETF path-dependency guard.

WHY (straight from the course)
------------------------------
The course's "Leverage ETFs" and "Path Dependencies" lessons explain the trap:
a 3x ETF like TQQQ/SQQQ resets its leverage DAILY, so in a choppy or volatile
market it bleeds value even if the underlying ends flat — the path matters, not
just the destination. Buy-and-hold a leveraged ETF through chop and decay quietly
eats you. The fix the course implies: only carry leveraged ETFs when the market
is cleanly TRENDING, never in CHOPPY/VOLATILE regimes.

This guard reuses the regime classifier the bot already computes — when an entry
is a leveraged ETF and the current regime is in LEVERAGED_ETF_BAD_REGIMES, block
it. Default OFF; every block is logged so its value can be measured.
"""

import logging

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)


def check_entry(ticker: str, regime: str) -> dict:
    """Block leveraged-ETF entries in decay-prone (non-trending) regimes."""
    if not config.LEVERAGED_ETF_REGIME_GUARD_ENABLED:
        return {"allow": True, "reason": "filter_off"}
    if ticker in config.LEVERAGED_ETFS and regime in config.LEVERAGED_ETF_BAD_REGIMES:
        reason = ("%s is leveraged and the regime is %s — daily-rebalance decay "
                  "(path dependency) makes holding it costly here" % (ticker, regime))
        slog.log_block("leverage_decay", ticker, reason, regime=regime)
        return {"allow": False, "reason": reason}
    return {"allow": True, "reason": "ok"}
