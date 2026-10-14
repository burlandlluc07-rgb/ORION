"""Proactive mode: ORION notices things and speaks up — like JARVIS.

Everything here runs locally (psutil, the file system, the calendar feed),
so watching costs zero Gemini requests. Each alert has a cooldown so ORION
informs without nagging, and quiet hours silence everything but critical
alerts (they still appear in the app).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import psutil

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger("orion.proactive")

PARTIAL = (".crdownload", ".part", ".tmp", ".download", ".opdownload", ".partial")


@dataclass
class Readings:
    cpu: float = 0.0
    ram: float = 0.0
    disk_free_gb: float = 999.0
    battery: float | None = None
    plugged: bool | None = None
    online: bool | None = None
    top_cpu: str = ""
    downloads: dict[str, int] = field(default_factory=dict)  # name → size
    events: list[dict] = field(default_factory=list)  # upcoming calendar events with "_start" datetime


def _in_quiet_hours(now: dt.datetime, start: str, end: str) -> bool:
    try:
        s = dt.time.fromisoformat(start)
        e = dt.time.fromisoformat(end)
    except ValueError:
        return False
    t = now.time()
    return (s <= t or t < e) if s > e else (s <= t < e)


class Proactive:
    def __init__(self, engine: "Engine", clock: Callable[[], float] = time.time) -> None:
        self.e = engine
        self.clock = clock
        self._last: dict[str, float] = {}
        self._cpu_high = 0
        self._battery_warned = 0  # 0 none, 1 low, 2 critical
        self._online: bool | None = None
        self._offline_since: float | None = None
        self._dl_seen: dict[str, int] = {}
        self._dl_pending: dict[str, int] = {}
        self._dl_started = False
        self._events_warned: set[str] = set()
        self._task: asyncio.Task | None = None
        self.alerts: list[dict] = []

    # ── loop ───────────────────────────────────────────────────────────────
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="proactive")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(15)
        while True:
            if self.e.config.settings.proactive:
                try:
                    readings = await asyncio.to_thread(self.read)
                    for alert in self.evaluate(readings):
                        self.fire(**alert)
                except Exception:
                    log.exception("Proactive check failed")
            await asyncio.sleep(20)

    # ── sensors ────────────────────────────────────────────────────────────
    def read(self) -> Readings:
        from .tools.system import _system_drive

        r = Readings(cpu=psutil.cpu_percent(interval=1.0), ram=psutil.virtual_memory().percent,
                     disk_free_gb=psutil.disk_usage(str(_system_drive())).free / 2**30)
        batt = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if batt:
            r.battery, r.plugged = batt.percent, bool(batt.power_plugged)
        try:
            with socket.create_connection(("1.1.1.1", 443), timeout=3):
                r.online = True
        except OSError:
            r.online = False
        if r.cpu > 85:
            procs = []
            for p in psutil.process_iter(["name", "cpu_percent"]):
                try:
                    procs.append((p.info["cpu_percent"] or 0, p.info["name"] or "?"))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(reverse=True)
            r.top_cpu = procs[0][1] if procs else ""
        dl = Path.home() / "Downloads"
        if dl.is_dir():
            try:
                for f in dl.iterdir():
                    if f.is_file():
                        r.downloads[f.name] = f.stat().st_size
            except OSError:
                pass
        accounts = getattr(self.e, "accounts", None)
        if accounts and accounts.data.get("calendars"):
            now = dt.datetime.now().astimezone()
            try:
                for ev in accounts.events_between(now, now + dt.timedelta(minutes=16)):
                    if not ev["all_day"]:
                        r.events.append(ev)
            except Exception:
                pass
        return r

    # ── rules ──────────────────────────────────────────────────────────────
    def _cooled(self, key: str, seconds: float) -> bool:
        now = self.clock()
        if now - self._last.get(key, 0) < seconds:
            return False
        self._last[key] = now
        return True

    def evaluate(self, r: Readings) -> list[dict]:
        out: list[dict] = []
        s = self.e.config.settings

        if r.battery is not None:
            if r.plugged:
                self._battery_warned = 0
            elif r.battery <= 10 and self._battery_warned < 2:
                self._battery_warned = 2
                out.append({"key": "battery", "title": "Battery critical", "critical": True,
                            "message": f"Battery at {r.battery:.0f}%. Plug in now or I'll lose you."})
            elif r.battery <= 20 and self._battery_warned < 1:
                self._battery_warned = 1
                out.append({"key": "battery", "title": "Battery low",
                            "message": f"Battery at {r.battery:.0f}% — you may want to plug in."})

        self._cpu_high = self._cpu_high + 1 if r.cpu > 90 else 0
        if self._cpu_high >= 3 and self._cooled("cpu", 1800):
            who = f" {r.top_cpu} is the main culprit." if r.top_cpu else ""
            out.append({"key": "cpu", "title": "PC under heavy load",
                        "message": f"CPU has been above 90% for a minute.{who} Want me to close it?"})

        if r.ram > 92 and self._cooled("ram", 1800):
            out.append({"key": "ram", "title": "Memory almost full",
                        "message": f"Memory is at {r.ram:.0f}%. Closing a few apps will speed things up."})

        if r.disk_free_gb < 5 and self._cooled("disk", 86400):
            out.append({"key": "disk", "title": "Disk almost full",
                        "message": f"Only {r.disk_free_gb:.1f} GB free on the system drive. Say “why is my PC slow” "
                                   "and I'll find what's taking space."})

        if r.online is not None:
            if r.online:
                if self._online is False and self._offline_since and self.clock() - self._offline_since > 60:
                    out.append({"key": "net", "title": "Back online", "message": "Internet connection restored."})
                self._offline_since = None
                self._online = True
            else:
                if self._online:
                    self._offline_since = self.clock()
                self._online = False
                if self._offline_since and self.clock() - self._offline_since > 60 and self._cooled("net_down", 3600):
                    out.append({"key": "net_down", "title": "Internet is down",
                                "message": "I've lost the internet connection. Local commands still work."})

        if s.proactive_downloads:
            if not self._dl_started:  # first scan: remember what's already there
                self._dl_seen = dict(r.downloads)
                self._dl_started = True
            else:
                for name, size in r.downloads.items():
                    if name in self._dl_seen or name.lower().endswith(PARTIAL) or name.startswith("~$"):
                        continue
                    if self._dl_pending.get(name) == size and size > 0:  # size stable across checks → finished
                        self._dl_seen[name] = size
                        self._dl_pending.pop(name, None)
                        out.append({"key": f"dl:{name}", "title": "Download finished", "message": f"{name} is ready in Downloads."})
                    else:
                        self._dl_pending[name] = size

        for ev in r.events:
            key = f"{ev['title']}@{ev['start']}"
            if key not in self._events_warned:
                self._events_warned.add(key)
                where = f" ({ev['location']})" if ev.get("location") else ""
                out.append({"key": f"ev:{key}", "title": "Coming up",
                            "message": f"{ev['title']} at {ev['start'][-5:]}{where}."})
        return out

    # ── delivery ───────────────────────────────────────────────────────────
    def fire(self, key: str, title: str, message: str, critical: bool = False) -> None:
        s = self.e.config.settings
        quiet = _in_quiet_hours(dt.datetime.now(), s.quiet_start, s.quiet_end) and not critical
        alert = {"key": key, "title": title, "message": message, "ts": self.clock(), "quiet": quiet}
        self.alerts = (self.alerts + [alert])[-50:]
        self.e.bus.emit("alert", alert=alert)
        log.info("Alert: %s — %s", title, message)
        if quiet:
            return
        self.e.notify(title, message)
        if s.proactive_voice and self.e.voice and self.e.voice.available and self.e.voice.state in ("standby", "off"):
            asyncio.ensure_future(self.e.voice.speak(message))
