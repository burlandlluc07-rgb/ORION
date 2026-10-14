"""Application bootstrap: single instance, engine + API server thread, desktop window, tray, hotkeys."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx

from . import __version__
from .config import Config, data_dir

log = logging.getLogger("orion")


def setup_logging(root: Path, debug: bool) -> None:
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(logs / "orion.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None and getattr(sys.stderr, "name", "") != os.devnull:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "uvicorn.access", "google_genai", "websockets", "asyncio", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def instance_file() -> Path:
    return data_dir() / "instance.json"


def signal_existing(action: str = "show") -> bool:
    """If ORION is already running, ask it to show itself and return True."""
    f = instance_file()
    if not f.exists():
        return False
    try:
        info = json.loads(f.read_text())
        r = httpx.post(f"http://127.0.0.1:{info['port']}/api/window/{action}",
                       headers={"x-orion-token": info["token"]}, timeout=2)
        return r.status_code == 200
    except Exception:
        return False


class ServerThread(threading.Thread):
    """Runs the asyncio engine and the uvicorn server on their own loop."""

    def __init__(self, config: Config, port: int, token: str, shell) -> None:
        super().__init__(name="engine", daemon=True)
        self.config, self.port, self.token, self.shell = config, port, token, shell
        self.ready = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.engine = None
        self.error: BaseException | None = None
        self._server = None

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException as exc:  # surface start-up failures to the main thread
            self.error = exc
            log.exception("Engine crashed")
            self.ready.set()

    async def _main(self) -> None:
        import uvicorn

        from .engine import Engine
        from .server.api import create_app

        self.loop = asyncio.get_running_loop()
        self.engine = Engine(self.config)
        await self.engine.start()
        app = create_app(self.engine, self.token, self.shell)
        cfg = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", access_log=False, log_config=None,
                             ws_ping_interval=20, lifespan="off")
        self._server = uvicorn.Server(cfg)
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        serve = asyncio.create_task(self._server.serve())
        while not self._server.started:
            await asyncio.sleep(0.02)
            if serve.done():
                serve.result()
        self.ready.set()
        await serve
        await self.engine.stop()

    def call(self, coro_fn) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(coro_fn(), self.loop)

    def shutdown(self) -> None:
        if self._server:
            self._server.should_exit = True


def _ensure_std_streams() -> None:
    """A windowed .exe has no console: sys.stdout/stderr are None and libraries that
    write to them (or call .isatty()) would crash. Point them at the null device."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    _ensure_std_streams()
    ap = argparse.ArgumentParser(prog="orion", description="O.R.I.O.N. desktop assistant")
    ap.add_argument("--minimized", action="store_true", help="start in the tray")
    ap.add_argument("--browser", action="store_true", help="use the default browser instead of a native window")
    ap.add_argument("--headless", action="store_true", help="engine + API only (no window)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--version", action="version", version=f"ORION {__version__}")
    args = ap.parse_args(argv)

    config = Config()
    setup_logging(config.root, args.debug)
    if signal_existing("show"):
        log.info("ORION is already running; brought it to the front.")
        return 0

    import secrets

    from .desktop.shell import DesktopShell

    port = args.port or free_port()
    token = secrets.token_urlsafe(24)
    shell = DesktopShell(config)
    server = ServerThread(config, port, token, shell)
    server.start()
    if not server.ready.wait(60) or server.error:
        log.error("Engine failed to start: %s", server.error)
        return 1
    instance_file().write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}))
    url = f"http://127.0.0.1:{port}/?token={token}"
    log.info("ORION %s ready at http://127.0.0.1:%d (data: %s)", __version__, port, config.root)

    shell.attach(server, url)
    try:
        if args.headless:
            print(f"ORION running headless. Open: {url}")
            while server.is_alive():
                time.sleep(0.5)
        elif args.browser or not shell.has_webview():
            webbrowser.open(url)
            shell.run_tray_blocking()
        else:
            shell.run(start_hidden=args.minimized or config.settings.start_minimized)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.join(timeout=8)
        try:
            instance_file().unlink(missing_ok=True)
        except OSError:
            pass
    return 0

