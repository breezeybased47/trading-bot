"""
structured_log.py
JSON-lines structured event logging for offline research analysis.

WHY
---
The spec's golden rule: "a guard you can't measure is useless." Every module
that blocks, resizes, or alters a trade must leave a machine-readable trace so
its real-world value can be measured later. This module writes exactly one JSON
object per line to STRUCTURED_LOG_FILE. The whole research history can then be
loaded in one line:

    import pandas as pd
    events = pd.read_json("logs/events.jsonl", lines=True)

Design notes
------------
- Thread-safe: the main loop, the exit engine, and the shadow engine all emit
  events from different threads, so writes are serialised behind a lock.
- Never raises: logging must NEVER crash the trading loop. All writes are wrapped
  and failures are swallowed (after being reported to the standard logger).
- Zero new dependencies (stdlib json + threading).
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional

import pytz

import config

ET = pytz.timezone("America/New_York")
logger = logging.getLogger("events")

_lock = threading.Lock()


def init() -> None:
    """Ensure the structured-log directory exists. Safe to call repeatedly."""
    path = getattr(config, "STRUCTURED_LOG_FILE", "logs/events.jsonl")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)


def log_event(event_type: str, **fields) -> dict:
    """
    Append one structured event to the JSON-lines file.

    `event_type` is a short stable string ("regime_block", "size_decision",
    "liquidity_reject", "partial_exit", ...). Everything else goes in **fields.
    Returns the record dict (handy for tests / chaining). Never raises.
    """
    record = {"ts": datetime.now(ET).isoformat(), "event": event_type}
    record.update(fields)

    if getattr(config, "JSON_LOGGING", True):
        line = json.dumps(record, default=str)
        path = getattr(config, "STRUCTURED_LOG_FILE", "logs/events.jsonl")
        try:
            with _lock:
                with open(path, "a") as f:
                    f.write(line + "\n")
        except Exception as exc:  # logging must never crash trading
            logger.error("structured log write failed: %s", exc)

    if getattr(config, "RESEARCH_MODE", False):
        logger.info("EVENT %s | %s", event_type, fields)

    return record


def log_block(guard: str, ticker: str, reason: str, **extra) -> dict:
    """
    Convenience for the single most important event class: a guard refusing or
    altering a trade. Standardising the shape ("blocked_by", "ticker", "reason")
    lets journal_report count blocks per guard and ask "did blocking help?".
    """
    return log_event("guard_block", blocked_by=guard, ticker=ticker, reason=reason, **extra)


def log_decision(decision: str, ticker: str, **extra) -> dict:
    """A non-blocking decision that changed a trade (resize, half-size, caution)."""
    return log_event("decision", decision=decision, ticker=ticker, **extra)
