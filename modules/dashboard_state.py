"""
dashboard_state.py
A tiny thread-safe bridge between the live Bot and the Flask web dashboard.

WHY
---
The web dashboard runs Flask in a background thread inside the same process as
the bot, but it queries Alpaca directly and has no handle on the bot's in-memory
research objects (regime, cooldowns, correlation, shadow, ...). Rather than couple
them, the bot pushes a plain-dict snapshot here each refresh and the dashboard
reads it. Empty/standalone is fine — the dashboard just shows "waiting for data".
"""

import threading
from datetime import datetime

import pytz

ET = pytz.timezone("America/New_York")
_lock = threading.Lock()
_state: dict = {}


def update(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)
        _state["updated"] = datetime.now(ET).strftime("%H:%M:%S ET")


def snapshot() -> dict:
    with _lock:
        return dict(_state)
