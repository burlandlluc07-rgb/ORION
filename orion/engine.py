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

CORE_TOOL_MODULES = ["system", "apps", "files", "web", "screen", "productivity", "shell", "office", "media", "browser", "windows", "context"]


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
        from .ecosystem.home import SmartHome

        self.home = SmartHome(self.accounts, self.registry)
        from .ecosystem.whatsapp import WhatsApp

        self.whatsapp = WhatsApp(self.config.root, self.registry, lambda: self.bus.emit("ecosystem.changed"))
        self.remote = RemoteServer(self)
        self.approvals.on_request = self._push_approval
        from .proactive import Proactive

        self.proactive = Proactive(self)
        from .tasks.missions import Missions

        self.missions = Missions(self)
        from .memory.learner import Learner

        self.learner = Learner(self)
        from .memory.vault import Vault

        self.vault = Vault(self)
        from .background.service import BackgroundService

        self.background = BackgroundService(self)
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
        files_tools.set_data_dir(self.config.root)
        self._apply_browser_mode()
        self.accounts.sync_tools()
        self.home.sync_tools()
        self.whatsapp.sync_tools()
        from .ecosystem.integrations import status_tool

        self.registry.add(status_tool(self))

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.bus.bind(self._loop)
        self.load_tools()
        self.skills.load()
        self.plugins.load_all()
        from .tools.apps import INDEX
        from .tools.context import FOREGROUND

        INDEX.build_async()
        FOREGROUND.start()  # remembers the window you were last in, for "this"/"the selected text"
        self.scheduler = Scheduler(self.store, self.notify, self._run_routine, lambda: self.bus.emit("routines.changed"))
        self.scheduler.start()
        self._bg.append(asyncio.create_task(self.connectors.start_all(), name="connectors"))
        self._bg.append(asyncio.create_task(self._vitals_loop(), name="vitals"))
        if self.config.get_api_key():
            self._bg.append(asyncio.create_task(self._discover(), name="discover"))
        self._bg.append(asyncio.create_task(self._update_loop(), name="updates"))
        self.proactive.start()
        self.missions.recover()
        if self.config.settings.background_agent:
            self.start_background()
        try:
            self.vault.ensure()
            self.vault.migrate()
            self.vault.sync(force=True)
        except OSError as exc:
            log.warning("Memory vault unavailable at %s: %s", self.vault.root, exc)
        self.learner.start()
        await self.start_voice()
        if self.config.settings.remote_enabled:
            try:
                await self.remote.start()
            except OSError as exc:
                log.warning("Phone remote couldn't start: %s", exc)
        log.info("ORION %s engine started (%d tools, %d skills)", __version__, len(self.registry.all()),
                 len(self.skills.skills))

    def start_background(self) -> None:
        try:
            self.background.start(self._loop)
        except Exception as exc:  # a broken task database must not stop ORION itself
            log.warning("Background agent couldn't start: %s", exc)

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

    async def check_update(self) -> dict | None:
        """Compare against the newest GitHub release; returns {"version", "url", "notes"} if newer."""
        import httpx

        from . import __version__

        repo = self.config.settings.update_repo.strip()
        if not repo or "/" not in repo:
            return None
        try:
            async with httpx.AsyncClient(timeout=10, headers={"Accept": "application/vnd.github+json"}) as c:
                r = await c.get(f"https://api.github.com/repos/{repo}/releases/latest")
            if r.status_code != 200:
                return None  # no releases yet, private repo or offline
            rel = r.json()
        except Exception:
            return None
        latest = str(rel.get("tag_name", "")).lstrip("vV")
        if not latest or _version_tuple(latest) <= _version_tuple(__version__):
            return None
        asset = next((a["browser_download_url"] for a in rel.get("assets", []) if a["name"].endswith(".exe")), None)
        info = {"version": latest, "url": asset or rel.get("html_url"), "notes": (rel.get("body") or "")[:1500],
                "page": rel.get("html_url")}
        self.store.kv_set("update", info)
        self.bus.emit("update.available", update=info)
        return info

    async def _update_loop(self) -> None:
        await asyncio.sleep(20)  # let start-up finish first
        while True:
            if self.config.settings.check_updates:
                await self.check_update()
            await asyncio.sleep(24 * 3600)

    async def stop(self) -> None:
        await asyncio.to_thread(self.background.stop)  # before deny_all, so its approvals stay pending
        self.tracker.stop_all()
        self.approvals.deny_all()
        if self.voice:
            await self.voice.stop()
        if self.scheduler:
            await self.scheduler.stop()
        await self.connectors.stop_all()
        await self.remote.stop()
        await self.proactive.stop()
        await self.learner.stop()
        from .tools.browser import BROWSER

        if BROWSER._ctx is not None:
            try:
                await asyncio.wait_for(asyncio.to_thread(BROWSER.call, BROWSER.close, 10), 12)
            except Exception:
                pass
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
    def _push(self, title: str, body: str, data: dict | None = None, tag: str | None = None) -> None:
        """Send a push notification to paired phones without blocking the engine."""
        if not any(d.get("push") for d in self.remote.devices.list()):
            return
        loop = self._loop
        job = lambda: self.remote.push.send(title, body, data, tag)  # noqa: E731
        if loop and loop.is_running():
            loop.run_in_executor(None, job)

    def _push_approval(self, info: dict) -> None:
        self._push("ORION needs your OK", info["label"], {"approval_id": info["id"], "risk": info["risk"]},
                   f"approval-{info['id']}")

    def notify(self, title: str, message: str, push: bool = True) -> None:
        self.bus.emit("notify", title=title, message=message)
        if push:
            self._push(title, message, {}, None)
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
        self._apply_browser_mode()
        if self.config.settings.background_agent and not self.background.running and self._loop:
            self.start_background()
        elif not self.config.settings.background_agent and self.background.running and self._loop:
            self._loop.run_in_executor(None, self.background.stop)
        if self.voice and self.config.settings.voice.model_dump() != old_voice:
            asyncio.ensure_future(self.voice.restart())
        self.bus.emit("settings.changed", settings=self.config.settings.model_dump())

    def _apply_browser_mode(self) -> None:
        from .tools.browser import BROWSER

        # Only the preference: a running browser switches the next time it starts (or via browser_show/hide).
        BROWSER.want_visible = self.config.settings.browser_mode == "visible"

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
            "background": self.background.status(),
            "live_model": self.brain.live_model,
            "data_dir": str(self.config.root),
            "update": self.store.kv_get("update"),
        }


def _version_tuple(v: str) -> tuple[int, ...]:
    import re

    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)
