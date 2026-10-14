"""Reach ORION from anywhere — securely, for free.

* **Tailscale** (recommended): your PC and phone join a private, encrypted
  network. ORION asks Tailscale to serve the phone app over HTTPS at a stable
  address like ``https://my-pc.tailnet-name.ts.net``.
* **Instant link** (Cloudflare quick tunnel): no account or setup. ORION runs
  ``cloudflared`` and gets an HTTPS address like ``https://xyz.trycloudflare.com``.
  The address changes when ORION restarts; paired phones get a push
  notification with a one-tap re-pair link.

Both give HTTPS, which phones require for the microphone and notifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("orion.tunnel")
IS_WIN = sys.platform == "win32"
CF_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _creationflags() -> int:
    return 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW


async def _run(*args: str, timeout: float = 20) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                creationflags=_creationflags())
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "timed out"
    return proc.returncode or 0, out.decode(errors="replace")


def tailscale_cli() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    for cand in (r"C:\Program Files\Tailscale\tailscale.exe", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if Path(cand).exists():
            return cand
    return None


class Tunnel:
    def __init__(self, bin_dir: Path, on_url_change: Callable[[str | None, str | None], Awaitable[None]]) -> None:
        self.bin_dir = bin_dir
        self.on_url_change = on_url_change
        self.mode = "lan"
        self.url: str | None = None
        self.state = "off"  # off | starting | ready | error
        self.detail = ""
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._port = 0

    def status(self) -> dict:
        return {"mode": self.mode, "url": self.url, "state": self.state, "detail": self.detail,
                "tailscale_installed": tailscale_cli() is not None,
                "cloudflared_installed": self._cloudflared_path() is not None}

    async def start(self, mode: str, port: int) -> None:
        await self.stop()
        self.mode, self._port = mode, port
        if mode == "tailscale":
            await self._start_tailscale()
        elif mode == "cloudflare":
            self.state, self.detail = "starting", "Starting secure link…"
            self._task = asyncio.create_task(self._cloudflare_loop(), name="cloudflared")
        else:
            self.state, self.url, self.detail = "off", None, ""

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        self._proc = None
        if self.mode == "tailscale" and self.state == "ready" and tailscale_cli():
            await _run(tailscale_cli(), "serve", "--https=443", "off", timeout=10)
        old, self.url, self.state = self.url, None, "off"
        if old:
            await self.on_url_change(old, None)

    # ── Tailscale ──────────────────────────────────────────────────────────
    async def _start_tailscale(self) -> None:
        cli = tailscale_cli()
        if not cli:
            self.state, self.detail = "error", "Tailscale isn't installed on this PC. Get it free at tailscale.com/download."
            return
        code, out = await _run(cli, "status", "--json")
        try:
            info = json.loads(out)
        except ValueError:
            self.state, self.detail = "error", "Tailscale is installed but not running or not signed in."
            return
        if info.get("BackendState") != "Running":
            self.state, self.detail = "error", "Open Tailscale and sign in, then try again."
            return
        me = info.get("Self", {})
        dns = (me.get("DNSName") or "").rstrip(".")
        ips = me.get("TailscaleIPs") or []
        # HTTPS via `tailscale serve` (newer CLI syntax first, then the older one).
        for args in (("serve", "--bg", "--https=443", f"http://127.0.0.1:{self._port}"),
                     ("serve", "--bg", str(self._port)),
                     ("serve", "https", "/", f"http://127.0.0.1:{self._port}")):
            code, out = await _run(cli, *args)
            if code == 0 and dns:
                self.url, self.state, self.detail = f"https://{dns}", "ready", "Private HTTPS via Tailscale"
                await self.on_url_change(None, self.url)
                return
        if ips:
            self.url = f"http://{ips[0]}:{self._port}"
            self.state = "ready"
            self.detail = ("Connected over Tailscale without HTTPS. Turn on HTTPS certificates in the Tailscale admin "
                           "console (DNS → HTTPS) to enable phone voice and notifications.")
            await self.on_url_change(None, self.url)
            return
        self.state, self.detail = "error", f"Tailscale couldn't share ORION: {out.strip()[:200]}"

    # ── Cloudflare quick tunnel ────────────────────────────────────────────
    def _cloudflared_path(self) -> str | None:
        local = self.bin_dir / ("cloudflared.exe" if IS_WIN else "cloudflared")
        if local.exists():
            return str(local)
        return shutil.which("cloudflared")

    async def _download_cloudflared(self) -> str:
        import httpx

        machine = platform.machine().lower()
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        name = f"cloudflared-windows-{arch}.exe" if IS_WIN else (
            f"cloudflared-darwin-{arch}.tgz" if sys.platform == "darwin" else f"cloudflared-linux-{arch}")
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{name}"
        self.detail = "Downloading Cloudflare's tunnel tool (one time, ~40 MB)…"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        dest = self.bin_dir / ("cloudflared.exe" if IS_WIN else "cloudflared")
        tmp = dest.with_suffix(".part")
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        f.write(chunk)
        if name.endswith(".tgz"):
            import tarfile

            with tarfile.open(tmp) as t:
                member = next(m for m in t.getmembers() if m.name.endswith("cloudflared"))
                with t.extractfile(member) as src, open(dest, "wb") as out:  # type: ignore[union-attr]
                    out.write(src.read())
            tmp.unlink()
        else:
            tmp.replace(dest)
        os.chmod(dest, 0o755)
        return str(dest)

    async def _cloudflare_loop(self) -> None:
        backoff = 3
        while True:
            try:
                exe = self._cloudflared_path() or await self._download_cloudflared()
                self.detail = "Opening secure link…"
                self._proc = await asyncio.create_subprocess_exec(
                    exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self._port}",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, creationflags=_creationflags())
                assert self._proc.stdout
                async for raw in self._proc.stdout:
                    line = raw.decode(errors="replace")
                    m = CF_URL.search(line)
                    if m and m.group(0) != self.url:
                        old, self.url = self.url, m.group(0)
                        self.state, self.detail = "ready", "Secure link via Cloudflare (changes when ORION restarts)"
                        backoff = 3
                        log.info("Cloudflare link: %s", self.url)
                        await self.on_url_change(old, self.url)
                await self._proc.wait()
                self.state, self.detail = "starting", "Link dropped — reconnecting…"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("cloudflared failed: %s", exc)
                self.state, self.detail = "error", f"Couldn't open the link: {exc}"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
