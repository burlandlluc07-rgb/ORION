"""Approvals queue (wait until answered, "Later", the jump-to hotkey) and the phone's fingerprint check for Approve."""

import asyncio
import hashlib
import json
import struct

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from orion.config import Settings
from orion.desktop.hotkeys import parse
from orion.server.api import create_app
from orion.server.webauthn import b64e, cbor_decode
from orion.tasks import tracker

from .test_phone import paired

ORIGIN = "https://orion-pc.tail1234.ts.net"
RP = "orion-pc.tail1234.ts.net"


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(tracker, "REPLY_MIN_AGE", 0)


async def _ask(engine, label, task=None, **kw):
    fut = asyncio.create_task(engine.approvals.ask("probe", label, "confirm", {}, task, **kw))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(i["label"] == label for i in engine.approvals.list()):
            return fut
    raise AssertionError("approval never appeared")


def _id(engine, label):
    return next(i["id"] for i in engine.approvals.list() if i["label"] == label)


# ── The queue ────────────────────────────────────────────────────────────────────────────────────────────

def test_defaults():
    s = Settings()
    assert s.approval_timeout == 0 and s.phone_biometric is True
    assert parse(s.hotkey_approvals) == (0x0002 | 0x0001, ord("A"))  # Ctrl+Alt+A


async def test_a_request_waits_until_answered_by_default(engine, monkeypatch):
    monkeypatch.setattr(tracker, "APPROVAL_TIMEOUT", 0.2)  # the old fixed limit no longer applies
    fut = await _ask(engine, "Send email")
    assert engine.approvals.list()[0]["expires"] is None
    await asyncio.sleep(1.2)  # well past any limit, and past the 0.5 s poll
    assert not fut.done()
    engine.approvals.respond(_id(engine, "Send email"), "deny")
    assert await fut == "deny"


async def test_time_limit_from_settings_lapses_without_doing_it(engine):
    engine.config.settings.approval_timeout = 1
    fut = await _ask(engine, "Send email")
    assert engine.approvals.list()[0]["expires"] is not None
    assert await asyncio.wait_for(fut, 4) == "expired"
    assert engine.approvals.list() == []


async def test_queue_is_arrival_order_and_later_moves_one_back(engine):
    a, b, c = [await _ask(engine, x) for x in ("First", "Second", "Third")]
    assert [i["label"] for i in engine.approvals.list()] == ["First", "Second", "Third"]
    assert engine.approvals.park(_id(engine, "First"))
    assert [i["label"] for i in engine.approvals.list()] == ["Second", "Third", "First"]
    assert engine.approvals.list()[-1]["parked"] is True
    await asyncio.sleep(0.6)
    assert not any(f.done() for f in (a, b, c))  # parking never answers anything
    assert engine.approvals.park(_id(engine, "First"), False)  # reopened: back with the waiting ones
    assert engine.approvals.list()[-1]["label"] == "First" and not engine.approvals.list()[-1]["parked"]
    engine.approvals.deny_all()
    assert await asyncio.gather(a, b, c) == ["deny"] * 3


async def test_focus_shows_the_front_one_and_never_answers(engine):
    assert engine.approvals.focus() is None
    a = await _ask(engine, "Only")
    engine.approvals.park(_id(engine, "Only"))
    front = engine.approvals.focus()  # everything is parked: the hotkey reopens the oldest
    assert front["label"] == "Only" and front["parked"] is False
    await asyncio.sleep(0.6)
    assert not a.done() and "probe" not in engine.approvals.session_allow
    engine.approvals.deny_all()
    await a


async def test_typed_yes_still_answers_and_a_parked_one_still_counts_as_waiting(engine, no_wait):
    a = await _ask(engine, "First")
    engine.approvals.park(_id(engine, "First"))
    assert engine.approvals.answer_from_text("yes")["label"] == "First"  # still one waiting: yes answers it
    assert await a == "approve" and "probe" not in engine.approvals.session_allow
    b = await _ask(engine, "Second")
    c = await _ask(engine, "Third")
    engine.approvals.park(_id(engine, "Second"))
    assert engine.approvals.answer_from_text("yes") == {"ambiguous": 2}  # unchanged rule: several → none
    assert not b.done() and not c.done()
    engine.approvals.deny_all()
    await asyncio.gather(b, c)


async def test_desktop_fallback_inside_a_tool_keeps_a_limit(engine):
    # A tool waiting mid-run for a foreground fallback must not hang forever.
    fut = asyncio.create_task(engine.approvals.request("type_text", "Type", "confirm", {}, None, desktop=True,
                                                       timeout=0.3))
    assert await asyncio.wait_for(fut, 3) is False


async def test_park_and_focus_endpoints(engine):
    client = TestClient(create_app(engine, "tok"))
    h = {"x-orion-token": "tok"}
    a = await _ask(engine, "First")
    b = await _ask(engine, "Second")
    aid = _id(engine, "First")
    assert client.post(f"/api/approvals/{aid}/park", headers=h, json={}).json() == {"ok": True}
    assert client.post("/api/approvals/focus", headers=h).json() == {"id": _id(engine, "Second")}
    assert [i["label"] for i in client.get("/api/state", headers=h).json()["approvals"]] == ["Second", "First"]
    engine.approvals.deny_all()
    await asyncio.gather(a, b)


# ── The phone's fingerprint check (a software authenticator stands in for the phone) ─────────────────────

def _cbor(v) -> bytes:
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
        raise ValueError
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(_cbor(k) + _cbor(x) for k, x in v.items())
    raise TypeError(v)


class Phone:
    """What the phone's secure hardware does: make a key after the fingerprint, sign challenges with it."""

    def __init__(self, rp=RP, origin=ORIGIN):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = b"cred-1234567890"
        self.rp, self.origin, self.count = rp, origin, 0

    def _client(self, kind, challenge, origin=None):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin}).encode()

    def create(self, challenge, uv=True):
        nums = self.key.public_key().public_numbers()
        cose = {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        flags = 0x01 | 0x40 | (0x04 if uv else 0)
        auth = (hashlib.sha256(self.rp.encode()).digest() + bytes([flags]) + struct.pack(">I", 0) + b"\0" * 16
                + struct.pack(">H", len(self.cred_id)) + self.cred_id + _cbor(cose))
        return {"clientDataJSON": b64e(self._client("webauthn.create", challenge)),
                "attestationObject": b64e(_cbor({"fmt": "none", "attStmt": {}, "authData": auth}))}

    def sign(self, challenge, uv=True, origin=None):
        self.count += 1
        cd = self._client("webauthn.get", challenge, origin)
        auth = hashlib.sha256(self.rp.encode()).digest() + bytes([0x01 | (0x04 if uv else 0)]) + struct.pack(">I", self.count)
        sig = self.key.sign(auth + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64e(self.cred_id), "clientDataJSON": b64e(cd), "authenticatorData": b64e(auth),
                "signature": b64e(sig)}


def test_cbor_round_trip():
    assert cbor_decode(_cbor({"a": b"\x01\x02", 3: -7, -1: 1})) == {"a": b"\x01\x02", 3: -7, -1: 1}


def _enrol(client, h, phone):
    o = client.post("/api/biometric/options", headers=h, json={"purpose": "register"}).json()
    assert o["rp_id"] == RP and o["credential_id"] is None
    r = client.post("/api/biometric/register", headers=h, json=phone.create(o["challenge"]))
    assert r.status_code == 200, r.text


def _approve(client, h, phone, aid, **kw):
    o = client.post("/api/biometric/options", headers=h, json={"purpose": f"approve:{aid}"}).json()
    return client.post(f"/api/approvals/{aid}", headers=h, json={"decision": "approve", "assertion": phone.sign(o["challenge"], **kw)})


async def test_enrolled_phone_must_pass_the_fingerprint_check_to_approve(engine):
    client, h = paired(engine)
    h = {**h, "origin": ORIGIN}
    phone = Phone()
    assert client.get("/api/biometric", headers=h).json() == {"enabled": True, "rp_id": RP, "enrolled": False}
    _enrol(client, h, phone)
    assert client.get("/api/biometric", headers=h).json()["enrolled"] is True
    assert engine.remote.devices.list()[0]["biometric"] is True and "webauthn" not in engine.remote.devices.list()[0]

    fut = await _ask(engine, "Send email")
    aid = _id(engine, "Send email")
    r = client.post(f"/api/approvals/{aid}", headers=h, json={"decision": "approve"})  # a tap alone
    assert r.status_code == 403 and r.json()["detail"]["code"] == "biometric_required"
    r = client.post(f"/api/approvals/{aid}", headers=h, json={"decision": "always"})
    assert r.status_code == 403
    assert _approve(client, h, phone, aid, uv=False).status_code == 403  # touched, but no fingerprint/lock
    assert not fut.done()
    assert _approve(client, h, phone, aid).json() == {"ok": True}
    assert await asyncio.wait_for(fut, 3) == "approve"


async def test_signature_is_one_time_and_bound_to_that_request(engine):
    client, h = paired(engine)
    h = {**h, "origin": ORIGIN}
    phone = Phone()
    _enrol(client, h, phone)
    a = await _ask(engine, "First")
    b = await _ask(engine, "Second")
    aid, bid = _id(engine, "First"), _id(engine, "Second")
    o = client.post("/api/biometric/options", headers=h, json={"purpose": f"approve:{aid}"}).json()
    signed = phone.sign(o["challenge"])
    # Made for "First": can't approve "Second".
    assert client.post(f"/api/approvals/{bid}", headers=h, json={"decision": "approve", "assertion": signed}).status_code == 403
    o = client.post("/api/biometric/options", headers=h, json={"purpose": f"approve:{aid}"}).json()
    signed = phone.sign(o["challenge"])
    assert client.post(f"/api/approvals/{aid}", headers=h, json={"decision": "approve", "assertion": signed}).json()["ok"]
    # Replaying the same signature is refused (the challenge is used up).
    assert client.post(f"/api/approvals/{bid}", headers=h, json={"decision": "approve", "assertion": signed}).status_code == 403
    # Signed on another site: refused.
    o = client.post("/api/biometric/options", headers=h, json={"purpose": f"approve:{bid}"}).json()
    bad = phone.sign(o["challenge"], origin="https://evil.example")
    assert client.post(f"/api/approvals/{bid}", headers=h, json={"decision": "approve", "assertion": bad}).status_code == 403
    # Another phone's key: refused.
    o = client.post("/api/biometric/options", headers=h, json={"purpose": f"approve:{bid}"}).json()
    assert client.post(f"/api/approvals/{bid}", headers=h,
                       json={"decision": "approve", "assertion": Phone().sign(o["challenge"])}).status_code == 403
    await asyncio.wait_for(a, 3)
    assert not b.done()
    engine.approvals.deny_all()
    await b


async def test_deny_never_asks_and_settings_can_turn_it_off(engine):
    client, h = paired(engine)
    h = {**h, "origin": ORIGIN}
    _enrol(client, h, Phone())
    a = await _ask(engine, "First")
    assert client.post(f"/api/approvals/{_id(engine, 'First')}", headers=h, json={"decision": "deny"}).json()["ok"]
    assert await asyncio.wait_for(a, 3) == "deny"
    engine.config.settings.phone_biometric = False
    b = await _ask(engine, "Second")
    assert client.post(f"/api/approvals/{_id(engine, 'Second')}", headers=h, json={"decision": "approve"}).json()["ok"]
    assert await asyncio.wait_for(b, 3) == "approve"


async def test_phone_without_a_key_works_as_before_and_pc_can_reset(engine):
    client, h = paired(engine)
    plain = {**h, "origin": "http://192.168.1.20:8790"}  # LAN address: no WebAuthn, no key
    a = await _ask(engine, "First")
    assert client.post(f"/api/approvals/{_id(engine, 'First')}", headers=plain, json={"decision": "approve"}).json()["ok"]
    assert await asyncio.wait_for(a, 3) == "approve"
    secure = {**h, "origin": ORIGIN}
    _enrol(client, secure, Phone())
    dev = engine.remote.devices.list()[0]["id"]
    # The phone can't reset its own key (that would undo the check); the PC can.
    assert client.delete(f"/api/devices/{dev}/biometric", headers=secure).status_code == 403
    pc = TestClient(create_app(engine, "tok"))
    assert pc.delete(f"/api/devices/{dev}/biometric", headers={"x-orion-token": "tok"}).json() == {"ok": True}
    assert client.get("/api/biometric", headers=secure).json()["enrolled"] is False


async def test_registration_needs_the_fingerprint_and_this_address(engine):
    client, h = paired(engine)
    h = {**h, "origin": ORIGIN}
    phone = Phone()
    o = client.post("/api/biometric/options", headers=h, json={"purpose": "register"}).json()
    assert client.post("/api/biometric/register", headers=h, json=phone.create(o["challenge"], uv=False)).status_code == 400
    other = Phone(rp="evil.example")
    o = client.post("/api/biometric/options", headers=h, json={"purpose": "register"}).json()
    assert client.post("/api/biometric/register", headers=h, json=other.create(o["challenge"])).status_code == 400
    assert client.post("/api/biometric/register", headers=h, json=phone.create("made-up")).status_code == 400
    assert client.get("/api/biometric", headers=h).json()["enrolled"] is False


async def test_background_agent_approve_from_phone_is_checked_too(engine):
    client, h = paired(engine)
    h = {**h, "origin": ORIGIN}
    _enrol(client, h, Phone())
    r = client.post("/api/background/approvals/42", headers=h, json={"decision": "approve"})
    assert r.status_code == 403
