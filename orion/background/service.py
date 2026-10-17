"""ORION's side of the background agent: runs one :class:`BackgroundAgent` inside the engine.

What it connects:

* **LLM and quota.** Tasks run on ORION's Gemini key, and every request goes through ORION's free-tier
  :class:`~orion.brain.governor.Governor`, so the agent and the assistant share one budget and the agent
  waits for a slot (or fails with the governor's message) instead of spending quota ORION is counting on.
* **Approvals.** The agent's SQLite store stays the record: a task parked for approval survives restarts.
  Each pending approval is also shown through ORION's :class:`~orion.tasks.tracker.ApprovalBroker`
  (screen, paired phones), and the answer there is written back to the store. A prompt that expires leaves
  the approval pending; it is asked again on the next start and can be answered through the API meanwhile.
* **Notifications and events.** Results go through :meth:`Engine.notify`; every task change is published on
  the event bus as ``background.task``.
* **Start-up.** Nothing of its own: the agent starts and stops with the engine, so ORION's existing
  "start with Windows" setting covers it. The standalone daemon's tray, hotkey, HTTP server, Task
  Scheduler task and supervisor are not used.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from . import config as bg_config
from .engine import BackgroundAgent
from .gemini_agent import RETRY_CODES, GeminiAgentRunner
from .runners import RunContext
from .store import NEEDS_APPROVAL, Store

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.background")

CONFIG_FILE = "background-agent.toml"
MAX_SLOT_WAIT = 180.0  # background work waits out per-minute limits, like missions do
MAX_ATTEMPTS = 6
DANGER_TOOLS = {"delete_file", "run_shell"}

# The in-process agent has no tray, hotkey or HTTP server of its own, and approvals are announced by
# ORION's broker (which also pushes them to paired phones), so the agent's own approval toast is off.
OVERRIDES = {
    "notify": {"toast": False, "on_approval": False},
    "hotkey": {"enabled": False},
    "tray": {"enabled": False},
    "scheduler": {"enabled": False},
    "agent": {"provider": "gemini"},
}


def load_config(engine: Engine) -> bg_config.Config:
    """Defaults, then ``background-agent.toml`` in ORION's data folder, then the in-process overrides."""
    cfg = bg_config.load(engine.config.root / CONFIG_FILE, overrides=OVERRIDES)
    cfg["gemini"]["model"] = engine.config.settings.model_smart
    cfg["gemini"]["fallback_models"] = [engine.config.settings.model_fast]
    return cfg


class OrionGeminiRunner(GeminiAgentRunner):
    """The agent's Gemini loop, with ORION's key and every request paid for through ORION's governor."""

    def __init__(self, cfg: dict, engine: Engine, loop: asyncio.AbstractEventLoop, client: Any = None):
        super().__init__(cfg, client=client)
        self.engine = engine
        self.loop = loop
        self._client_key: str | None = None

    def _get_client(self) -> Any:
        key = self.engine.config.get_api_key()
        if not key:
            raise RuntimeError("Add your Gemini API key in ORION's settings to run background tasks")
        if self._client is None or (self._client_key is not None and key != self._client_key):
            from google import genai

            self._client = genai.Client(api_key=key)
            self._client_key = key
        return self._client

    def _on_loop(self, coro: Coroutine, ctx: RunContext | None = None) -> Any:
        """Run a coroutine on ORION's event loop from this worker thread, still honouring cancel and timeout."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        while True:
            try:
                return fut.result(timeout=0.25)
            except concurrent.futures.TimeoutError:
                if ctx is not None:
                    try:
                        ctx.check()
                    except BaseException:
                        fut.cancel()
                        raise

    def _call(self, fn, *args) -> Any:
        async def run():
            return fn(*args)

        return self._on_loop(run())

    def _generate(self, contents: Any, config: Any, ctx: RunContext) -> Any:
        from google.genai import errors

        from ..brain.governor import QuotaExhausted

        gov = self.engine.governor
        client = self._get_client()
        tried: set[str] = set()
        for attempt in range(MAX_ATTEMPTS):
            ctx.check()
            wait = max(1.0, min(MAX_SLOT_WAIT, ctx.remaining()))
            try:
                model = self._on_loop(gov.acquire("smart", exclude=tried, max_wait=wait), ctx)
            except QuotaExhausted as exc:
                raise RuntimeError(str(exc)) from exc
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as exc:
                self._call(gov.record, model)
                if exc.code == 429:
                    delay = self._call(gov.cool_down, model, str(exc))
                    log.warning("Background agent rate limited on %s for %.0fs", model, delay)
                if exc.code not in RETRY_CODES and exc.code != 404:
                    raise
                if attempt == 0 and exc.code in (500, 502, 503, 504):
                    ctx.sleep(1.0)  # transient overload: one quick retry on the same model first
                else:
                    tried.add(model)
                continue
            usage = getattr(response, "usage_metadata", None)
            self._call(gov.record, model, int(getattr(usage, "total_token_count", 0) or 0))
            self.last_model = model
            return response
        raise RuntimeError("Gemini kept failing; the background task stopped. Try again later.")


class BackgroundService:
    def __init__(self, engine: Engine) -> None:
        self.e = engine
        self.agent: BackgroundAgent | None = None
        self._asked: dict[str, str] = {}  # agent approval id -> id of ORION's prompt for it
        self._stopping = False

    @property
    def running(self) -> bool:
        return self.agent is not None

    def start(self, loop: asyncio.AbstractEventLoop, client: Any = None) -> None:
        if self.agent is not None:
            return
        self._stopping = False
        cfg = load_config(self.e)
        data = self.e.config.root / "background"
        data.mkdir(parents=True, exist_ok=True)
        self.agent = BackgroundAgent(cfg, store=Store(data / "tasks.db"), notifier=_Notifier(self.e, loop),
                                     runners={"agent": OrionGeminiRunner(cfg, self.e, loop, client=client)})
        self.agent.subscribe(self._on_event)
        self.agent.start()
        for a in self.pending_approvals():  # left waiting by the last session
            self._ask(a)

    def stop(self) -> None:
        """Running tasks stop at their next step and resume on the next start; approvals stay pending."""
        if self.agent is None:
            return
        self._stopping = True
        for aid in list(self._asked.values()):
            self._close_prompt(aid, "deny")  # only closes the prompt: decisions are ignored while stopping
        agent, self.agent = self.agent, None
        threads = list(agent._threads)
        agent.stop(timeout=5)
        if not any(t.is_alive() for t in threads):  # a worker still inside a model call closes nothing under it
            agent.store.close()

    # ── Public API (used by the HTTP API and the chat tools) ──────────────
    def _need(self) -> BackgroundAgent:
        if self.agent is None:
            raise RuntimeError("The background agent is off (turn it on in settings)")
        return self.agent

    def submit(self, prompt: str, source: str = "orion", kind: str = "agent", timeout: float | None = None) -> dict:
        return view(self._need().submit(prompt, kind=kind, source=source, timeout=timeout))

    def get(self, task_id: str) -> dict | None:
        task = self._need().get(task_id)
        if task is None:
            return None
        return {**view(task), "approvals": self._need().store.approvals(None, task_id=task_id)}

    def list(self, limit: int = 50) -> list[dict]:
        return [view(t) for t in self._need().list(limit=limit)]

    def cancel(self, task_id: str) -> dict | None:
        task = self._need().cancel(task_id)
        return view(task) if task else None

    def pending_approvals(self) -> list[dict]:
        agent = self._need()
        out = []
        for a in agent.pending_approvals():
            task = agent.get(a["task_id"])
            if task and task["status"] == NEEDS_APPROVAL:  # a cancelled task's approvals are moot
                out.append(a)
        return out

    def decide(self, approval_id: str, approved: bool) -> bool:
        agent = self._need()
        try:
            (agent.approve if approved else agent.deny)(approval_id)
        except KeyError:
            return False
        aid = self._asked.pop(approval_id, None)
        if aid:
            self._close_prompt(aid, "approve" if approved else "deny")  # close ORION's prompt too
        return True

    def pause(self) -> None:
        self._need().pause()

    def resume(self) -> None:
        self._need().resume()

    def status(self) -> dict:
        if self.agent is None:
            return {"enabled": False}
        return {"enabled": True, **self.agent.status()}

    # ── Bridges ───────────────────────────────────────────────────────────
    def _on_event(self, event: str, task: dict) -> None:
        """Called from the agent's worker threads."""
        self.e.bus.emit("background.task", event=event, task=view(task))
        if event == "needs_approval":
            for a in self.agent.pending_approvals(task["id"]) if self.agent else []:
                self._ask(a)
        elif event == "cancelled":
            for a in self.agent.store.approvals(None, task_id=task["id"]) if self.agent else []:
                aid = self._asked.pop(a["id"], None)
                if aid:
                    self._close_prompt(aid, "deny")

    def _close_prompt(self, aid: str, decision: str) -> None:
        """Answer ORION's prompt from any thread (asyncio futures may only be resolved on their loop)."""
        loop = self.e._loop
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if on_loop or loop is None:
            self.e.approvals.respond(aid, decision)
        else:
            try:
                loop.call_soon_threadsafe(self.e.approvals.respond, aid, decision)
            except RuntimeError:
                pass

    def _ask(self, approval: dict) -> None:
        if approval["id"] in self._asked or self.e._loop is None:
            return
        self._asked[approval["id"]] = f"bg-{approval['id']}"
        asyncio.run_coroutine_threadsafe(self._prompt(approval), self.e._loop)

    async def _prompt(self, approval: dict) -> None:
        tool = approval["tool"]
        task = self.agent.get(approval["task_id"]) if self.agent else None
        what = _short(task["prompt"]) if task else "a background task"
        label = f"Background task '{what}': {approval.get('reason') or tool}"
        risk = "danger" if tool in DANGER_TOOLS else "confirm"
        decision = await self.e.approvals.ask(f"background:{tool}", label, risk, approval.get("input") or {}, None,
                                              approval_id=self._asked.get(approval["id"]))
        if self._asked.pop(approval["id"], None) is None or self._stopping or self.agent is None:
            return  # already decided elsewhere, cancelled, or ORION is shutting down
        if decision in ("approve", "always", "deny"):
            try:
                (self.agent.approve if decision != "deny" else self.agent.deny)(approval["id"])
            except KeyError:
                pass  # decided through the API in the meantime
        # "expired": left pending in the store; it is asked again on the next start


class _Notifier:
    """The agent's notifier, routed through ORION (tray toast, event bus, paired phones)."""

    def __init__(self, engine: Engine, loop: asyncio.AbstractEventLoop) -> None:
        self.e = engine
        self.loop = loop
        self.sent: list[tuple[str, str]] = []

    def __call__(self, title: str, message: str) -> None:
        self.send(title, message)

    def send(self, title: str, message: str) -> None:
        title = title.replace("Orion ", "ORION ", 1)
        self.sent.append((title, message))
        del self.sent[:-50]
        try:
            self.loop.call_soon_threadsafe(self.e.notify, title, message)
        except RuntimeError:  # ORION's loop is already closed (shutting down)
            pass


def view(task: dict) -> dict:
    """A task without its saved conversation (which can be large)."""
    return {k: v for k, v in task.items() if k != "state"}


def _short(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."
