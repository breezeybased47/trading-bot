"""
log_setup.py
Bounded, de-spammed logging.

WHY
---
alpaca-py's stream reconnects with ZERO backoff and logs a full traceback every
iteration when it can't authenticate. When a second instance hits Alpaca's
single-connection limit this balloons logs/bot_error.log to tens of MB in
minutes. launchd (com.tradingbot.plist) redirects stderr to that file and can't
rotate it, so we bound the damage in-process:

  - DedupeRateLimitFilter: collapse identical repeated records to 1 / interval
  - trim_oversized():       truncate a log file that has already ballooned
  - a bounded RotatingFileHandler for a clean, capped application log

stdlib only.
"""

import logging
import os
import time
from logging.handlers import RotatingFileHandler


class DedupeRateLimitFilter(logging.Filter):
    """
    Drop identical log records seen within `min_interval` seconds. A reconnect
    loop with no backoff can emit thousands of identical tracebacks per second;
    this caps each unique message to one per interval while preserving signal.
    """

    def __init__(self, min_interval: float = 60.0):
        super().__init__()
        self.min_interval = min_interval
        self._last = {}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            key = (record.name, record.levelno, record.getMessage()[:160])
        except Exception:
            return True
        now = time.time()
        if now - self._last.get(key, 0.0) >= self.min_interval:
            self._last[key] = now
            return True
        return False


def trim_oversized(path: str, max_mb: float = 5.0) -> bool:
    """Truncate a launchd-redirected log that has grown past max_mb. Returns True if trimmed."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_mb * 1024 * 1024:
            with open(path, "w"):
                pass
            return True
    except Exception:
        pass
    return False


def configure_logging(rotating_path: str = "logs/bot.rotating.log",
                      dedupe_interval: float = 60.0) -> DedupeRateLimitFilter:
    """
    Attach the dedupe filter to all existing handlers (so the launchd-captured
    stderr is de-spammed too) and add a bounded rotating application log.
    Returns the filter (handy for tests).
    """
    root = logging.getLogger()
    flt = DedupeRateLimitFilter(dedupe_interval)
    for h in root.handlers:
        h.addFilter(flt)
    try:
        os.makedirs(os.path.dirname(rotating_path) or ".", exist_ok=True)
        rh = RotatingFileHandler(rotating_path, maxBytes=5 * 1024 * 1024, backupCount=3)
        rh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s — %(message)s", "%H:%M:%S"))
        rh.addFilter(flt)
        root.addHandler(rh)
    except Exception as exc:
        root.error("could not attach rotating handler: %s", exc)
    return flt
