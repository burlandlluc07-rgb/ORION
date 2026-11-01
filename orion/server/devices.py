"""Paired phones: one-time pairing codes, per-device tokens, Web Push, brute-force protection.

Pairing: the desktop shows a QR code containing a short-lived one-time code.
The phone exchanges it for its own long random token (only a SHA-256 hash is
stored). Each phone can be revoked individually. Failed logins are throttled
per IP, which matters once ORION is reachable from the internet.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..memory.store import Store

log = logging.getLogger("orion.devices")

PAIR_TTL = 600  # seconds a pairing code stays valid
FAIL_WINDOW, FAIL_LIMIT, BLOCK_FOR = 600, 12, 900


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DeviceRegistry:
    def __init__(self, store: "Store") -> None:
        self.store = store
        self._codes: dict[str, float] = {}
        self._fails: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    # ── devices ────────────────────────────────────────────────────────────
    def _all(self) -> list[dict]:
        return self.store.kv_get("devices", [])

    def _save(self, devices: list[dict]) -> None:
        self.store.kv_set("devices", devices)

    def list(self) -> list[dict]:
        return [{k: v for k, v in d.items() if k not in ("token_hash", "push", "webauthn")}
                | {"push": bool(d.get("push")), "biometric": bool(d.get("webauthn"))} for d in self._all()]

    def new_pairing_code(self) -> str:
        code = secrets.token_urlsafe(9)
        with self._lock:
            now = time.time()
            self._codes = {c: t for c, t in self._codes.items() if now - t < PAIR_TTL}
            self._codes[code] = now
        return code

    def pair(self, code: str, name: str) -> str | None:
        with self._lock:
            created = self._codes.pop(code, None)
        if created is None or time.time() - created > PAIR_TTL:
            return None
        token = secrets.token_urlsafe(32)
        devices = self._all()
        devices.append({"id": secrets.token_hex(6), "name": (name or "Phone")[:40], "token_hash": _hash(token),
                        "created": time.time(), "last_seen": time.time(), "push": None})
        self._save(devices)
        return token

    def authenticate(self, token: str) -> dict | None:
        if not token:
            return None
        h = _hash(token)
        devices = self._all()
        for d in devices:
            if secrets.compare_digest(d["token_hash"], h):
                if time.time() - d.get("last_seen", 0) > 60:
                    d["last_seen"] = time.time()
                    self._save(devices)
                return d
        return None

    def revoke(self, device_id: str) -> bool:
        devices = self._all()
        keep = [d for d in devices if d["id"] != device_id]
        self._save(keep)
        return len(keep) != len(devices)

    def set_push(self, device_id: str, subscription: dict | None) -> None:
        devices = self._all()
        for d in devices:
            if d["id"] == device_id:
                d["push"] = subscription
        self._save(devices)

    # ── brute-force protection ─────────────────────────────────────────────
    def blocked(self, ip: str) -> bool:
        until = self._blocked.get(ip)
        return bool(until and until > time.time())

    def record_failure(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            fails = [t for t in self._fails.get(ip, []) if now - t < FAIL_WINDOW] + [now]
            self._fails[ip] = fails
            if len(fails) >= FAIL_LIMIT:
                self._blocked[ip] = now + BLOCK_FOR
                log.warning("Blocking %s for %ds after %d failed logins", ip, BLOCK_FOR, len(fails))


class PushSender:
    """Web Push (VAPID) to paired phones — works on Android Chrome and iOS 16.4+ home-screen apps."""

    def __init__(self, root: Path, devices: DeviceRegistry) -> None:
        self.devices = devices
        self.key_file = root / "vapid.json"
        self._keys: dict[str, str] | None = None

    def keys(self) -> dict[str, str]:
        if self._keys:
            return self._keys
        if self.key_file.exists():
            self._keys = json.loads(self.key_file.read_text())
            return self._keys
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid

        v = Vapid()
        v.generate_keys()
        priv = v.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()).decode()
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self._keys = {"private_pem": priv, "public": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
        self.key_file.write_text(json.dumps(self._keys))
        return self._keys

    def send(self, title: str, body: str, data: dict[str, Any] | None = None, tag: str | None = None) -> int:
        """Send to every subscribed phone (blocking; call from a thread). Returns deliveries."""
        try:
            from py_vapid import Vapid
            from pywebpush import WebPushException, webpush
        except ImportError:
            return 0
        keys = self.keys()
        vapid = Vapid.from_pem(keys["private_pem"].encode())
        payload = json.dumps({"title": title, "body": body[:300], "data": data or {}, "tag": tag})
        sent = 0
        for d in self.devices._all():
            sub = d.get("push")
            if not sub:
                continue
            try:
                webpush(sub, payload, vapid_private_key=vapid, vapid_claims={"sub": "mailto:orion@localhost"},
                        ttl=3600, timeout=10)
                sent += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):  # subscription expired: forget it
                    self.devices.set_push(d["id"], None)
                log.info("Push to %s failed: %s", d["name"], exc)
            except Exception as exc:
                log.info("Push to %s failed: %s", d["name"], exc)
        return sent
