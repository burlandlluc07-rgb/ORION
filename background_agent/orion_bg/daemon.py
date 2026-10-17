"""The headless daemon: one process, no window. It owns the task queue,
worker pool, scheduler, localhost API, hotkey and tray icon. UIs are clients.

Exit codes: 0 = clean stop (the supervisor stops too), anything else = crash
(the supervisor restarts it).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import sys
import threading
from pathlib import Path

from . import config, log as logsetup, paths
from .client import Client, DaemonNotRunning
from .engine import BackgroundAgent

log = logging.getLogger("orion_bg.daemon")


def _already_running() -> bool:
    try:
        return Client.from_runtime(timeout=2).ping()
    except DaemonNotRunning:
        return False


def _write_runtime(host: str, port: int, token: str) -> Path:
    path = paths.runtime_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"host": host, "port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)  # %APPDATA% is already per-user on Windows
    os.replace(tmp, path)
    return path


def _remove_runtime() -> None:
    path = paths.runtime_file()
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if info.get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def main(config_path: Path | None = None, console: bool = False, tray: bool | None = None,
         stop_event: threading.Event | None = None, ready: threading.Event | None = None) -> int:
    cfg = config.load(config_path)
    logsetup.setup(cfg, console=console)
    if _already_running():
        log.info("another daemon is already running; exiting")
        return 0

    stop = stop_event or threading.Event()
    agent = BackgroundAgent(cfg)

    if cfg["scheduler"]["enabled"]:
        try:
            from .scheduler import Scheduler

            Scheduler(agent)
        except ImportError:
            log.warning("APScheduler/SQLAlchemy not installed; recurring tasks are off")

    from .server import ApiServer

    token = secrets.token_urlsafe(32)
    server = ApiServer(agent, cfg["daemon"]["host"], int(cfg["daemon"]["port"]), token, on_shutdown=stop.set)
    ui_url = f"{server.url}/?token={token}"
    agent.notifier.ui_url = ui_url

    hotkey = None
    try:
        agent.start()
        server.start()
        _write_runtime(server.host, server.port, token)

        if cfg["hotkey"]["enabled"]:
            from .hotkey import Hotkey, read_clipboard_text

            def on_hotkey() -> None:
                text = read_clipboard_text().strip()
                if not text:
                    agent.notifier.send("Orion", "Copy some text first, then press the hotkey to queue it.")
                    return
                task = agent.submit(text, source="hotkey")
                agent.notifier.send("Orion queued a task", task["prompt"][:120])

            try:
                hotkey = Hotkey(cfg["hotkey"]["combo"], on_hotkey)
                hotkey.start()
            except ValueError as exc:
                log.warning("hotkey disabled: %s", exc)
                hotkey = None

        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())

        use_tray = cfg["tray"]["enabled"] if tray is None else tray
        from . import tray as traymod

        if use_tray and traymod.available() and threading.current_thread() is threading.main_thread():
            traymod.Tray(agent, ui_url, stop, on_quit=stop.set).run()
        else:
            if use_tray:
                log.info("tray icon unavailable (pystray/Pillow missing); running without it")
            if ready:
                ready.set()
            while not stop.wait(1.0):
                pass
        return 0
    except Exception:
        log.exception("daemon crashed")
        return 1
    finally:
        log.info("daemon stopping")
        if hotkey:
            hotkey.stop()
        try:
            server.stop()
        except Exception:
            pass
        agent.stop()
        agent.store.close()
        _remove_runtime()
