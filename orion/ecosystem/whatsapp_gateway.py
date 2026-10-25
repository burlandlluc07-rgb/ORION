"""Personal WhatsApp in the background, through a local linked-device gateway (opt-in, off by default).

The gateway is GOWA (github.com/aldinokemal/go-whatsapp-web-multidevice, MIT, built on the whatsmeow library). It
links to the user's own WhatsApp the way WhatsApp Web does (Linked devices → scan a QR code), then sends and reads
messages over plain HTTP, so nothing opens on screen: no window, no mouse, no focus.

This is unofficial. WhatsApp's terms don't allow unofficial clients and it can restrict or ban accounts that use
them, mostly for bulk or automated sending. That is why it is off until the user turns it on and accepts the
warning in Accounts → WhatsApp, and why every send still waits for the user's yes to that exact message.

How it is kept tight:

* One pinned release, downloaded from GitHub only when the user turns it on. The zip and the program inside it are
  both checked against SHA-256 hashes written here, and the program is checked again before every start.
* It listens on 127.0.0.1 only, on a random free port, behind a random password that lives in the OS credential
  vault (never on the command line, in logs or in memory files).
* Media downloads, its web dashboard and its MCP endpoint are off. Its own output is discarded, so message text
  never reaches a log file.
* Its session and chat history stay in ORION's data folder; "Unlink and delete" logs it out of WhatsApp and
  removes that folder.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import io
import logging
import os
import platform as _platform
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx

from ..tools.registry import ToolError

log = logging.getLogger("orion.whatsapp.gateway")

VERSION = "9.4.0"
RELEASES = "https://github.com/aldinokemal/go-whatsapp-web-multidevice/releases/download"
# (zip name, program inside it, sha256 of the zip, sha256 of the program), checked by hand on 2026-10-25.
BUILDS = {
    "windows-amd64": (f"whatsapp_{VERSION}_windows_amd64.zip", "windows-amd64.exe",
                      "c89e0bcdf1ea08407d2cee7116598241ba06b242b734c9291f408c6ecfd5ffc4",
                      "2340a84cb3bffb7ee15a1c18939edebfe152e10d7aac422688e51477efdf5962"),
    "linux-amd64": (f"whatsapp_{VERSION}_linux_amd64.zip", "linux-amd64",
                    "5333922d8c0259c6b279e5a5a0600e774204ee088b6301c29a5654e830264ccb",
                    "cf9debfaaaadef7c2c098cd7a02bc9de8eeb56637656366bd530f1d87ae3a291"),
}
MAX_DOWNLOAD = 60_000_000
USER = "orion"
DEVICE = "orion"  # GOWA 8+ serves several accounts; ORION uses one slot with this id
SECRET_KEY, SECRET_FILE = "whatsapp:gateway", ".whatsapp_gateway_key"
# Chats that aren't conversations: Status posts, broadcast lists and channels.
NOT_CHATS = ("status@broadcast", "@broadcast", "@newsletter")


class GatewayDown(ToolError):
    """The gateway couldn't be reached, so nothing was handed to WhatsApp (safe to try another route)."""


def build_key() -> str | None:
    machine = _platform.machine().lower()
    arch = "amd64" if machine in ("amd64", "x86_64") else machine
    key = f"{'windows' if sys.platform == 'win32' else sys.platform}-{arch}"
    return key if key in BUILDS else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn(cmd: list[str], env: dict, cwd: Path) -> Any:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window on Windows
    return subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags)


def jid_for(number: str) -> str:
    return f"{number}@s.whatsapp.net"


class Gateway:
    """Downloads, starts and talks to the local gateway. ``transport`` and ``spawn`` are swapped out in tests."""

    def __init__(self, folder: Path, get_secret: Callable[[], str], set_secret: Callable[[str], None],
                 clear_secret: Callable[[], None], transport: httpx.BaseTransport | None = None,
                 spawn: Callable[[list[str], dict, Path], Any] = _spawn) -> None:
        self.folder = folder
        self.get_secret, self.set_secret, self.clear_secret = get_secret, set_secret, clear_secret
        self.transport = transport
        self.spawn = spawn
        self.proc: Any = None
        self.port = 0
        self.linked = False
        self.account = ""  # the linked number, digits only (shown masked)
        self.error = ""
        self._lock = threading.RLock()
        atexit.register(self.stop)

    # ── files ──────────────────────────────────────────────────────────────
    @property
    def program(self) -> Path:
        key = build_key()
        name = BUILDS[key][1] if key else "gowa"
        return self.folder / "bin" / name

    @property
    def installed(self) -> bool:
        return self.program.exists()

    def install(self) -> None:
        """Download the pinned release and unpack the program, refusing anything whose hash doesn't match."""
        key = build_key()
        if not key:
            raise ToolError("The background WhatsApp link needs 64-bit Windows.")
        zip_name, member, zip_sha, exe_sha = BUILDS[key]
        if self.installed and sha256_file(self.program) == exe_sha:
            return
        url = f"{RELEASES}/v{VERSION}/{zip_name}"
        buf = io.BytesIO()
        try:
            with httpx.Client(timeout=60, follow_redirects=True, transport=self.transport) as c:
                with c.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise ToolError(f"Couldn't download the WhatsApp link program (HTTP {r.status_code}).")
                    for chunk in r.iter_bytes():
                        buf.write(chunk)
                        if buf.tell() > MAX_DOWNLOAD:
                            raise ToolError("The WhatsApp link download was larger than expected, so it was dropped.")
        except httpx.HTTPError as exc:
            raise ToolError(f"Couldn't download the WhatsApp link program: {type(exc).__name__}.") from exc
        data = buf.getvalue()
        if hashlib.sha256(data).hexdigest() != zip_sha:
            raise ToolError("The downloaded WhatsApp link program didn't match its published fingerprint, so ORION "
                            "deleted it and didn't run it.")
        try:
            exe = zipfile.ZipFile(io.BytesIO(data)).read(member)
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ToolError("The WhatsApp link download wasn't the expected file.") from exc
        if hashlib.sha256(exe).hexdigest() != exe_sha:
            raise ToolError("The WhatsApp link program didn't match its fingerprint, so ORION didn't run it.")
        self.program.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.program.with_suffix(".part")
        tmp.write_bytes(exe)
        if sys.platform != "win32":
            os.chmod(tmp, 0o700)
        os.replace(tmp, self.program)
        log.info("whatsapp gateway: installed GOWA %s", VERSION)

    # ── process ────────────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            key = build_key()
            if not key or not self.installed:
                raise ToolError("The WhatsApp link program isn't installed; turn the background link on again.")
            if sha256_file(self.program) != BUILDS[key][3]:
                raise ToolError("The WhatsApp link program on disk has changed since it was checked, so ORION won't "
                                "run it. Turn the background link off and on again to download it fresh.")
            password = self.get_secret()
            if not password:
                password = secrets.token_urlsafe(24)
                self.set_secret(password)
            data = self.folder / "data"
            data.mkdir(parents=True, exist_ok=True)
            self.port = free_port()
            # The password goes in the environment, not the command line, where other programs could read it.
            env = {**os.environ, "APP_BASIC_AUTH": f"{USER}:{password}"}
            cmd = [str(self.program), "rest", "--host=127.0.0.1", f"--port={self.port}", "--os=ORION",
                   "--auto-download-media=false", "--ui-enabled=false", "--ui-auto-update=false",
                   "--mcp-enabled=false", "--debug=false"]
            self.proc = self.spawn(cmd, env, data)
            self.error = ""
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if not self.running:
                    break
                try:
                    with self._client(auth=False) as c:
                        if c.get("/health").status_code == 200:
                            break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                self.stop()
                raise ToolError("The WhatsApp link program didn't start in time.")
            if not self.running:
                self.proc = None
                raise ToolError("The WhatsApp link program stopped as soon as it started.")
            devices = self._api("GET", "/devices", device=False) or []
            if not any(d.get("id") == DEVICE for d in devices if isinstance(d, dict)):
                self._api("POST", "/devices", device=False, json={"device_id": DEVICE})
            log.info("whatsapp gateway: running on 127.0.0.1")
            self.refresh()

    def stop(self) -> None:
        with self._lock:
            proc, self.proc = self.proc, None
            if proc is None or proc.poll() is not None:
                return
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            log.info("whatsapp gateway: stopped")

    def uninstall(self) -> None:
        """Log the linked device out of WhatsApp, stop, and delete the program, session and chat history."""
        if self.running and self.linked:
            try:
                self._api("GET", "/app/logout")
            except ToolError:
                pass  # removing the session files below unlinks it on this side anyway
        self.stop()
        shutil.rmtree(self.folder, ignore_errors=True)
        self.clear_secret()
        self.linked, self.account = False, ""

    # ── HTTP ───────────────────────────────────────────────────────────────
    def _client(self, auth: bool = True, device: bool = True, timeout: float = 20) -> httpx.Client:
        headers = {"X-Device-Id": DEVICE} if device else {}
        return httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=timeout, transport=self.transport,
                            auth=(USER, self.get_secret()) if auth else None, headers=headers)

    def _api(self, method: str, path: str, device: bool = True, timeout: float = 20, **kw) -> Any:
        if not self.running:
            raise GatewayDown("The background WhatsApp link isn't running.")
        try:
            with self._client(device=device, timeout=timeout) as c:
                r = c.request(method, path, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise GatewayDown("The background WhatsApp link isn't answering.") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"The background WhatsApp link didn't answer in time ({type(exc).__name__}).") from exc
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 401 and not body:  # its own Basic Auth said no (WhatsApp's errors come as JSON)
            raise ToolError("The background WhatsApp link refused ORION's password. Turn it off and on again.")
        if r.status_code >= 400 or body.get("code") not in ("SUCCESS", 200, "200"):
            msg = str(body.get("message") or f"HTTP {r.status_code}")[:200]
            if msg == "reconnect error":
                msg = "couldn't connect to WhatsApp. Check the internet connection and try again."
            log.warning("whatsapp gateway: %s %s failed (%s)", method, path.split("?")[0].split("@")[0][:40],
                        body.get("code") or r.status_code)
            raise ToolError(f"WhatsApp link: {msg}")
        return body.get("results")

    # ── account ────────────────────────────────────────────────────────────
    def refresh(self) -> dict:
        """Ask the gateway whether the account is linked and connected."""
        try:
            st = self._api("GET", "/app/status", timeout=5) or {}
        except ToolError as exc:
            self.error = str(exc)
            return {}
        self.linked = bool(st.get("is_logged_in"))
        jid = str(st.get("jid") or "")
        self.account = jid.split("@")[0].split(":")[0] if self.linked else ""
        self.error = ""
        return st

    def login_qr(self) -> dict:
        """A QR code (as a data URL) to scan in WhatsApp → Linked devices → Link a device."""
        res = self._api("GET", "/app/login") or {}
        link = str(res.get("qr_link") or "")
        if not link:
            raise ToolError("The WhatsApp link didn't return a QR code; it may already be linked.")
        path = urllib.parse.urlparse(link).path  # the image is served by the same local gateway
        try:
            with self._client() as c:
                img = c.get(path)
        except httpx.HTTPError as exc:
            raise ToolError("Couldn't load the QR code from the WhatsApp link.") from exc
        if img.status_code != 200 or not img.content:
            raise ToolError("Couldn't load the QR code from the WhatsApp link.")
        return {"image": "data:image/png;base64," + base64.b64encode(img.content).decode(),
                "expires_in": int(res.get("qr_duration") or 30)}

    def login_code(self, number: str) -> str:
        """An 8-character code to type in WhatsApp → Linked devices → Link with phone number instead."""
        res = self._api("GET", "/app/login-with-code", params={"phone": number}) or {}
        code = str(res.get("pair_code") or "")
        if not code:
            raise ToolError("The WhatsApp link didn't return a pairing code.")
        return code

    # ── messages ───────────────────────────────────────────────────────────
    def send_text(self, number: str, text: str) -> str:
        """Hand one text message to WhatsApp. Returns its message id.

        Raises GatewayDown when the gateway couldn't be reached (nothing was sent), and ToolError for anything after
        the request went out, when it can't be known whether WhatsApp got it.
        """
        res = self._api("POST", "/send/message", timeout=30, json={"phone": jid_for(number), "message": text}) or {}
        mid = str(res.get("message_id") or "")
        if not mid:
            raise ToolError("The WhatsApp link answered without a message id, so ORION can't tell whether it went. "
                            "Check WhatsApp before sending again.")
        return mid

    def chats(self, limit: int = 10, search: str = "") -> list[dict]:
        params: dict = {"limit": min(max(int(limit), 1), 50) + 5}
        if search:
            params["search"] = search
        res = self._api("GET", "/chats", params=params) or {}
        rows = [c for c in (res.get("data") or []) if not str(c.get("jid", "")).endswith(NOT_CHATS)]
        return rows[: params["limit"] - 5]

    def messages(self, jid: str, limit: int = 10) -> list[dict]:
        res = self._api("GET", f"/chat/{urllib.parse.quote(jid, safe='@.')}/messages",
                        params={"limit": min(max(int(limit), 1), 50)}) or {}
        rows = list(res.get("data") or [])
        rows.sort(key=lambda m: str(m.get("timestamp") or ""))
        return rows
