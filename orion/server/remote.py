"""Phone remote: a second, restricted API server on the local network.

Scan the QR code in Settings → Phone remote and your phone gets a mobile
ORION: chat, quick actions, live task progress and approvals. It uses its
own secret token (rotatable) and cannot change settings, connectors,
plugins or skills. It's plain HTTP on your LAN, so use it on trusted Wi-Fi.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.remote")


def lan_ip() -> str:
    """Best guess at this PC's address on the local network (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class RemoteServer:
    def __init__(self, engine: "Engine") -> None:
        self.e = engine
        self._server = None
        self._task: asyncio.Task | None = None
        self.error: str | None = None

    @property
    def token(self) -> str:
        tok = self.e.store.kv_get("remote_token")
        if not tok:
            tok = secrets.token_urlsafe(24)
            self.e.store.kv_set("remote_token", tok)
        return tok

    @property
    def port(self) -> int:
        return self.e.config.settings.remote_port

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def url(self) -> str:
        return f"http://{lan_ip()}:{self.port}/?token={self.token}#/remote"

    async def start(self) -> None:
        if self.running:
            return
        import uvicorn

        from .api import create_app

        # Fail fast (and visibly) if the port is taken.
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("0.0.0.0", self.port))
        app = create_app(self.e, self.token, shell=None, remote=True)
        cfg = uvicorn.Config(app, host="0.0.0.0", port=self.port, log_level="warning", access_log=False, log_config=None, lifespan="off")
        self._server = uvicorn.Server(cfg)
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._task = asyncio.create_task(self._server.serve(), name="remote-server")
        for _ in range(100):
            await asyncio.sleep(0.03)
            if self._server.started or self._task.done():
                break
        if self._task.done() and self._task.exception():
            self.error = str(self._task.exception())
            raise OSError(self.error)
        self.error = None
        log.info("Phone remote listening on %s:%d", lan_ip(), self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
        self._server = self._task = None

    async def rotate(self) -> None:
        """Invalidate the old QR/link (e.g. a lost phone) and issue a new token."""
        self.e.store.kv_set("remote_token", secrets.token_urlsafe(24))
        if self.running:
            await self.stop()
            await self.start()

    def status(self, with_qr: bool = False) -> dict:
        out = {"enabled": self.e.config.settings.remote_enabled, "running": self.running, "port": self.port,
               "ip": lan_ip(), "error": self.error}
        if self.running:
            out["url"] = self.url()
            if with_qr:
                try:
                    import qrcode
                    import qrcode.image.svg

                    img = qrcode.make(out["url"], image_factory=qrcode.image.svg.SvgPathImage, border=2)
                    out["qr_svg"] = img.to_string().decode()
                except Exception as exc:  # qrcode missing: the link still works
                    log.info("QR unavailable: %s", exc)
        return out
