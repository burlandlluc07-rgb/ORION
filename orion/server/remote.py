"""Phone app server: a second, restricted API for paired phones.

Listens on the LAN (port 8790 by default) and, optionally, through a secure
tunnel (Tailscale or Cloudflare) so the phone app works from anywhere. Phones
pair once by scanning a QR code; each gets its own revocable token. The phone
can chat, talk, watch and control the PC, approve actions and run routines,
but can't change settings, accounts, connectors, plugins or skills.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING

from .devices import DeviceRegistry, PushSender
from .tunnel import Tunnel

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
        self.devices = DeviceRegistry(engine.store)
        self.push = PushSender(engine.config.root, self.devices)
        self.tunnel = Tunnel(engine.config.root / "bin", self._url_changed)
        self._server = None
        self._task: asyncio.Task | None = None
        self.error: str | None = None

    @property
    def port(self) -> int:
        return self.e.config.settings.remote_port

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def lan_url(self) -> str:
        return f"http://{lan_ip()}:{self.port}"

    def best_url(self) -> str:
        return self.tunnel.url or self.lan_url()

    async def start(self) -> None:
        if not self.running:
            import uvicorn

            from .api import create_app

            with socket.socket() as probe:  # fail fast (and visibly) if the port is taken
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("0.0.0.0", self.port))
            app = create_app(self.e, None, shell=None, remote=True)
            cfg = uvicorn.Config(app, host="0.0.0.0", port=self.port, log_level="warning", access_log=False,
                                 log_config=None, lifespan="off", proxy_headers=True, forwarded_allow_ips="127.0.0.1")
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
            log.info("Phone app server on %s", self.lan_url())
        mode = self.e.config.settings.remote_access
        if mode != self.tunnel.mode or (mode != "lan" and self.tunnel.state in ("off", "error")):
            await self.tunnel.start(mode, self.port)

    async def stop(self) -> None:
        await self.tunnel.stop()
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
        self._server = self._task = None

    async def _url_changed(self, old: str | None, new: str | None) -> None:
        self.e.bus.emit("remote.changed")
        if old and new and old != new:
            # Phones paired on the old address get a one-tap link to re-pair on the new one.
            code = self.devices.new_pairing_code()
            await asyncio.to_thread(self.push.send, "ORION has a new address",
                                    "Tap to reconnect your phone.", {"url": f"{new}/#/pair/{code}"}, "orion-url")

    def pair_link(self) -> str:
        return f"{self.best_url()}/#/pair/{self.devices.new_pairing_code()}"

    def status(self, with_qr: bool = False) -> dict:
        out = {"enabled": self.e.config.settings.remote_enabled, "running": self.running, "port": self.port,
               "lan_url": self.lan_url(), "access": self.e.config.settings.remote_access, "tunnel": self.tunnel.status(),
               "devices": self.devices.list(), "error": self.error, "secure": self.best_url().startswith("https://")}
        if self.running and with_qr:
            link = self.pair_link()
            out["pair_url"] = link
            try:
                import qrcode
                import qrcode.image.svg

                out["qr_svg"] = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, border=2).to_string().decode()
            except Exception as exc:
                log.info("QR unavailable: %s", exc)
        return out
