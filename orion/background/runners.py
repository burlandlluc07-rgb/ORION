"""What a worker runs for a task.

A runner gets the task and a RunContext and returns one outcome:
    Completed(result)            the task is done
    Failed(error)                the task failed
    NeedsApproval(state, calls)  park the task until the user decides

Runners must check `ctx.check()` between steps (it raises on cancel or timeout)
and save resumable progress with `ctx.save_state()`. When an approval round is
decided, the task comes back with `task["state"]["decisions"]` = {call_id: bool}.

New kinds of work (ORION's own tools, a Gemini runner...) plug in by
registering another Runner under a new `kind`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class TaskCancelled(Exception):
    pass


class TaskTimedOut(Exception):
    pass


@dataclass
class Completed:
    result: str


@dataclass
class Failed:
    error: str


@dataclass
class NeedsApproval:
    state: Any
    # each: {"call_id", "tool", "input", "reason"}
    requests: list[dict] = field(default_factory=list)


Outcome = Completed | Failed | NeedsApproval


class RunContext:
    def __init__(self, task: dict, deadline: float, is_cancelled: Callable[[], bool],
                 save_state: Callable[[Any], None], notify: Callable[[str, str], None]):
        self.task = task
        self.deadline = deadline
        self._is_cancelled = is_cancelled
        self._save_state = save_state
        self.notify = notify

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self) -> None:
        if self._is_cancelled():
            raise TaskCancelled()
        if time.monotonic() >= self.deadline:
            raise TaskTimedOut()

    def sleep(self, seconds: float) -> None:
        """Interruptible sleep that still honours cancel and timeout."""
        end = time.monotonic() + seconds
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    def save_state(self, state: Any) -> None:
        self._save_state(state)


class Runner(Protocol):
    def run(self, task: dict, ctx: RunContext) -> Outcome: ...


class DummyRunner:
    """Waits a moment and echoes the prompt. Proves the daemon, queue, worker
    pool, cancel, timeout and toast work without an API key.

    If the prompt contains "[approval]" it first asks for approval of a fake
    action, which tests the approval round trip end to end.
    """

    def __init__(self, seconds: float = 2.0):
        self.seconds = seconds

    def run(self, task: dict, ctx: RunContext) -> Outcome:
        state = task.get("state") or {}
        prompt = task["prompt"]
        if "[approval]" in prompt and "decisions" not in state:
            ctx.sleep(self.seconds / 2)
            return NeedsApproval(
                state={"phase": "waiting"},
                requests=[{
                    "call_id": "dummy-1",
                    "tool": "dummy_action",
                    "input": {"prompt": prompt},
                    "reason": "Dummy task asks for approval to test the gate",
                }],
            )
        ctx.sleep(self.seconds)
        if "decisions" in state:
            verdict = "approved" if state["decisions"].get("dummy-1") else "denied"
            return Completed(f"Dummy action was {verdict}. Prompt: {prompt}")
        return Completed(f"Dummy task done: {prompt}")
