"""Recurring tasks with APScheduler and a SQLite job store, so schedules
survive restarts. A firing schedule only queues a normal task; the worker
pool runs it like any other.

Schedule specs (local time):
    "daily 08:00"            every day at 08:00
    "weekdays 07:30"         Monday to Friday
    "every 30m" / "every 2h" / "every 45s"
    "cron 0 8 * * 1"         standard 5-field crontab
    "at 2026-10-18 09:00"    once
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

log = logging.getLogger("orion_bg.scheduler")

# APScheduler stores a reference to `fire` by import path, so it needs a
# module-level way to reach the running agent.
_AGENT: Any = None


def fire(prompt: str, kind: str = "agent", schedule_id: str | None = None) -> None:
    if _AGENT is None:
        log.error("schedule %s fired with no agent attached", schedule_id)
        return
    _AGENT.submit(prompt, kind=kind, source="schedule", schedule_id=schedule_id)


_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_trigger(spec: str):
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    text = " ".join(spec.strip().split())
    low = text.lower()
    m = re.fullmatch(r"(daily|weekdays) (\d{1,2}):(\d{2})", low)
    if m:
        hour, minute = int(m.group(2)), int(m.group(3))
        dow = "mon-fri" if m.group(1) == "weekdays" else "*"
        return CronTrigger(day_of_week=dow, hour=hour, minute=minute)
    m = re.fullmatch(r"every (\d+) ?([smhd])", low)
    if m:
        amount = int(m.group(1))
        if amount <= 0:
            raise ValueError("Interval must be positive")
        return IntervalTrigger(**{_UNITS[m.group(2)]: amount})
    if low.startswith("cron "):
        return CronTrigger.from_crontab(text[5:])
    if low.startswith("at "):
        return DateTrigger(run_date=datetime.fromisoformat(text[3:].strip()))
    raise ValueError(
        f"Unrecognised schedule {spec!r}. Use 'daily 08:00', 'weekdays 07:30', "
        "'every 30m', 'cron 0 8 * * *' or 'at 2026-10-18 09:00'."
    )


class Scheduler:
    def __init__(self, agent: Any, db_path: Path | None = None):
        global _AGENT
        from apscheduler.executors.pool import ThreadPoolExecutor
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.schedulers.background import BackgroundScheduler

        _AGENT = agent
        db_path = db_path or paths.schedules_db()
        # Instances, not alias strings: alias lookup uses entry points, which
        # PyInstaller builds do not carry.
        self._sched = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
            executors={"default": ThreadPoolExecutor(2)},
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
        )
        self.agent = agent
        agent.attach_scheduler(self)

    def start(self) -> None:
        if not self._sched.running:
            self._sched.start()
            log.info("scheduler started with %d schedule(s)", len(self._sched.get_jobs()))

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)

    def add(self, prompt: str, spec: str, kind: str = "agent", name: str | None = None) -> dict:
        if not prompt.strip():
            raise ValueError("prompt is empty")
        trigger = parse_trigger(spec)
        schedule_id = uuid.uuid4().hex[:12]
        self._sched.add_job(
            fire, trigger=trigger, id=schedule_id, name=name or prompt[:60],
            kwargs={"prompt": prompt, "kind": kind, "schedule_id": schedule_id},
            replace_existing=True,
        )
        log.info("schedule %s added: %s", schedule_id, spec)
        return self.get(schedule_id)  # type: ignore[return-value]

    def remove(self, schedule_id: str) -> bool:
        from apscheduler.jobstores.base import JobLookupError

        try:
            self._sched.remove_job(schedule_id)
            return True
        except JobLookupError:
            return False

    def get(self, schedule_id: str) -> dict | None:
        job = self._sched.get_job(schedule_id)
        return self._describe(job) if job else None

    def list(self) -> list[dict]:
        return [self._describe(j) for j in self._sched.get_jobs()]

    @staticmethod
    def _describe(job: Any) -> dict:
        nxt = getattr(job, "next_run_time", None)
        return {
            "id": job.id,
            "name": job.name,
            "prompt": job.kwargs.get("prompt"),
            "kind": job.kwargs.get("kind"),
            "trigger": str(job.trigger),
            "next_run": nxt.isoformat() if nxt else None,
        }
