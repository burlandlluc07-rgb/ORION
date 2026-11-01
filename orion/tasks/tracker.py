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

APPROVAL_TIMEOUT = 180  # the limit when a caller asks for one (e.g. a desktop fallback inside a running tool)


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


# A typed or spoken message only answers an approval when it is a short, plain yes or no. Anything longer or
# mixed ("yes but send it to Ana instead") is an ordinary message and the approval keeps waiting.
_REPLY_MAX_WORDS = 6
_YES_ONLY = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do", "it", "go", "ahead", "for", "approve",
             "approved", "confirm", "confirmed", "proceed", "affirmative", "allow", "please", "absolutely", "of",
             "course", "send", "that", "orion", "thanks", "thank", "you", "fine", "right", "now"}
# An approval must have been on screen this long before a yes counts, so a "yes" typed for something else
# can't land on a prompt that popped up a moment before it was sent.
REPLY_MIN_AGE = 1.0


def parse_approval_reply(text: str) -> bool | None:
    """Like :func:`parse_yes_no`, but stricter: None unless the whole message is a short yes or no."""
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > _REPLY_MAX_WORDS:
        return None
    answer = parse_yes_no(text)
    if answer is True and not all(w in _YES_ONLY for w in words):
        return None
    return answer


class ApprovalBroker:
    """Asks the UI (and, for voice tasks, the user's voice) to approve a risky step.

    Waiting requests form a queue in arrival order. By default a request waits until it is answered (it never
    folds or expires on its own); one can be parked with "Later", which moves it to the back of the queue
    without answering it, and :meth:`focus` brings the front one up again (the approvals hotkey). Nothing in
    the queue is ever approved without an explicit answer."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}
        # Seconds a request waits before it lapses (not done); 0 = until answered. Set from Settings by the engine.
        self.timeout: Callable[[], float] = lambda: 0
        self.session_allow: set[str] = set()
        self.on_voice_request: Callable[[dict], Awaitable[None]] | None = None
        self.on_request: Callable[[dict], None] | None = None  # e.g. push the approval to paired phones
        self.voice_active: Callable[[], bool] | None = None  # a voice session is open: ask out loud for any task

    async def request(self, tool: str, label: str, risk: str, args: dict[str, Any], task: Task | None,
                      desktop: bool = False, timeout: float | None = None) -> bool:
        """Ask the user. ``desktop`` marks a foreground step (mouse, keys, focus or a visible window): it is
        approved for this one action only, so "allow for this session" never covers it."""
        return await self.ask(tool, label, risk, args, task, desktop, timeout=timeout) in ("approve", "always")

    async def ask(self, tool: str, label: str, risk: str, args: dict[str, Any], task: Task | None,
                  desktop: bool = False, approval_id: str | None = None, timeout: float | None = None) -> str:
        """Like :meth:`request`, but returns the decision: approve, always, deny or expired.
        ``approval_id`` lets the caller pick the prompt's id, so it can answer the prompt itself later.
        ``timeout`` overrides the setting (seconds; 0 = wait until answered)."""
        if tool in self.session_allow and risk != "danger" and not desktop:
            return "always"
        aid = approval_id or new_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        if timeout is None:
            try:
                timeout = float(self.timeout() or 0)
            except Exception:
                timeout = 0
        info = {"id": aid, "tool": tool, "label": label, "risk": risk, "args": _preview(args),
                "task_id": task.id if task else None,
                "expires": time.time() + timeout if timeout and timeout > 0 else None,
                "desktop": desktop, "created": time.time(), "source": task.source if task else None,
                "parked": False}
        self.pending[aid] = (fut, info)
        self.bus.emit("approval.request", approval=info)
        if self.on_request is not None:
            try:
                self.on_request(info)
            except Exception:
                pass
        spoken = task is not None and task.source == "voice"
        if not spoken and self.voice_active is not None:
            try:
                spoken = self.voice_active()
            except Exception:
                spoken = False
        if spoken and self.on_voice_request is not None:
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
                    if info["expires"] is not None and time.time() > info["expires"]:
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

    def answer_from_text(self, text: str, conv_id: str | None = None, tasks: dict | None = None) -> dict | None:
        """Treat a typed or spoken message as the answer to one pending approval.

        Returns ``{"id", "approved"}`` when it answered one, ``{"ambiguous": n}`` when it was a clear yes/no
        but several approvals are waiting (none is touched), else None (not an answer: handle the message
        normally and leave every approval pending). A yes approves that one step only, never the session."""
        answer = parse_approval_reply(text)
        if answer is None:
            return None
        now = time.time()
        waiting = [info for fut, info in self.pending.values()
                   if not fut.done() and now - info.get("created", 0) >= REPLY_MIN_AGE]
        if len(waiting) > 1 and conv_id and tasks is not None:
            here = [i for i in waiting if i.get("task_id") and getattr(tasks.get(i["task_id"]), "conv_id", None) == conv_id]
            if len(here) == 1:
                waiting = here
        if not waiting:
            return None
        if len(waiting) > 1:
            return {"ambiguous": len(waiting)}
        info = waiting[0]
        if not self.respond(info["id"], "approve" if answer else "deny"):
            return None
        self.bus.emit("approval.answered", id=info["id"], approved=answer)
        return {"id": info["id"], "approved": answer, "label": info["label"]}

    def park(self, aid: str, parked: bool = True) -> bool:
        """"Later": move a request to the back of the queue without answering it (``parked=False`` reopens it)."""
        item = self.pending.get(aid)
        if not item or item[0].done():
            return False
        item[1]["parked"] = bool(parked)
        self.pending[aid] = self.pending.pop(aid)  # re-insert: parked ones go behind everything waiting now
        self.bus.emit("approval.updated", approval=item[1], queue=self.queue_ids())
        return True

    def queue(self) -> list[dict]:
        """Waiting requests, front first: unparked ones in arrival order, then parked ones."""
        items = [info for fut, info in self.pending.values() if not fut.done()]
        return [i for i in items if not i.get("parked")] + [i for i in items if i.get("parked")]

    def queue_ids(self) -> list[str]:
        return [i["id"] for i in self.queue()]

    def focus(self) -> dict | None:
        """Bring the front request up (the approvals hotkey). A parked one is reopened when nothing else waits.
        Only shows it: it is never answered here."""
        q = self.queue()
        if not q:
            return None
        front = q[0]
        if front.get("parked"):
            self.park(front["id"], False)
        self.bus.emit("approval.focus", id=front["id"])
        return front

    def voice_pending(self) -> dict | None:
        """The most recent approval that can be answered by voice."""
        items = [info for fut, info in self.pending.values() if not fut.done()]
        return items[-1] if items else None

    def deny_all(self) -> None:
        for fut, _ in list(self.pending.values()):
            if not fut.done():
                fut.set_result("deny")

    def list(self) -> list[dict]:
        return self.queue()


def _preview(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        s = v if isinstance(v, (int, float, bool)) else str(v)
        if isinstance(s, str) and len(s) > 1500:
            s = s[:1500] + "…"
        out[k] = s
    return out
