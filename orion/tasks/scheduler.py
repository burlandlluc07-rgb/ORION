"""Background scheduler for timers, reminders and routines.

Items persist in SQLite, so routines survive restarts. A routine whose time
passed while ORION was closed runs once on start-up (if missed by < 6 h)
rather than replaying every missed occurrence.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Awaitable, Callable

from ..memory.store import Store, new_id
from .schedule import next_run

log = logging.getLogger("orion.scheduler")
NOTIFY_PREFIX = "!notify:"
MISSED_GRACE = 6 * 3600


class Scheduler:
    def __init__(self, store: Store, on_notify: Callable[[str, str], None],
                 on_prompt: Callable[[dict], Awaitable[str]], on_change: Callable[[], None]) -> None:
        self.store = store
        self.on_notify = on_notify
        self.on_prompt = on_prompt
        self.on_change = on_change
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def add(self, name: str, prompt: str, schedule: str, enabled: bool = True) -> dict:
        nxt = next_run(schedule)
        r = {"id": new_id(), "name": name, "prompt": prompt, "schedule": schedule, "enabled": enabled,
             "next_run": nxt.timestamp() if nxt else None, "last_run": None, "last_result": None}
        self.store.save_routine(r)
        self._changed()
        return r

    def add_once(self, name: str, prompt: str, when: dt.datetime) -> dict:
        return self.add(name, prompt, "once " + when.replace(microsecond=0).isoformat(timespec="seconds"))

    def update(self, rid: str, patch: dict) -> dict | None:
        for r in self.store.list_routines():
            if r["id"] == rid:
                r.update({k: v for k, v in patch.items() if k in ("name", "prompt", "schedule", "enabled")})
                nxt = next_run(r["schedule"])
                r["next_run"] = nxt.timestamp() if nxt else None
                self.store.save_routine(r)
                self._changed()
                return r
        return None

    def remove(self, rid: str) -> None:
        self.store.delete_routine(rid)
        self._changed()

    async def run_now(self, rid: str) -> None:
        for r in self.store.list_routines():
            if r["id"] == rid:
                await self._fire(r, reschedule=False)

    def _changed(self) -> None:
        self._wake.set()
        self.on_change()

    async def _loop(self) -> None:
        now = time.time()
        for r in self.store.list_routines():  # catch up on anything missed while closed
            if r["enabled"] and r["next_run"] and r["next_run"] < now:
                if now - r["next_run"] < MISSED_GRACE:
                    asyncio.create_task(self._fire(r))
                else:
                    self._reschedule(r)
        while True:
            self._wake.clear()
            now = time.time()
            soonest = 30.0
            for r in self.store.list_routines():
                if not r["enabled"] or not r["next_run"]:
                    continue
                if r["next_run"] <= now:
                    asyncio.create_task(self._fire(r))
                    r["next_run"] = None  # prevent double fire while running
                    self.store.save_routine(r)
                else:
                    soonest = min(soonest, r["next_run"] - now)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.2, soonest))
            except asyncio.TimeoutError:
                pass

    def _reschedule(self, r: dict) -> None:
        nxt = next_run(r["schedule"])
        if nxt is None:  # finished one-off
            self.store.delete_routine(r["id"])
        else:
            r["next_run"] = nxt.timestamp()
            self.store.save_routine(r)
        self.on_change()

    async def _fire(self, r: dict, reschedule: bool = True) -> None:
        log.info("Firing %s", r["name"])
        try:
            if r["prompt"].startswith(NOTIFY_PREFIX):
                self.on_notify(r["name"], r["prompt"][len(NOTIFY_PREFIX):])
                result = "notified"
            else:
                result = await self.on_prompt(r)
        except Exception as exc:  # never let one routine kill the loop
            log.exception("Routine failed")
            result = f"error: {exc}"
        r["last_run"] = time.time()
        r["last_result"] = (result or "")[:500]
        self.store.save_routine(r)
        if reschedule:
            self._reschedule(r)
