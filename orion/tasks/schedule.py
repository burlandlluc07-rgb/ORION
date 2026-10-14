"""Schedule specs and natural-language time parsing (no external deps).

Specs:
    once 2026-10-14T17:30      a single run
    daily 08:00                every day
    weekdays 08:00             Monday–Friday
    weekly mon 09:00           one weekday
    every 30m | every 2h       fixed interval
"""

from __future__ import annotations

import datetime as dt
import re

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ScheduleError(ValueError):
    pass


def _hhmm(s: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s.strip().lower())
    if not m:
        raise ScheduleError(f"Bad time: {s!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ScheduleError(f"Bad time: {s!r}")
    return h, mi


def next_run(spec: str, after: dt.datetime | None = None) -> dt.datetime | None:
    """Next fire time strictly after ``after`` (local naive datetimes). None when a one-off has passed."""
    after = after or dt.datetime.now()
    parts = spec.strip().lower().split()
    if not parts:
        raise ScheduleError("Empty schedule")
    kind = parts[0]
    if kind == "once":
        when = dt.datetime.fromisoformat(" ".join(parts[1:]).upper().replace(" ", "T"))
        return when if when > after else None
    if kind == "every":
        m = re.fullmatch(r"(\d+)\s*(s|m|h|d)", "".join(parts[1:]))
        if not m:
            raise ScheduleError("Use e.g. 'every 30m' or 'every 2h'")
        secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        if secs < 60:
            raise ScheduleError("Minimum interval is 1 minute")
        return after + dt.timedelta(seconds=secs)
    if kind in ("daily", "weekdays", "weekends"):
        h, mi = _hhmm(" ".join(parts[1:]))
        cand = after.replace(hour=h, minute=mi, second=0, microsecond=0)
        for _ in range(8):
            if cand <= after:
                cand += dt.timedelta(days=1)
                continue
            wd = cand.weekday()
            if kind == "weekdays" and wd >= 5 or kind == "weekends" and wd < 5:
                cand += dt.timedelta(days=1)
                continue
            return cand
    if kind == "weekly":
        if len(parts) < 3 or parts[1][:3] not in DAYS:
            raise ScheduleError("Use e.g. 'weekly mon 09:00'")
        day = DAYS.index(parts[1][:3])
        h, mi = _hhmm(" ".join(parts[2:]))
        cand = after.replace(hour=h, minute=mi, second=0, microsecond=0)
        cand += dt.timedelta(days=(day - cand.weekday()) % 7)
        if cand <= after:
            cand += dt.timedelta(days=7)
        return cand
    raise ScheduleError(f"Unknown schedule: {spec!r}")


def describe(spec: str) -> str:
    p = spec.split()
    if not p:
        return spec
    if p[0] == "once":
        try:
            return dt.datetime.fromisoformat(" ".join(p[1:]).replace(" ", "T")).strftime("Once, %a %d %b %H:%M")
        except ValueError:
            return spec
    if p[0] == "every":
        return "Every " + " ".join(p[1:])
    return spec[0].upper() + spec[1:]


_UNITS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600,
          "hour": 3600, "d": 86400, "day": 86400}


def parse_when(text: str, now: dt.datetime | None = None) -> dt.datetime:
    """Parse "in 10 minutes", "in 1h 30m", "at 5pm", "tomorrow 9:15", "17:30" or an ISO timestamp."""
    now = now or dt.datetime.now()
    t = text.strip().lower()
    try:
        when = dt.datetime.fromisoformat(text.strip())
        return when.replace(tzinfo=None) if when.tzinfo is None else when.astimezone().replace(tzinfo=None)
    except ValueError:
        pass
    pairs = re.findall(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b", t)
    if t.startswith("in ") or (pairs and not re.search(r"\d:\d|am|pm|tomorrow|today|at ", t)):
        if not pairs:
            raise ScheduleError(f"Couldn't understand {text!r}")
        secs = sum(float(n) * _UNITS[u.rstrip("s") if u.rstrip("s") in _UNITS else u] for n, u in pairs)
        return now + dt.timedelta(seconds=secs)
    day = now.date()
    if "tomorrow" in t:
        day += dt.timedelta(days=1)
    m = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", t.replace("at ", ""))
    if not m:
        if "tomorrow" in t:
            return dt.datetime.combine(day, dt.time(9, 0))
        raise ScheduleError(f"Couldn't understand {text!r}")
    h, mi = _hhmm(m.group(1))
    when = dt.datetime.combine(day, dt.time(h, mi))
    if when <= now and "tomorrow" not in t and "today" not in t:
        when += dt.timedelta(days=1)
    return when
