"""Deep memory: ORION learns facts from conversations by itself and files them in the vault.

When a conversation goes quiet, the fast model reads what's new and extracts
lasting facts (people, projects, preferences, plans). Near-duplicates are
skipped, a daily cap protects the free quota, and everything learned shows up
in Memory where it can be deleted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine import Engine

from .vault import is_duplicate

log = logging.getLogger("orion.learner")
IDLE_SECONDS = 180
DAILY_CAP = 40
KINDS = {"profile", "person", "project", "preference", "fact", "plan"}

PROMPT = """You maintain the memory of a personal assistant.
From the NEW conversation lines below, extract facts about the user worth remembering: their name, people in their
life (with relationship), projects/work/studies, preferences and habits, important dates and plans, devices and
setup. Ignore one-off requests, small talk and anything already known. Never store passwords, keys or
financial/medical identifiers.

For each fact choose "importance":
- "permanent": lasting for months (identity, people, ongoing projects, stable preferences, birthdays, key dates).
- "short-term": passing details that may matter in the next days (this week's plans, a temporary situation).

Already known:
{known}

New conversation lines:
{lines}

Return JSON: {{"facts": [{{"text": "<self-contained statement about the user>",
"kind": "profile|person|project|preference|plan|fact", "importance": "permanent|short-term",
"subject": "<person or project name, for person/project facts>"}}]}}
Return at most 5 facts; return {{"facts": []}} if nothing new is worth keeping."""


class Learner:
    def __init__(self, engine: "Engine") -> None:
        self.e = engine
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="learner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                self.e.vault.sync()
                if self.e.config.settings.auto_memory and self.e.config.get_api_key():
                    await self.scan()
                if self.e.config.get_api_key() and self.e.vault.review_due():
                    await self.e.vault.review()
            except Exception:
                log.exception("Memory learning/review failed")
            await asyncio.sleep(60)

    def _budget_ok(self) -> bool:
        from ..brain.governor import quota_day

        day = quota_day()
        used = self.e.store.kv_get("learner_usage", {})
        return used.get(day, 0) < DAILY_CAP

    def _spend(self) -> None:
        from ..brain.governor import quota_day

        day = quota_day()
        self.e.store.kv_set("learner_usage", {day: self.e.store.kv_get("learner_usage", {}).get(day, 0) + 1})

    async def scan(self, now: float | None = None) -> int:
        now = now or time.time()
        marks: dict[str, float] = self.e.store.kv_get("learner_marks", {})
        learned = 0
        for conv in self.e.store.list_conversations(30):
            last = marks.get(conv["id"], 0)
            if conv["updated"] <= last or now - conv["updated"] < IDLE_SECONDS:
                continue
            msgs = [m for m in self.e.store.messages(conv["id"], 60) if m["ts"] > last]
            user_lines = [m for m in msgs if m["role"] == "user"]
            marks[conv["id"]] = conv["updated"]
            if len(user_lines) < 1 or sum(len(m["content"]) for m in user_lines) < 40:
                continue
            if not self._budget_ok():
                break
            learned += await self.learn_from(msgs)
        self.e.store.kv_set("learner_marks", marks)
        return learned

    async def learn_from(self, msgs: list[dict]) -> int:
        known = [m["text"] for m in self.e.store.list_memories()[:60]]
        lines = "\n".join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:500]}" for m in msgs[-24:])
        self._spend()
        raw = await self.e.brain.generate_text(
            PROMPT.format(known="\n".join(f"- {k}" for k in known) or "(nothing yet)", lines=lines),
            tier="fast", json_mode=True, patient=True)
        try:
            data = json.loads(raw)
        except ValueError:
            return 0
        facts = data if isinstance(data, list) else data.get("facts", []) if isinstance(data, dict) else []
        facts = [f for f in facts if isinstance(f, dict)]
        added = 0
        for f in facts[:5]:
            text = str(f.get("text", "")).strip()
            kind = f.get("kind") if f.get("kind") in KINDS else "fact"
            if len(text) < 8 or is_duplicate(text, known):
                continue
            tier = "short" if str(f.get("importance", "")).startswith("short") else "permanent"
            try:
                self.e.vault.add(text, kind, tier, str(f.get("subject") or ""))
            except (OSError, ValueError) as exc:
                log.warning("Couldn't save a learned fact: %s", exc)
                continue
            known.append(text)
            added += 1
        if added:
            self.e.bus.emit("memory.changed")
            log.info("Learned %d new fact(s)", added)
        return added
