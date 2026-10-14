"""Task tracking and approvals — the 'Cowork' layer.

Every request that touches the PC becomes a *task*: a live checklist of
plan items plus every tool step with its status, so the user can see what
ORION is doing, approve risky steps, and stop it at any moment.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..events import EventBus
from ..memory.store import Store, new_id

APPROVAL_TIMEOUT = 180


@dataclass
class Task:
    id: str
    title: str
    conv_id: str | None
    status: str = "running"  # running | waiting | done | failed | stopped
    plan: list[dict] = field(default_factory=list)  # [{"text":…, "status": pending|active|done}]
    steps: list[dict] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "conv_id": self.conv_id, "status": self.status,
                "plan": self.plan, "steps": self.steps, "created": self.created, "updated": self.updated}


class TaskTracker:
    def __init__(self, bus: EventBus, store: Store) -> None:
        self.bus = bus
        self.store = store
        self.active: dict[str, Task] = {}

    def start(self, title: str, conv_id: str | None) -> Task:
        t = Task(id=new_id(), title=title[:90], conv_id=conv_id)
        self.active[t.id] = t
        self._emit(t)
        return t

    def _emit(self, t: Task) -> None:
        t.updated = time.time()
        self.bus.emit("task", task=t.to_dict())

    def set_plan(self, t: Task, items: list[str]) -> None:
        old = {p["text"]: p["status"] for p in t.plan}
        t.plan = [{"text": s[:140], "status": old.get(s[:140], "pending")} for s in items[:12]]
        self._emit(t)

    def mark_plan(self, t: Task, index: int, status: str) -> None:
        if 0 <= index < len(t.plan):
            t.plan[index]["status"] = status
            self._emit(t)

    def add_step(self, t: Task, tool: str, label: str, risk: str) -> dict:
        step = {"id": new_id(), "tool": tool, "label": label, "risk": risk, "status": "running",
                "result": None, "started": time.time(), "ended": None}
        t.steps.append(step)
        self._emit(t)
        return step

    def update_step(self, t: Task, step: dict, status: str, result: str | None = None) -> None:
        step["status"] = status
        if result is not None:
            step["result"] = result[:600]
        if status not in ("running", "waiting"):
            step["ended"] = time.time()
        t.status = "waiting" if status == "waiting" else ("running" if t.status == "waiting" else t.status)
        self._emit(t)

    def finish(self, t: Task, status: str = "done") -> None:
        if t.status not in ("stopped",):
            t.status = status
        if status == "done":
            for p in t.plan:
                if p["status"] == "active":
                    p["status"] = "done"
        self._emit(t)
        self.active.pop(t.id, None)
        if t.steps:  # only persist tasks that actually did something
            self.store.save_task(t.to_dict())

    def stop_all(self) -> int:
        n = 0
        for t in list(self.active.values()):
            t.cancel.set()
            t.status = "stopped"
            self._emit(t)
            n += 1
        return n


class ApprovalBroker:
    """Asks the UI to approve a risky step and waits for the answer."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}
        self.session_allow: set[str] = set()

    async def request(self, tool: str, label: str, risk: str, args: dict[str, Any], task: Task | None) -> bool:
        if tool in self.session_allow and risk != "danger":
            return True
        aid = new_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        info = {"id": aid, "tool": tool, "label": label, "risk": risk, "args": _preview(args),
                "task_id": task.id if task else None, "expires": time.time() + APPROVAL_TIMEOUT}
        self.pending[aid] = (fut, info)
        self.bus.emit("approval.request", approval=info)
        try:
            while True:
                try:
                    decision = await asyncio.wait_for(asyncio.shield(fut), timeout=0.5)
                    break
                except asyncio.TimeoutError:
                    if task and task.cancel.is_set():
                        decision = "deny"
                        break
                    if time.time() > info["expires"]:
                        decision = "expired"
                        break
        finally:
            self.pending.pop(aid, None)
            self.bus.emit("approval.resolved", id=aid)
        if decision == "always":
            self.session_allow.add(tool)
        return decision in ("approve", "always")

    def respond(self, aid: str, decision: str) -> bool:
        item = self.pending.get(aid)
        if not item or item[0].done():
            return False
        item[0].set_result(decision)
        return True

    def deny_all(self) -> None:
        for fut, _ in list(self.pending.values()):
            if not fut.done():
                fut.set_result("deny")

    def list(self) -> list[dict]:
        return [info for _, info in self.pending.values()]


def _preview(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        s = v if isinstance(v, (int, float, bool)) else str(v)
        if isinstance(s, str) and len(s) > 1500:
            s = s[:1500] + "…"
        out[k] = s
    return out
