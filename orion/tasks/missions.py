"""Missions: long-running autonomous jobs that ORION works on in the background.

"Research the best budget gaming laptops and put a comparison on my desktop."
A mission gets its own conversation, the deep model, a bigger step budget and
instructions to work end-to-end without asking questions. Progress shows up
live (plan + steps); risky steps still need approval (pushed to the phone);
when it's done ORION notifies the PC and the phone.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..memory.store import new_id

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.missions")
MAX_PARALLEL = 2
MISSION_ROUNDS = 40

MISSION_PROMPT = """MISSION MODE — you are running an autonomous mission in the background; the user is NOT watching.
- Work end-to-end without asking questions. Make sensible assumptions and state them in the final summary.
- Start with update_plan (3-8 steps) and keep it updated as you go.
- Be thorough: use web_search with several angles, read_webpage for sources, files and other tools as needed.
- For research or comparison missions, save a well-structured Markdown report with write_file to
  "documents/ORION Missions/<short title>.md" (include sources), then mention the path.
- For action missions (organise, clean up, set up…), do the work and verify it.
- Finish with a short summary: what you did, key results (3-6 bullets), anything that needs the user."""


class Missions:
    def __init__(self, engine: "Engine") -> None:
        self.e = engine
        self._running: dict[str, asyncio.Task] = {}
        self._queue: list[str] = []

    def _all(self) -> list[dict]:
        return self.e.store.kv_get("missions", [])

    def _save(self, items: list[dict]) -> None:
        self.e.store.kv_set("missions", items[-100:])
        self.e.bus.emit("missions.changed")

    def _update(self, mid: str, **patch) -> dict | None:
        items = self._all()
        for m in items:
            if m["id"] == mid:
                m.update(patch)
                self._save(items)
                return m
        return None

    def list(self) -> list[dict]:
        return list(reversed(self._all()))

    def start(self, goal: str, title: str | None = None) -> dict:
        goal = goal.strip()
        conv = self.e.store.create_conversation(f"🎯 {(title or goal)[:60]}")
        self.e.bus.emit("conversation.created", conversation=conv)
        m = {"id": new_id(), "goal": goal, "title": (title or goal)[:80], "status": "queued", "conv_id": conv["id"],
             "task_id": None, "created": time.time(), "started": None, "finished": None, "summary": None}
        items = self._all()
        items.append(m)
        self._save(items)
        self._queue.append(m["id"])
        self._pump()
        return m

    def _pump(self) -> None:
        while self._queue and len(self._running) < MAX_PARALLEL:
            mid = self._queue.pop(0)
            self._running[mid] = asyncio.create_task(self._run(mid), name=f"mission-{mid}")

    async def _run(self, mid: str) -> None:
        m = self._update(mid, status="running", started=time.time())
        if m is None:
            return
        try:
            res = await self.e.agent.handle(
                m["goal"], m["conv_id"], source="mission", mode="smart", extra_system=MISSION_PROMPT,
                max_rounds=MISSION_ROUNDS, on_task=lambda t: self._update(mid, task_id=t.id))
            reply = (res.get("reply") or "").strip()
            attempts = m.get("attempts", 0)
            transient = any(s in reply for s in ("free-tier limit", "free daily quota", "temporarily unavailable",
                                                 "incomplete answer", "is busy right now"))
            if reply.startswith("⚠") and transient and attempts < 3:
                # Rate-limited or overloaded: try again in a few minutes rather than failing.
                self._update(mid, status="queued", attempts=attempts + 1,
                             summary="Gemini is busy or out of free quota for the moment; will retry automatically.")
                await asyncio.sleep(120 * (attempts + 1))
                self._queue.append(mid)
                return
            failed = reply.startswith("⚠") or not res.get("ok", True)
            stopped = "*(stopped)*" in reply
            status = "stopped" if stopped else ("failed" if failed else "done")
            self._update(mid, status=status, finished=time.time(), summary=reply[:1500])
            first = next((ln.strip("-• *") for ln in reply.splitlines() if ln.strip()), "")[:200]
            self.e.notify(f"Mission {'complete' if status == 'done' else status}: {m['title'][:50]}", first or status)
        except asyncio.CancelledError:
            self._update(mid, status="stopped", finished=time.time())
            raise
        except Exception as exc:
            log.exception("Mission failed")
            self._update(mid, status="failed", finished=time.time(), summary=str(exc))
            self.e.notify(f"Mission failed: {m['title'][:50]}", str(exc)[:200])
        finally:
            self._running.pop(mid, None)
            self._pump()

    def cancel(self, mid: str) -> bool:
        if mid in self._queue:
            self._queue.remove(mid)
            self._update(mid, status="stopped", finished=time.time())
            return True
        m = next((x for x in self._all() if x["id"] == mid), None)
        if m and m.get("task_id") and m["task_id"] in self.e.tracker.active:
            t = self.e.tracker.active[m["task_id"]]
            t.cancel.set()
            return True
        task = self._running.get(mid)
        if task:
            task.cancel()
            return True
        return False

    def recover(self) -> None:
        """Missions interrupted by a restart are marked so they don't look stuck."""
        items = self._all()
        changed = False
        for m in items:
            if m["status"] in ("running", "queued"):
                m["status"], m["summary"] = "stopped", "ORION was closed while this mission was running."
                changed = True
        if changed:
            self._save(items)
