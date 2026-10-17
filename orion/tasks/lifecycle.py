"""Task records and the common executor interface.

Every action ORION takes (each tool call) gets a :class:`TaskRecord` that walks one lifecycle::

    queued → validating → awaiting_permission (only when needed) → running → verifying → completed
    other outcomes: failed | cancel_requested → cancelled | needs_attention

The record keeps who/what/when: the executor, the target, every stage change with its time, the
approval scope, log lines, outputs and the verification evidence. It is stored on the task step
(``step["lifecycle"]``), so the UI, the phone and the task history all show the same record.

Executors share one interface (:class:`Executor`): ``capabilities()``, ``validate()``, ``execute()``,
``cancel()`` and ``verify()``. :class:`ToolExecutor` wraps an existing registry tool without changing
what it does, so every current tool already runs through the interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..tools.registry import Tool, ToolContext

STAGES = ("queued", "validating", "awaiting_permission", "running", "verifying", "completed", "failed",
          "cancel_requested", "cancelled", "needs_attention")
TERMINAL = {"completed", "failed", "cancelled", "needs_attention"}
_NEXT: dict[str, set[str]] = {
    "queued": {"validating", "cancel_requested", "cancelled", "failed"},
    "validating": {"awaiting_permission", "running", "failed", "cancel_requested", "cancelled"},
    "awaiting_permission": {"running", "cancelled", "failed", "cancel_requested"},
    "running": {"verifying", "failed", "cancel_requested", "awaiting_permission"},
    "verifying": {"completed", "needs_attention", "failed"},
    "cancel_requested": {"cancelled", "verifying", "failed"},
}

# How a step's lifecycle stage shows in the existing task UI (step["status"]).
STEP_STATUS = {"queued": "running", "validating": "running", "awaiting_permission": "waiting", "running": "running",
               "verifying": "running", "completed": "done", "failed": "failed", "cancel_requested": "running",
               "cancelled": "stopped", "needs_attention": "attention"}


@dataclass(frozen=True)
class Capabilities:
    """What an executor can safely do, declared up front so the router and the permission gate can decide."""

    route: str = "local"  # api_mcp | cli | headless_browser | app_automation | gui_env | foreground | local
    desktop: str = "none"  # none | visible (opens or changes a visible window) | input (mouse, keys, focus)
    needs_desktop: bool = False  # needs the user's interactive desktop to work at all
    changes_external: bool = False  # changes data outside this PC (email, messages, remote services)
    changes_local: bool = False  # changes files or settings on this PC
    cancellable: bool = False  # stops cooperatively when asked
    rollback: bool = False  # ORION can undo it
    evidence: str = ""  # what proves it worked

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class TaskRecord:
    id: str
    type: str  # the action, e.g. the tool name
    executor: str
    target: str = ""
    task_id: str | None = None
    origin: str = "assistant"  # assistant | command (the user's exact instant command) | user (their own button)
    stage: str = "queued"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)
    approval: dict | None = None
    logs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    verification: dict | None = None
    capabilities: dict = field(default_factory=dict)
    on_change: Callable[["TaskRecord"], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.history:
            self.history.append({"stage": self.stage, "at": self.created})

    @property
    def done(self) -> bool:
        return self.stage in TERMINAL

    def advance(self, stage: str, note: str = "") -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage {stage!r}")
        if stage == self.stage:
            return
        if stage not in _NEXT.get(self.stage, set()):
            raise ValueError(f"Illegal lifecycle step {self.stage} → {stage}")
        self.stage = stage
        self.updated = time.time()
        entry = {"stage": stage, "at": self.updated}
        if note:
            entry["note"] = note[:300]
        self.history.append(entry)
        self._changed()

    def log(self, message: str) -> None:
        self.logs.append(f"{time.strftime('%H:%M:%S')} {message}"[:500])
        del self.logs[:-40]
        self.updated = time.time()
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "executor": self.executor, "target": self.target,
                "task_id": self.task_id, "origin": self.origin, "stage": self.stage, "created": self.created,
                "updated": self.updated, "history": list(self.history), "approval": self.approval,
                "logs": list(self.logs), "outputs": list(self.outputs), "verification": self.verification,
                "capabilities": self.capabilities}


class Executor:
    """The interface every executor implements."""

    name = "executor"

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def validate(self, record: TaskRecord, args: dict) -> None:
        """Raise ToolError when the arguments, target or permissions are not valid."""

    async def execute(self, record: TaskRecord, args: dict, ctx: "ToolContext") -> str:
        raise NotImplementedError

    def cancel(self, record: TaskRecord) -> None:
        """Ask a running execution to stop. Cancellation is cooperative; see Agent.run_tool for the stages."""

    def verify(self, record: TaskRecord, args: dict, result: str) -> dict:
        """→ {"status": passed | failed | unverified | not_applicable, "evidence": [...]}"""
        return {"status": "unverified", "evidence": []}


class ToolExecutor(Executor):
    """Wraps an existing registry tool behind the executor interface without changing its behaviour."""

    def __init__(self, tool: "Tool", caps: Capabilities | None = None,
                 verifier: Callable[[dict, str], dict] | None = None) -> None:
        self.tool = tool
        self.name = f"tool:{tool.source}" if tool.source != "core" else "tool"
        self._caps = caps or Capabilities()
        self._verifier = verifier
        self._contexts: dict[str, Any] = {}

    def capabilities(self) -> Capabilities:
        return self._caps

    def validate(self, record: TaskRecord, args: dict) -> None:
        from ..tools.registry import ToolError

        props = (self.tool.parameters or {}).get("properties", {})
        missing = [p for p in (self.tool.parameters or {}).get("required", []) if p not in args]
        if missing:
            raise ToolError(f"Bad arguments for {self.tool.name}: missing {', '.join(missing)}")
        unknown = [k for k in args if props and k not in props]
        if unknown:
            raise ToolError(f"Bad arguments for {self.tool.name}: unexpected {', '.join(unknown)}")

    async def execute(self, record: TaskRecord, args: dict, ctx: "ToolContext") -> str:
        self._contexts[record.id] = ctx
        try:
            return await self.tool.run(args, ctx)
        finally:
            self._contexts.pop(record.id, None)

    def cancel(self, record: TaskRecord) -> None:
        ctx = self._contexts.get(record.id)
        if ctx is not None:
            ctx.cancel.set()

    def verify(self, record: TaskRecord, args: dict, result: str) -> dict:
        if self._verifier is not None:
            return self._verifier(args, result)
        caps = self._caps
        if not (caps.changes_local or caps.changes_external or caps.desktop != "none"):
            return {"status": "not_applicable", "evidence": ["read-only: the result itself is the answer"]}
        return {"status": "unverified", "evidence": ["no independent check exists for this action yet"]}
