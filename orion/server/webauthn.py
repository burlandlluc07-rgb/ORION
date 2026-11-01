"""Fingerprint / face / screen-lock check for Approve on the phone app (WebAuthn, platform authenticator).

The phone registers a key held by its own secure hardware, unlocked only by the user's fingerprint, face or
screen lock ("user verification"). From then on, Approve from that phone must carry a fresh signature made
with that key over a one-time challenge from ORION. Deny never asks.

Only what ORION needs is implemented: "none" attestation, ES256 and RS256 keys, user verification required.
WebAuthn works only on https pages with a domain name (the Tailscale or Cloudflare link), not on the plain
http LAN address, so a key belongs to the domain it was made on (``rp_id``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import struct
import threading
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .devices import DeviceRegistry

CHALLENGE_TTL = 120  # seconds a challenge stays usable
FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40


class WebAuthnError(ValueError):
    pass


def b64d(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


# ── Minimal CBOR (only what attestation objects and COSE keys use) ─────────────────────────────────────
def cbor_decode(data: bytes) -> Any:
    return _cbor(data, 0)[0]


def _cbor(d: bytes, i: int) -> tuple[Any, int]:
    if i >= len(d):
        raise WebAuthnError("truncated CBOR")
    major, info = d[i] >> 5, d[i] & 0x1F
    i += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        n = 1 << (info - 24)
        if i + n > len(d):
            raise WebAuthnError("truncated CBOR")
        arg = int.from_bytes(d[i:i + n], "big")
        i += n
    else:
        raise WebAuthnError("unsupported CBOR")
    if major == 0:
        return arg, i
    if major == 1:
        return -1 - arg, i
    if major in (2, 3):
        if i + arg > len(d):
            raise WebAuthnError("truncated CBOR")
        raw = d[i:i + arg]
        return (raw if major == 2 else raw.decode("utf-8")), i + arg
    if major == 4:
        out = []
        for _ in range(arg):
            v, i = _cbor(d, i)
            out.append(v)
        return out, i
    if major == 5:
        m = {}
        for _ in range(arg):
            k, i = _cbor(d, i)
            v, i = _cbor(d, i)
            m[k] = v
        return m, i
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], i
    raise WebAuthnError("unsupported CBOR")


def cbor_encoded_length(data: bytes) -> int:
    return _cbor(data, 0)[1]


# ── Keys ───────────────────────────────────────────────────────────────────────────────────────────────
def cose_to_key(cose: dict) -> dict:
    alg = cose.get(3)
    if alg == -7 and cose.get(1) == 2 and cose.get(-1) == 1:  # ES256, P-256
        return {"alg": -7, "x": b64e(cose[-2]), "y": b64e(cose[-3])}
    if alg == -257 and cose.get(1) == 3:  # RS256
        return {"alg": -257, "n": b64e(cose[-1]), "e": b64e(cose[-2])}
    raise WebAuthnError("This phone's key type isn't supported.")


def verify_signature(key: dict, signature: bytes, data: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    try:
        if key["alg"] == -7:
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(b64d(key["x"]), "big"),
                                                int.from_bytes(b64d(key["y"]), "big"), ec.SECP256R1()).public_key()
            pub.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
        if key["alg"] == -257:
            pub = rsa.RSAPublicNumbers(int.from_bytes(b64d(key["e"]), "big"),
                                       int.from_bytes(b64d(key["n"]), "big")).public_key()
            pub.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
            return True
    except (InvalidSignature, ValueError):
        return False
    return False


def parse_auth_data(auth: bytes) -> dict:
    if len(auth) < 37:
        raise WebAuthnError("authenticator data too short")
    out = {"rp_hash": auth[:32], "flags": auth[32], "count": struct.unpack(">I", auth[33:37])[0]}
    if out["flags"] & FLAG_AT:
        if len(auth) < 55:
            raise WebAuthnError("authenticator data too short")
        n = struct.unpack(">H", auth[53:55])[0]
        out["cred_id"] = auth[55:55 + n]
        rest = auth[55 + n:]
        out["cose"] = cbor_decode(rest[:cbor_encoded_length(rest)])
    return out


def rp_from_origin(origin: str) -> str:
    return (urlsplit(origin).hostname or "").lower()


# ── Per-phone keys and challenges ──────────────────────────────────────────────────────────────────────
class PhoneBiometrics:
    """Keys live on the paired-device record (``devices[i]["webauthn"] = {rp_id: {...}}``)."""

    def __init__(self, devices: DeviceRegistry) -> None:
        self.devices = devices
        self._challenges: dict[str, tuple[str, str, float]] = {}  # challenge → (device id, purpose, time)
        self._lock = threading.Lock()

    def challenge(self, device_id: str, purpose: str) -> str:
        c = b64e(secrets.token_bytes(32))
        with self._lock:
            now = time.time()
            self._challenges = {k: v for k, v in self._challenges.items() if now - v[2] < CHALLENGE_TTL}
            self._challenges[c] = (device_id, purpose, now)
        return c

    def _take_challenge(self, challenge: str, device_id: str, purpose: str) -> None:
        with self._lock:
            item = self._challenges.pop(challenge, None)
        if not item or item[0] != device_id or item[1] != purpose or time.time() - item[2] > CHALLENGE_TTL:
            raise WebAuthnError("The check timed out. Try again.")

    def keys(self, device_id: str) -> dict[str, dict]:
        for d in self.devices._all():
            if d["id"] == device_id:
                return d.get("webauthn") or {}
        return {}

    def enrolled(self, device_id: str, rp_id: str) -> bool:
        return bool(rp_id) and rp_id in self.keys(device_id)

    def _client_data(self, raw_b64: str, kind: str, purpose: str, device_id: str, rp_id: str) -> bytes:
        raw = b64d(raw_b64)
        cd = json.loads(raw)
        if cd.get("type") != kind:
            raise WebAuthnError("wrong kind of check")
        self._take_challenge(cd.get("challenge", ""), device_id, purpose)
        if rp_from_origin(cd.get("origin", "")) != rp_id or not str(cd.get("origin", "")).startswith("https://"):
            raise WebAuthnError("The check came from a different address.")
        return raw

    def register(self, device_id: str, rp_id: str, body: dict) -> None:
        """Store the phone's new key after checking it was made here, now, with the user's fingerprint/lock."""
        self._client_data(body.get("clientDataJSON", ""), "webauthn.create", "register", device_id, rp_id)
        att = cbor_decode(b64d(body.get("attestationObject", "")))
        auth = parse_auth_data(att.get("authData", b""))
        if auth["rp_hash"] != hashlib.sha256(rp_id.encode()).digest():
            raise WebAuthnError("The key was made for a different address.")
        if not auth["flags"] & FLAG_UV:
            raise WebAuthnError("The phone didn't check your fingerprint or screen lock.")
        if "cose" not in auth:
            raise WebAuthnError("No key in the response.")
        key = cose_to_key(auth["cose"])
        devices = self.devices._all()
        for d in devices:
            if d["id"] == device_id:
                d.setdefault("webauthn", {})[rp_id] = {"id": b64e(auth["cred_id"]), "key": key,
                                                       "count": auth["count"], "created": time.time()}
        self.devices._save(devices)

    def verify(self, device_id: str, rp_id: str, purpose: str, assertion: dict | None) -> None:
        """Raise WebAuthnError unless ``assertion`` is a fresh, user-verified signature from this phone's key."""
        cred = self.keys(device_id).get(rp_id)
        if not cred:
            raise WebAuthnError("This phone has no fingerprint key for this address.")
        if not assertion:
            raise WebAuthnError("Confirm with your fingerprint or screen lock.")
        if assertion.get("id") != cred["id"]:
            raise WebAuthnError("Unknown key.")
        raw_cd = self._client_data(assertion.get("clientDataJSON", ""), "webauthn.get", purpose, device_id, rp_id)
        auth_raw = b64d(assertion.get("authenticatorData", ""))
        auth = parse_auth_data(auth_raw)
        if auth["rp_hash"] != hashlib.sha256(rp_id.encode()).digest():
            raise WebAuthnError("The check was made for a different address.")
        if not auth["flags"] & FLAG_UP or not auth["flags"] & FLAG_UV:
            raise WebAuthnError("The phone didn't check your fingerprint or screen lock.")
        if not verify_signature(cred["key"], b64d(assertion.get("signature", "")),
                                auth_raw + hashlib.sha256(raw_cd).digest()):
            raise WebAuthnError("The fingerprint check didn't match.")
        # A counter that goes backwards means a cloned key. Passkeys that don't count report 0 every time.
        if auth["count"] and cred.get("count") and auth["count"] <= cred["count"]:
            raise WebAuthnError("This key looks copied. Reset the fingerprint check in ORION on your PC.")
        if auth["count"]:
            devices = self.devices._all()
            for d in devices:
                if d["id"] == device_id and rp_id in (d.get("webauthn") or {}):
                    d["webauthn"][rp_id]["count"] = auth["count"]
            self.devices._save(devices)

    def reset(self, device_id: str) -> bool:
        devices = self.devices._all()
        found = False
        for d in devices:
            if d["id"] == device_id and d.get("webauthn"):
                d["webauthn"] = {}
                found = True
        self.devices._save(devices)
        return found
