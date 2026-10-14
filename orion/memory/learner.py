"""Deep memory: ORION learns durable facts from conversations by itself.

When a conversation goes quiet, the fast model reads what's new and extracts
lasting facts (people, projects, preferences, plans). Near-duplicates are
skipped, a daily cap protects the free quota, and everything learned shows up
in Memory where it can be deleted.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.learner")
IDLE_SECONDS = 180
DAILY_CAP = 40
KINDS = {"profile", "person", "project", "preference", "fact", "plan"}

PROMPT = """You maintain the long-term memory of a personal assistant.
From the NEW conversation lines below, extract facts about the user worth remembering for months:
their name, people in their life (with relationship), projects/work/studies, preferences and habits,
important dates and plans, devices and setup. Ignore one-off requests, small talk, anything temporary,
and anything already known. Never store passwords, keys or financial/medical identifiers.

Already known:
{known}

New conversation lines:
{lines}

Return JSON: {{"facts": [{{"text": "<self-contained statement about the user>", "kind": "profile|person|project|preference|plan|fact"}}]}}
Return at most 5 facts; return {{"facts": []}} if nothing new and durable."""


def is_duplicate(text: str, known: list[str]) -> bool:
    t = text.lower().strip(" .")
    return any(difflib.SequenceMatcher(None, t, k.lower().strip(" .")).ratio() > 0.82 for k in known)


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
                if self.e.config.settings.auto_memory and self.e.config.get_api_key():
                    await self.scan()
            except Exception:
                log.exception("Memory learning failed")
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
            facts = json.loads(raw).get("facts", [])
        except (ValueError, AttributeError):
            return 0
        added = 0
        for f in facts[:5]:
            text = str(f.get("text", "")).strip()
            kind = f.get("kind") if f.get("kind") in KINDS else "fact"
            if len(text) < 8 or is_duplicate(text, known):
                continue
            self.e.store.remember(text, kind)
            known.append(text)
            added += 1
        if added:
            self.e.bus.emit("memory.changed")
            log.info("Learned %d new fact(s)", added)
        return added
