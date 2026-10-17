"""Task tracking and approvals — the 'Cowork' layer.

Every request that touches the PC becomes a *task*: a live checklist of
plan items plus every tool step with its status, so the user can see what
ORION is doing, approve risky steps, and stop it at any moment.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..events import EventBus
from ..memory.store import Store, new_id

APPROVAL_TIMEOUT = 180


@dataclass
class Task:
    id: str
    title: str
    conv_id: str | None
    source: str = "text"  # text | voice | routine
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

    def start(self, title: str, conv_id: str | None, source: str = "text") -> Task:
        t = Task(id=new_id(), title=title[:90], conv_id=conv_id, source=source)
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


_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok(?:ay)?|do it|go ahead|go for it|approved?|confirm(?:ed)?|"
                  r"proceed|affirmative|allow it|please do|absolutely|of course)\b", re.I)
_NO = re.compile(r"\b(no|nope|nah|don'?t|do not|cancel|stop|deny|denied|negative|abort|never ?mind|hold on|wait)\b", re.I)


def parse_yes_no(text: str) -> bool | None:
    """Interpret a spoken answer to "shall I…?". None when unclear (or both)."""
    t = text.strip().lower()
    if not t:
        return None
    yes, no = bool(_YES.search(t)), bool(_NO.search(t))
    if yes == no:
        return None
    return yes


class ApprovalBroker:
    """Asks the UI (and, for voice tasks, the user's voice) to approve a risky step."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}
        self.session_allow: set[str] = set()
        self.on_voice_request: Callable[[dict], Awaitable[None]] | None = None
        self.on_request: Callable[[dict], None] | None = None  # e.g. push the approval to paired phones

    async def request(self, tool: str, label: str, risk: str, args: dict[str, Any], task: Task | None,
                      desktop: bool = False) -> bool:
        """Ask the user. ``desktop`` marks a foreground step (mouse, keys, focus or a visible window): it is
        approved for this one action only, so "allow for this session" never covers it."""
        return await self.ask(tool, label, risk, args, task, desktop) in ("approve", "always")

    async def ask(self, tool: str, label: str, risk: str, args: dict[str, Any], task: Task | None,
                  desktop: bool = False, approval_id: str | None = None) -> str:
        """Like :meth:`request`, but returns the decision: approve, always, deny or expired.
        ``approval_id`` lets the caller pick the prompt's id, so it can answer the prompt itself later."""
        if tool in self.session_allow and risk != "danger" and not desktop:
            return "always"
        aid = approval_id or new_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        info = {"id": aid, "tool": tool, "label": label, "risk": risk, "args": _preview(args),
                "task_id": task.id if task else None, "expires": time.time() + APPROVAL_TIMEOUT,
                "desktop": desktop}
        self.pending[aid] = (fut, info)
        self.bus.emit("approval.request", approval=info)
        if self.on_request is not None:
            try:
                self.on_request(info)
            except Exception:
                pass
        if task is not None and task.source == "voice" and self.on_voice_request is not None:
            asyncio.create_task(self.on_voice_request(info))
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
        if decision == "always" and not desktop:
            self.session_allow.add(tool)
        return decision

    def respond(self, aid: str, decision: str) -> bool:
        item = self.pending.get(aid)
        if not item or item[0].done():
            return False
        item[0].set_result(decision)
        return True

    def voice_pending(self) -> dict | None:
        """The most recent approval that can be answered by voice."""
        items = [info for fut, info in self.pending.values() if not fut.done()]
        return items[-1] if items else None

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
