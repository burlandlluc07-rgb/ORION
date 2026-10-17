"""Persistent task queue and approval records in SQLite.

Statuses:
    queued -> running -> completed | failed | cancelled
                      -> needs_approval -> queued (after every approval is decided)

A task that was `running` when the daemon died is put back to `queued` on the
next start and resumes from the last saved agent state.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

QUEUED = "queued"
RUNNING = "running"
NEEDS_APPROVAL = "needs_approval"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

STATUSES = (QUEUED, RUNNING, NEEDS_APPROVAL, COMPLETED, FAILED, CANCELLED)
FINAL = (COMPLETED, FAILED, CANCELLED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'agent',
    source TEXT NOT NULL DEFAULT 'api',
    status TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    started REAL,
    finished REAL,
    result TEXT,
    error TEXT,
    state TEXT,
    run_seconds REAL NOT NULL DEFAULT 0,
    timeout REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    schedule_id TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    call_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    input TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created REAL NOT NULL,
    decided REAL
);
CREATE INDEX IF NOT EXISTS approvals_task ON approvals(task_id, status);
"""


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _task(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        task = dict(row)
        task["state"] = json.loads(task["state"]) if task["state"] else None
        task["cancel_requested"] = bool(task["cancel_requested"])
        return task

    @staticmethod
    def _approval(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["input"] = json.loads(item["input"])
        return item

    def _update(self, task_id: str, **fields: Any) -> None:
        fields["updated"] = time.time()
        if "state" in fields and not isinstance(fields["state"], (str, type(None))):
            fields["state"] = json.dumps(fields["state"])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))

    # -- tasks -------------------------------------------------------------

    def add(self, prompt: str, kind: str = "agent", source: str = "api",
            timeout: float | None = None, schedule_id: str | None = None) -> dict:
        now = time.time()
        task_id = _new_id()
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks (id, prompt, kind, source, status, created, updated, timeout, schedule_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, prompt, kind, source, QUEUED, now, now, timeout, schedule_id),
            )
        return self.get(task_id)  # type: ignore[return-value]

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task(row)

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            if status:
                rows = self._db.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY created DESC LIMIT ?", (status, limit)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._task(r) for r in rows]  # type: ignore[misc]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        out = {s: 0 for s in STATUSES}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    def claim_next(self) -> dict | None:
        """Atomically move the oldest queued task to running."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT id FROM tasks WHERE status = ? ORDER BY created LIMIT 1", (QUEUED,)
                ).fetchone()
                if row is None:
                    self._db.execute("COMMIT")
                    return None
                now = time.time()
                self._db.execute(
                    "UPDATE tasks SET status = ?, updated = ?, started = COALESCE(started, ?) WHERE id = ?",
                    (RUNNING, now, now, row["id"]),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return self.get(row["id"])

    def save_state(self, task_id: str, state: Any) -> None:
        with self._lock:
            self._update(task_id, state=state)

    def add_run_time(self, task_id: str, seconds: float) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET run_seconds = run_seconds + ?, updated = ? WHERE id = ?",
                (seconds, time.time(), task_id),
            )

    def finish(self, task_id: str, status: str, result: str | None = None, error: str | None = None) -> None:
        assert status in FINAL
        with self._lock:
            self._update(task_id, status=status, result=result, error=error, finished=time.time())
            if status != COMPLETED:
                self._db.execute(
                    "UPDATE approvals SET status = 'expired', decided = ? WHERE task_id = ? AND status = 'pending'",
                    (time.time(), task_id),
                )

    def request_cancel(self, task_id: str) -> dict | None:
        """Queued or waiting tasks are cancelled at once; running ones at the next check."""
        with self._lock:
            task = self.get(task_id)
            if task is None or task["status"] in FINAL:
                return task
            if task["status"] in (QUEUED, NEEDS_APPROVAL):
                self.finish(task_id, CANCELLED, error="Cancelled by user")
            else:
                self._update(task_id, cancel_requested=1)
        return self.get(task_id)

    def cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT cancel_requested FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def recover(self) -> int:
        """Re-queue tasks that were running when the daemon stopped."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = ?, updated = ? WHERE status = ?", (QUEUED, time.time(), RUNNING)
            )
        return cur.rowcount

    # -- approvals ---------------------------------------------------------

    def park_for_approval(self, task_id: str, state: Any, requests: list[dict]) -> list[dict]:
        """Save state, create approval rows and set the task to needs_approval."""
        now = time.time()
        created = []
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for req in requests:
                    approval_id = _new_id()
                    self._db.execute(
                        "INSERT INTO approvals (id, task_id, call_id, tool, input, reason, created)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (approval_id, task_id, req["call_id"], req["tool"], json.dumps(req["input"]),
                         req["reason"], now),
                    )
                    created.append(approval_id)
                self._update(task_id, status=NEEDS_APPROVAL, state=state)
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return [self.get_approval(a) for a in created]  # type: ignore[misc]

    def get_approval(self, approval_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return self._approval(row) if row else None

    def approvals(self, status: str | None = "pending", task_id: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM approvals WHERE 1=1", []
        if status:
            query += " AND status = ?"
            args.append(status)
        if task_id:
            query += " AND task_id = ?"
            args.append(task_id)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY created", args).fetchall()
        return [self._approval(r) for r in rows]

    def decide(self, approval_id: str, approved: bool) -> dict | None:
        """Record a decision. When the task has no pending approvals left, the
        decisions are written into its state and it is queued again.

        Returns the task when it was re-queued, else None.
        """
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,)
                ).fetchone()
                if row is None:
                    self._db.execute("COMMIT")
                    raise KeyError(f"No pending approval {approval_id}")
                self._db.execute(
                    "UPDATE approvals SET status = ?, decided = ? WHERE id = ?",
                    ("approved" if approved else "denied", time.time(), approval_id),
                )
                task_id = row["task_id"]
                left = self._db.execute(
                    "SELECT COUNT(*) FROM approvals WHERE task_id = ? AND status = 'pending'", (task_id,)
                ).fetchone()[0]
                requeued = False
                if left == 0:
                    task = self.get(task_id)
                    if task and task["status"] == NEEDS_APPROVAL:
                        decided = self._db.execute(
                            "SELECT call_id, status FROM approvals WHERE task_id = ?"
                            " AND status IN ('approved', 'denied')",
                            (task_id,),
                        ).fetchall()
                        state = task["state"] or {}
                        decisions = state.setdefault("decisions", {})
                        for d in decided:
                            decisions[d["call_id"]] = d["status"] == "approved"
                        self._update(task_id, status=QUEUED, state=state)
                        requeued = True
                self._db.execute("COMMIT")
            except KeyError:
                raise
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return self.get(task_id) if requeued else None
