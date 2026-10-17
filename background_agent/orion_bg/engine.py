"""BackgroundAgent: the in-process interface ORION wires into.

    agent = BackgroundAgent(config.load())
    agent.start()
    task = agent.submit("Summarise C:/Users/me/Documents/Orion/notes.txt")
    agent.get(task["id"])
    agent.pending_approvals(); agent.approve(approval_id)
    agent.stop()

The daemon (daemon.py) wraps one of these and exposes it over a localhost
HTTP API, so a UI can come and go without stopping tasks.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from . import paths
from .notify import Notifier
from .runners import (Completed, DummyRunner, Failed, NeedsApproval, RunContext, Runner,
                      TaskCancelled, TaskTimedOut)
from .store import CANCELLED, COMPLETED, FAILED, Store

log = logging.getLogger("orion_bg.engine")

Listener = Callable[[str, dict], None]


class BackgroundAgent:
    def __init__(self, cfg: dict, store: Store | None = None, notifier: Notifier | None = None,
                 runners: dict[str, Runner] | None = None):
        self.cfg = cfg
        self.store = store or Store(paths.tasks_db())
        self.notifier = notifier or Notifier(cfg)
        self.runners: dict[str, Runner] = {"dummy": DummyRunner(cfg["tasks"]["dummy_seconds"])}
        if runners:
            self.runners.update(runners)
        self.scheduler = None  # set by attach_scheduler()
        self._listeners: list[Listener] = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._threads: list[threading.Thread] = []
        self._active: dict[str, str] = {}  # thread name -> task id
        self._active_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if "agent" not in self.runners:
            self.runners["agent"] = self._make_agent_runner()
        recovered = self.store.recover()
        if recovered:
            log.info("re-queued %d task(s) left running by the last session", recovered)
        workers = max(1, int(self.cfg["daemon"]["workers"]))
        for i in range(workers):
            t = threading.Thread(target=self._worker, name=f"worker-{i + 1}", daemon=True)
            t.start()
            self._threads.append(t)
        if self.scheduler:
            self.scheduler.start()
        log.info("background agent started with %d worker(s)", workers)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self.scheduler:
            self.scheduler.shutdown()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()
        log.info("background agent stopped")

    def _make_agent_runner(self) -> Runner:
        from .agent_loop import make_runner

        return make_runner(self.cfg)

    def attach_scheduler(self, scheduler: Any) -> None:
        self.scheduler = scheduler

    # -- events ------------------------------------------------------------

    def subscribe(self, listener: Listener) -> None:
        """listener(event, task) for: submitted, started, needs_approval,
        requeued, completed, failed, cancelled."""
        self._listeners.append(listener)

    def _emit(self, event: str, task: dict | None) -> None:
        if task is None:
            return
        for listener in list(self._listeners):
            try:
                listener(event, task)
            except Exception:
                log.exception("listener failed for %s", event)

    # -- public API --------------------------------------------------------

    def submit(self, prompt: str, kind: str = "agent", source: str = "api",
               timeout: float | None = None, schedule_id: str | None = None) -> dict:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is empty")
        if kind not in self.runners and kind != "agent":  # "agent" is created in start()
            raise ValueError(f"unknown task kind {kind!r}; known: {sorted(self.runners)}")
        task = self.store.add(prompt, kind=kind, source=source, timeout=timeout, schedule_id=schedule_id)
        log.info("task %s queued from %s (%s)", task["id"], source, kind)
        self._emit("submitted", task)
        self._wake.set()
        return task

    def get(self, task_id: str) -> dict | None:
        return self.store.get(task_id)

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        return self.store.list(status=status, limit=limit)

    def cancel(self, task_id: str) -> dict | None:
        task = self.store.request_cancel(task_id)
        if task and task["status"] == CANCELLED:
            self._emit("cancelled", task)
        return task

    def pending_approvals(self, task_id: str | None = None) -> list[dict]:
        return self.store.approvals("pending", task_id=task_id)

    def approve(self, approval_id: str) -> dict | None:
        return self._decide(approval_id, True)

    def deny(self, approval_id: str) -> dict | None:
        return self._decide(approval_id, False)

    def _decide(self, approval_id: str, approved: bool) -> dict | None:
        task = self.store.decide(approval_id, approved)
        log.info("approval %s %s", approval_id, "approved" if approved else "denied")
        if task:
            self._emit("requeued", task)
            self._wake.set()
        return task

    def pause(self) -> None:
        """Stop starting new tasks. Running ones finish their current step."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def status(self) -> dict:
        with self._active_lock:
            running = list(self._active.values())
        return {
            "paused": self.paused,
            "workers": len(self._threads),
            "running": running,
            "counts": self.store.counts(),
            "pending_approvals": len(self.store.approvals("pending")),
            "scheduler": bool(self.scheduler),
        }

    def wait(self, task_id: str, timeout: float = 30.0, until=("completed", "failed", "cancelled")) -> dict:
        """Block until the task reaches one of `until` (for tests and the CLI)."""
        end = time.monotonic() + timeout
        while True:
            task = self.store.get(task_id)
            if task is None or task["status"] in until or time.monotonic() > end:
                return task  # type: ignore[return-value]
            time.sleep(0.05)

    # -- workers -----------------------------------------------------------

    def _worker(self) -> None:
        name = threading.current_thread().name
        while not self._stop.is_set():
            if self._paused.is_set():
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            task = self.store.claim_next()
            if task is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            with self._active_lock:
                self._active[name] = task["id"]
            try:
                self._run(task)
            except Exception:
                log.exception("worker crashed on task %s", task["id"])
                self.store.finish(task["id"], FAILED, error="Internal error; see the log")
                self._emit("failed", self.store.get(task["id"]))
            finally:
                with self._active_lock:
                    self._active.pop(name, None)

    def _run(self, task: dict) -> None:
        task_id = task["id"]
        runner = self.runners.get(task["kind"])
        if runner is None:
            self.store.finish(task_id, FAILED, error=f"Unknown task kind {task['kind']!r}")
            self._emit("failed", self.store.get(task_id))
            return
        limit = task["timeout"] or float(self.cfg["tasks"]["timeout_seconds"])
        budget = max(0.0, limit - (task["run_seconds"] or 0.0))
        started = time.monotonic()
        ctx = RunContext(
            task=task,
            deadline=started + budget,
            is_cancelled=lambda: self._stop.is_set() or self.store.cancel_requested(task_id),
            save_state=lambda state: self.store.save_state(task_id, state),
            notify=self.notifier.send,
        )
        log.info("task %s started (%s, %.0fs budget left)", task_id, task["kind"], budget)
        self._emit("started", task)
        try:
            outcome = runner.run(task, ctx)
        except TaskCancelled:
            outcome = None
            if self._stop.is_set() and not self.store.cancel_requested(task_id):
                # Daemon shutting down: leave it running so recover() re-queues it.
                self.store.add_run_time(task_id, time.monotonic() - started)
                log.info("task %s interrupted by shutdown; will resume", task_id)
                return
            self.store.finish(task_id, CANCELLED, error="Cancelled by user")
        except TaskTimedOut:
            outcome = None
            self.store.finish(task_id, FAILED, error=f"Timed out after {limit:.0f} s")
        except Exception as exc:
            log.exception("task %s raised", task_id)
            outcome = Failed(f"{type(exc).__name__}: {exc}")
        self.store.add_run_time(task_id, time.monotonic() - started)

        if isinstance(outcome, Completed):
            self.store.finish(task_id, COMPLETED, result=outcome.result)
        elif isinstance(outcome, Failed):
            self.store.finish(task_id, FAILED, error=outcome.error)
        elif isinstance(outcome, NeedsApproval):
            approvals = self.store.park_for_approval(task_id, outcome.state, outcome.requests)
            done = self.store.get(task_id)
            log.info("task %s waiting on %d approval(s)", task_id, len(approvals))
            self._emit("needs_approval", done)
            if self.cfg["notify"]["on_approval"]:
                tools = ", ".join(a["tool"] for a in approvals)
                self.notifier.send("Orion needs your OK", f"{_short(task['prompt'])}: {tools}")
            return

        done = self.store.get(task_id)
        assert done is not None
        log.info("task %s %s", task_id, done["status"])
        self._emit(done["status"], done)
        if done["status"] == COMPLETED and self.cfg["notify"]["on_complete"]:
            self.notifier.send("Orion finished a task", f"{_short(task['prompt'])}\n{done['result'] or ''}")
        elif done["status"] == FAILED and self.cfg["notify"]["on_failure"]:
            self.notifier.send("Orion task failed", f"{_short(task['prompt'])}\n{done['error'] or ''}")


def _short(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."
