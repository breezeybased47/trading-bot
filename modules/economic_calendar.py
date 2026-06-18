"""
economic_calendar.py  —  Course module: Economic Event Guard.

WHY (straight from the course)
------------------------------
Five of the course's lessons are about economic reports — FOMC meetings, CPI /
inflation, the jobs report, the economic calendar, "external factors". The point
they hammer: do NOT be in a fresh trade when a major scheduled release hits. A
2:00pm FOMC statement or an 8:30am CPI print can move the whole tape several
percent in seconds and stop you out on noise. So around those events the bot
should stand down (NO-TOUCH) or at least size down (CAUTION).

FREE BY DESIGN (no paid API)
----------------------------
The big events run on PUBLIC, predictable schedules:
  - Jobs report (NFP): first Friday of the month, 8:30 ET  -> computed automatically.
  - CPI: monthly ~8:30 ET on a set day            -> set ECON_CPI_DAY.
  - FOMC + anything else: paste the dates into ECON_EVENTS from any free economic
    calendar (exactly the skill the course teaches). FOMC gets a longer tail.
An optional Finnhub economic calendar could auto-fill this later, but nothing is
required — and the guard FAILS OPEN (never blocks) when it has no events.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pytz

import config
from modules import structured_log as slog

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

NO_TOUCH = "NO-TOUCH"
CAUTION = "CAUTION"
CLEAR = "CLEAR"

Event = Tuple[datetime, str, str]   # (datetime ET, name, impact)


# ── Pure schedule helpers (unit tested) ───────────────────────────────────────

def first_friday(year: int, month: int) -> datetime:
    """First Friday of a month (NFP jobs-report day)."""
    d = datetime(year, month, 1)
    offset = (4 - d.weekday()) % 7          # Mon=0 .. Fri=4
    return d.replace(day=1 + offset)


def _et(dt_naive: datetime) -> datetime:
    return ET.localize(dt_naive)


def _months(now: datetime, ahead: int):
    """Yield (year, month) for now and the next `ahead` months."""
    y, m = now.year, now.month
    for _ in range(ahead + 1):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def nfp_events(now: datetime, ahead: int = 2) -> List[Event]:
    out = []
    for y, m in _months(now, ahead):
        d = first_friday(y, m).replace(hour=8, minute=30)
        out.append((_et(d), "NFP (jobs report)", "high"))
    return out


def cpi_events(now: datetime, day: int, ahead: int = 2) -> List[Event]:
    if not day:
        return []
    out = []
    for y, m in _months(now, ahead):
        try:
            d = datetime(y, m, day, 8, 30)
        except ValueError:
            continue
        out.append((_et(d), "CPI (inflation)", "high"))
    return out


def parse_config_events(raw) -> List[Event]:
    out = []
    for item in raw or []:
        try:
            when, name, impact = item
            dt = _et(datetime.strptime(when, "%Y-%m-%d %H:%M"))
            out.append((dt, name, impact))
        except Exception as exc:
            logger.error("bad ECON_EVENTS entry %r: %s", item, exc)
    return out


def event_status(now: datetime, events: List[Event], before_min: int,
                 after_min: int, caution_min: int, fomc_after_min: int) -> dict:
    """
    Pure: classify `now` against the event list. NO-TOUCH wins over CAUTION wins
    over CLEAR. Returns {state, event, event_time, minutes_to}.
    """
    caution_hit = None
    for dt, name, impact in events:
        if impact != "high":
            continue
        tail = fomc_after_min if name.upper().startswith("FOMC") else after_min
        start = dt - timedelta(minutes=before_min)
        end = dt + timedelta(minutes=tail)
        if start <= now <= end:
            return {"state": NO_TOUCH, "event": name, "event_time": dt.isoformat(),
                    "minutes_to": round((dt - now).total_seconds() / 60.0)}
        caution_start = start - timedelta(minutes=caution_min)
        if caution_start <= now < start:
            caution_hit = (dt, name)
    if caution_hit:
        dt, name = caution_hit
        return {"state": CAUTION, "event": name, "event_time": dt.isoformat(),
                "minutes_to": round((dt - now).total_seconds() / 60.0)}
    return {"state": CLEAR, "event": None, "event_time": None, "minutes_to": None}


# ── Live guard ────────────────────────────────────────────────────────────────

class EconomicCalendar:
    def __init__(self):
        self._events: List[Event] = []
        self._built_day = None
        self.refresh()

    def refresh(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(ET)
        events = parse_config_events(config.ECON_EVENTS)
        if config.ECON_AUTO_NFP:
            events += nfp_events(now)
        events += cpi_events(now, config.ECON_CPI_DAY)
        self._events = events
        self._built_day = now.date()

    def status(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(ET)
        if self._built_day != now.date():
            self.refresh(now)
        return event_status(now, self._events, config.ECON_BLACKOUT_BEFORE_MIN,
                            config.ECON_BLACKOUT_AFTER_MIN, config.ECON_CAUTION_BUFFER_MIN,
                            config.ECON_FOMC_AFTER_MIN)

    def check_entry(self, ticker: str, now: Optional[datetime] = None) -> dict:
        """Gate an entry against the economic calendar."""
        if not config.ECON_GUARD_ENABLED:
            return {"allow": True, "size_mult": 1.0, "reason": "filter_off"}
        st = self.status(now)
        if st["state"] == NO_TOUCH:
            reason = "econ blackout: %s in %s min" % (st["event"], st["minutes_to"])
            slog.log_block("econ_event", ticker, reason, event=st["event"])
            return {"allow": False, "size_mult": 0.0, "reason": reason}
        if st["state"] == CAUTION:
            reason = "econ caution: %s approaching (%s min) -> half size" % (st["event"], st["minutes_to"])
            slog.log_decision("econ_caution", ticker, event=st["event"])
            return {"allow": True, "size_mult": 0.5, "reason": reason}
        return {"allow": True, "size_mult": 1.0, "reason": "clear"}

    def dashboard(self, now: Optional[datetime] = None) -> dict:
        st = self.status(now)
        return {"state": st["state"], "event": st["event"], "minutes_to": st["minutes_to"],
                "enabled": config.ECON_GUARD_ENABLED, "events_loaded": len(self._events)}
