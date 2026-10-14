"""The ORION engine: wires config, storage, brain, tools, ecosystem, scheduler and voice together."""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from pathlib import Path
from typing import Callable

from . import __version__
from .brain.agent import Agent
from .brain.gemini import GeminiBrain
from .brain.governor import Governor
from .config import Config
from .ecosystem.connectors import ConnectorManager
from .ecosystem.plugins import PluginManager
from .events import EventBus
from .memory.store import Store
from .skills.loader import SkillLibrary
from .tasks.scheduler import Scheduler
from .tasks.tracker import ApprovalBroker, TaskTracker
from .tools import files as files_tools
from .tools.registry import Registry, decorated_tools

log = logging.getLogger("orion.engine")

CORE_TOOL_MODULES = ["system", "apps", "files", "web", "screen", "productivity", "shell"]


class Engine:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.bus = EventBus()
        self.store = Store(self.config.root / "orion.db")
        self.governor = Governor(self.config, self.store)
        self.brain = GeminiBrain(self.config, self.governor, self.bus)
        self.registry = Registry()
        self.tracker = TaskTracker(self.bus, self.store)
        self.approvals = ApprovalBroker(self.bus)
        self.agent = Agent(self)
        self.skills = SkillLibrary(Path(__file__).resolve().parent / "builtin_skills", self.config.root / "skills")
        self.plugins = PluginManager(self.config.root / "plugins", self.registry)
        self.connectors = ConnectorManager(self.config.root / "connectors.json", self.registry,
                                           lambda: self.bus.emit("ecosystem.changed"))
        from .ecosystem.accounts import Accounts
        from .server.remote import RemoteServer

        self.accounts = Accounts(self.config.root, self.registry, lambda: self.bus.emit("ecosystem.changed"))
        self.remote = RemoteServer(self)
        self.scheduler: Scheduler | None = None
        self.voice = None
        self.notifier: Callable[[str, str], None] | None = None  # set by the desktop shell (tray toasts)
        self.started = time.time()
        self._bg: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Startup / shutdown ─────────────────────────────────────────────────
    def load_tools(self) -> None:
        for mod in CORE_TOOL_MODULES:
            importlib.import_module(f"orion.tools.{mod}")
        for t in decorated_tools():
            if getattr(t.fn, "__module__", "").startswith("orion.tools."):
                self.registry.add(t)
        disabled = self.store.kv_get("disabled_tools", [])
        for name in disabled:
            self.registry.set_enabled(name, False)
        self.skills.disabled = set(self.store.kv_get("disabled_skills", []))
        files_tools.set_allowed_roots(self.config.settings.allowed_roots)
        self.accounts.sync_tools()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.bus.bind(self._loop)
        self.load_tools()
        self.skills.load()
        self.plugins.load_all()
        from .tools.apps import INDEX

        INDEX.build_async()
        self.scheduler = Scheduler(self.store, self.notify, self._run_routine, lambda: self.bus.emit("routines.changed"))
        self.scheduler.start()
        self._bg.append(asyncio.create_task(self.connectors.start_all(), name="connectors"))
        self._bg.append(asyncio.create_task(self._vitals_loop(), name="vitals"))
        if self.config.get_api_key():
            self._bg.append(asyncio.create_task(self._discover(), name="discover"))
        await self.start_voice()
        if self.config.settings.remote_enabled:
            try:
                await self.remote.start()
            except OSError as exc:
                log.warning("Phone remote couldn't start: %s", exc)
        log.info("ORION %s engine started (%d tools, %d skills)", __version__, len(self.registry.all()),
                 len(self.skills.skills))

    async def start_voice(self) -> None:
        try:
            from .voice.manager import VoiceManager

            self.voice = VoiceManager(self)
            await self.voice.start()
        except Exception as exc:  # e.g. PortAudio missing — the app still works by typing
            log.warning("Voice disabled: %s", exc)
            self.voice = None

    async def _discover(self) -> None:
        try:
            info = await self.brain.discover()
            self.store.kv_set("models", info["models"])
            self.bus.emit("brain.models", **info)
        except Exception as exc:
            log.info("Model discovery failed: %s", exc)

    async def stop(self) -> None:
        self.tracker.stop_all()
        self.approvals.deny_all()
        if self.voice:
            await self.voice.stop()
        if self.scheduler:
            await self.scheduler.stop()
        await self.connectors.stop_all()
        await self.remote.stop()
        for t in self._bg:
            t.cancel()
        self.store.close()

    async def _vitals_loop(self) -> None:
        from .tools.system import vitals

        import psutil

        psutil.cpu_percent(interval=None)
        while True:
            await asyncio.sleep(2.5)
            try:
                self.bus.emit("vitals", vitals=await asyncio.to_thread(vitals))
            except Exception:
                pass

    # ── Actions used by the API / tools ────────────────────────────────────
    def notify(self, title: str, message: str) -> None:
        self.bus.emit("notify", title=title, message=message)
        if self.notifier:
            try:
                self.notifier(title, message)
            except Exception:
                pass
        if self.voice and self.voice.available and self.config.settings.voice.speak_replies and title.startswith(("⏱", "🔔")):
            coro = self.voice.speak(message)
            try:
                asyncio.get_running_loop()
                asyncio.ensure_future(coro)
            except RuntimeError:  # called from a worker thread
                if self._loop:
                    asyncio.run_coroutine_threadsafe(coro, self._loop)
                else:
                    coro.close()

    async def after_reply(self, reply: str, source: str) -> None:
        if source == "voice" and self.voice and self.config.settings.voice.speak_replies:
            await self.voice.speak(reply)

    async def _run_routine(self, routine: dict) -> str:
        conv = self.store.create_conversation(f"⟳ {routine['name']}")
        self.bus.emit("conversation.created", conversation=conv)
        res = await self.agent.handle(routine["prompt"], conv["id"], source="routine", mode="auto")
        self.notify(f"Routine: {routine['name']}", (res.get("reply") or "")[:240])
        return res.get("reply", "")

    def stop_everything(self) -> int:
        n = self.tracker.stop_all()
        self.approvals.deny_all()
        if self.voice:
            self.voice.interrupt()
        self.bus.emit("stopped", count=n)
        return n

    def set_tool_enabled(self, name: str, enabled: bool) -> None:
        self.registry.set_enabled(name, enabled)
        disabled = set(self.store.kv_get("disabled_tools", []))
        (disabled.discard if enabled else disabled.add)(name)
        self.store.kv_set("disabled_tools", sorted(disabled))

    def set_skill_enabled(self, name: str, enabled: bool) -> None:
        self.skills.set_enabled(name, enabled)
        self.store.kv_set("disabled_skills", sorted(self.skills.disabled))

    def apply_settings(self, patch: dict) -> None:
        old_voice = self.config.settings.voice.model_dump()
        self.config.update(patch)
        files_tools.set_allowed_roots(self.config.settings.allowed_roots)
        if self.voice and self.config.settings.voice.model_dump() != old_voice:
            asyncio.ensure_future(self.voice.restart())
        self.bus.emit("settings.changed", settings=self.config.settings.model_dump())

    def state(self) -> dict:
        s = self.config.settings
        return {
            "version": __version__,
            "settings": s.model_dump(),
            "has_key": bool(self.config.get_api_key()),
            "voice": self.voice.status() if self.voice else {"state": "unavailable", "mode": s.voice.mode},
            "quota": self.governor.snapshot(),
            "tasks": [t.to_dict() for t in self.tracker.active.values()],
            "approvals": self.approvals.list(),
            "live_model": self.brain.live_model,
            "data_dir": str(self.config.root),
        }
