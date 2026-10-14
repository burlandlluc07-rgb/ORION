"""SQLite persistence: conversations, long-term memory, tasks, routines, usage.

One file (``orion.db``) in the data directory. Long-term memories are indexed
with FTS5 so only the few relevant facts are injected into each prompt —
important on the free tier where every token counts.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conv_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}', ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, ts);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact',
  created REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='memories', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, conv_id TEXT, title TEXT NOT NULL, status TEXT NOT NULL,
  steps TEXT NOT NULL DEFAULT '[]', plan TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS routines (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL, schedule TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, next_run REAL, last_run REAL, last_result TEXT
);
CREATE TABLE IF NOT EXISTS usage (
  model TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
  tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (model, day)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── Conversations ──────────────────────────────────────────────────────
    def create_conversation(self, title: str = "New conversation") -> dict:
        cid, now = new_id(), time.time()
        self._x("INSERT INTO conversations VALUES (?,?,?,?,0)", (cid, title, now, now))
        return {"id": cid, "title": title, "created": now, "updated": now, "pinned": False}

    def list_conversations(self, limit: int = 100) -> list[dict]:
        rows = self._q("SELECT * FROM conversations ORDER BY pinned DESC, updated DESC LIMIT ?", (limit,))
        return [dict(r) | {"pinned": bool(r["pinned"])} for r in rows]

    def get_conversation(self, cid: str) -> dict | None:
        rows = self._q("SELECT * FROM conversations WHERE id=?", (cid,))
        return dict(rows[0]) if rows else None

    def rename_conversation(self, cid: str, title: str) -> None:
        self._x("UPDATE conversations SET title=? WHERE id=?", (title[:120], cid))

    def pin_conversation(self, cid: str, pinned: bool) -> None:
        self._x("UPDATE conversations SET pinned=? WHERE id=?", (int(pinned), cid))

    def delete_conversation(self, cid: str) -> None:
        with self._lock:
            self._x("DELETE FROM messages WHERE conv_id=?", (cid,))
            self._x("DELETE FROM conversations WHERE id=?", (cid,))

    def add_message(self, cid: str, role: str, content: str, meta: dict | None = None) -> dict:
        mid, now = new_id(), time.time()
        self._x(
            "INSERT INTO messages VALUES (?,?,?,?,?,?)",
            (mid, cid, role, content, json.dumps(meta or {}), now),
        )
        self._x("UPDATE conversations SET updated=? WHERE id=?", (now, cid))
        return {"id": mid, "conv_id": cid, "role": role, "content": content, "meta": meta or {}, "ts": now}

    def messages(self, cid: str, limit: int = 500) -> list[dict]:
        rows = self._q(
            "SELECT * FROM (SELECT * FROM messages WHERE conv_id=? ORDER BY ts DESC LIMIT ?) ORDER BY ts",
            (cid, limit),
        )
        return [dict(r) | {"meta": json.loads(r["meta"])} for r in rows]

    # ── Long-term memory ───────────────────────────────────────────────────
    def remember(self, text: str, kind: str = "fact") -> int:
        text = text.strip()
        existing = self._q("SELECT id FROM memories WHERE lower(text)=lower(?)", (text,))
        if existing:
            return existing[0]["id"]
        return self._x("INSERT INTO memories(text, kind, created) VALUES (?,?,?)", (text, kind, time.time())).lastrowid

    def forget(self, memory_id: int) -> bool:
        return self._x("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    def list_memories(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM memories ORDER BY created DESC")]

    def recall(self, query: str, limit: int = 6) -> list[dict]:
        words = [w for w in re.findall(r"[A-Za-z0-9']{3,}", query.lower())][:12]
        if not words:
            return []
        fts = " OR ".join(f'"{w}"' for w in words)
        try:
            rows = self._q(
                "SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.rowid "
                "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts, limit),
            )
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    def core_memories(self, limit: int = 8) -> list[dict]:
        """Profile-type memories are always included (name, preferences)."""
        return [dict(r) for r in self._q(
            "SELECT * FROM memories WHERE kind='profile' ORDER BY created DESC LIMIT ?", (limit,))]

    # ── Tasks ──────────────────────────────────────────────────────────────
    def save_task(self, task: dict) -> None:
        self._x(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
            "status=excluded.status, steps=excluded.steps, plan=excluded.plan, updated=excluded.updated",
            (task["id"], task.get("conv_id"), task["title"], task["status"], json.dumps(task.get("steps", [])),
             json.dumps(task.get("plan", [])), task["created"], task["updated"]),
        )

    def list_tasks(self, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT * FROM tasks ORDER BY updated DESC LIMIT ?", (limit,))
        return [dict(r) | {"steps": json.loads(r["steps"]), "plan": json.loads(r["plan"])} for r in rows]

    # ── Routines (scheduled prompts) ───────────────────────────────────────
    def save_routine(self, r: dict) -> None:
        self._x(
            "INSERT INTO routines VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "prompt=excluded.prompt, schedule=excluded.schedule, enabled=excluded.enabled, "
            "next_run=excluded.next_run, last_run=excluded.last_run, last_result=excluded.last_result",
            (r["id"], r["name"], r["prompt"], r["schedule"], int(r.get("enabled", True)),
             r.get("next_run"), r.get("last_run"), r.get("last_result")),
        )

    def list_routines(self) -> list[dict]:
        return [dict(r) | {"enabled": bool(r["enabled"])} for r in self._q("SELECT * FROM routines ORDER BY name")]

    def delete_routine(self, rid: str) -> None:
        self._x("DELETE FROM routines WHERE id=?", (rid,))

    # ── Usage accounting ───────────────────────────────────────────────────
    def add_usage(self, model: str, day: str, tokens: int = 0) -> None:
        self._x(
            "INSERT INTO usage VALUES (?,?,1,?) ON CONFLICT(model, day) DO UPDATE SET "
            "requests=requests+1, tokens=tokens+excluded.tokens",
            (model, day, tokens),
        )

    def usage_for(self, day: str) -> dict[str, dict]:
        return {r["model"]: {"requests": r["requests"], "tokens": r["tokens"]}
                for r in self._q("SELECT * FROM usage WHERE day=?", (day,))}

    # ── Key/value ──────────────────────────────────────────────────────────
    def kv_get(self, k: str, default: Any = None) -> Any:
        rows = self._q("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(rows[0]["v"]) if rows else default

    def kv_set(self, k: str, v: Any) -> None:
        self._x("INSERT INTO kv VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))
