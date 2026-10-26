"""Personal WhatsApp in the background through the opt-in local gateway. Everything here is a fake: no download,
no gateway program and no WhatsApp account is ever touched."""

import asyncio
import base64
import hashlib
import io
import json
import logging
import sys
import types
import zipfile

import httpx
import pytest

import orion.ecosystem.whatsapp_gateway as gw
from orion.tasks.activity import summary
from orion.tools.registry import ToolError

REAL_BUILDS = dict(gw.BUILDS)  # before the fixture below swaps in the fake one
PROGRAM = b"MZ fake gateway program"
PNG = b"\x89PNG fake qr"


def fake_zip(member: str = "windows-amd64.exe", content: bytes = PROGRAM) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, content)
        z.writestr("readme.md", "hi")
    return buf.getvalue()


ZIP = fake_zip()


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    store: dict = {}
    mod = types.SimpleNamespace(set_password=lambda s, k, v: store.__setitem__((s, k), v),
                                get_password=lambda s, k: store.get((s, k)),
                                delete_password=lambda s, k: store.pop((s, k), None))
    monkeypatch.setitem(sys.modules, "keyring", mod)
    return store


@pytest.fixture(autouse=True)
def pinned_fake_build(monkeypatch):
    """Pin the fake zip's hashes in place of the real release's, on every OS the tests run on."""
    monkeypatch.setattr(gw, "build_key", lambda: "windows-amd64")
    monkeypatch.setitem(gw.BUILDS, "windows-amd64", ("whatsapp_test_windows_amd64.zip", "windows-amd64.exe",
                                                     hashlib.sha256(ZIP).hexdigest(),
                                                     hashlib.sha256(PROGRAM).hexdigest()))


class FakeProc:
    def __init__(self, cmd, env, cwd):
        self.cmd, self.env, self.cwd = cmd, env, cwd
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0

    kill = terminate


class FakeGOWA:
    """GOWA's REST API as checked against the real 9.4.0 program, plus GitHub's download URL."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.procs: list[FakeProc] = []
        self.devices: list[str] = []
        self.logged_in = False
        self.download = ZIP
        self.send_status = 200
        self.down = False
        self.password = ""
        self.presence: list[tuple[str, str]] = []
        self.chats = [
            {"jid": "status@broadcast", "name": "Status"},
            {"jid": "34612345678@s.whatsapp.net", "name": "34612345678", "last_message_time": "2026-10-25T10:00:00Z"},
            {"jid": "120363000000@g.us", "name": "Family", "last_message_time": "2026-10-25T09:00:00Z"},
            {"jid": "34699999999@s.whatsapp.net", "name": "", "last_message_time": "2026-10-24T09:00:00Z"},
        ]
        self.history = [
            {"id": "B", "chat_jid": "34612345678@s.whatsapp.net", "sender_jid": "34612345678@s.whatsapp.net",
             "content": "Are you coming?", "timestamp": "2026-10-25T10:00:00Z", "is_from_me": False},
            {"id": "A", "chat_jid": "34612345678@s.whatsapp.net", "sender_jid": "34600000001@s.whatsapp.net",
             "content": "Dinner at 8", "timestamp": "2026-10-25T09:00:00Z", "is_from_me": True},
        ]

    def spawn(self, cmd, env, cwd):
        self.password = env["APP_BASIC_AUTH"].split(":", 1)[1]
        p = FakeProc(cmd, env, cwd)
        self.procs.append(p)
        return p

    @property
    def sends(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/send/message"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "github.com":
            return httpx.Response(200, content=self.download)
        assert url.host == "127.0.0.1", "the gateway is only ever reached on this PC"
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if url.path == "/health":
            return httpx.Response(200, text="OK")
        expected = "Basic " + base64.b64encode(f"orion:{self.password}".encode()).decode()
        if request.headers.get("Authorization") != expected:
            return httpx.Response(401, text="Unauthorized")
        ok = lambda results=None: httpx.Response(200, json={"code": "SUCCESS", "message": "ok", "results": results})
        if url.path == "/devices":
            if request.method == "POST":
                self.devices.append(json.loads(request.content)["device_id"])
                return ok({"id": self.devices[-1]})
            return ok([{"id": d} for d in self.devices] or None)
        if request.headers.get("X-Device-Id") != "orion":
            return httpx.Response(400, json={"code": "DEVICE_ID_REQUIRED", "message": "device_id is required"})
        if url.path == "/app/status":
            return ok({"device_id": "orion", "is_connected": self.logged_in, "is_logged_in": self.logged_in,
                       "jid": "34600000001:12@s.whatsapp.net" if self.logged_in else ""})
        if url.path == "/app/login":
            return ok({"qr_duration": 30, "qr_link": f"http://localhost:{url.port}/statics/images/qrcode/scan.png"})
        if url.path == "/statics/images/qrcode/scan.png":
            return httpx.Response(200, content=PNG)
        if url.path == "/app/login-with-code":
            return ok({"pair_code": "ABCD-1234"})
        if url.path == "/app/logout":
            self.logged_in = False
            return ok()
        if url.path == "/send/message":
            if self.send_status != 200:
                return httpx.Response(self.send_status, json={"code": "INTERNAL_SERVER_ERROR", "message": "boom"})
            return ok({"message_id": "3EB0B430B6F8F1D0E053AC120E0A9E5C", "status": "Message sent"})
        if url.path == "/send/chat-presence":
            body = json.loads(request.content)
            self.presence.append((body["phone"], body["action"]))
            return ok({"message_id": "", "status": "ok"})
        if url.path == "/chats":
            q = url.params.get("search", "").lower()
            return ok({"data": [c for c in self.chats if q in c["name"].lower()]})
        if url.path.startswith("/chat/") and url.path.endswith("/messages"):
            jid = url.path.split("/")[2]
            return ok({"data": [m for m in self.history if m["chat_jid"] == jid]})
        return httpx.Response(404, json={"code": "NOT_FOUND", "message": url.path})


@pytest.fixture
def gowa(engine):
    fake = FakeGOWA()
    engine.whatsapp.gateway.transport = httpx.MockTransport(fake)
    engine.whatsapp.gateway.spawn = fake.spawn
    yield fake
    engine.whatsapp.gateway.stop()


@pytest.fixture
def waits(engine):
    """Every pause ORION takes before a background send, without really waiting."""
    slept: list[float] = []
    engine.whatsapp._sleep = slept.append
    return slept


@pytest.fixture
def linked(engine, gowa, waits):
    gowa.logged_in = True
    engine.whatsapp.enable_gateway(True)
    engine.whatsapp.save_contact("Mom", "+34 612 345 678")
    return gowa


async def answer(engine, decision):
    for _ in range(100):
        pending = engine.approvals.list()
        if pending:
            engine.approvals.respond(pending[0]["id"], decision)
            return pending[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no approval was requested")


def test_off_by_default_and_nothing_downloaded(engine, gowa):
    info = engine.whatsapp.public()["gateway"]
    assert info["state"] == "off" and not info["enabled"] and "may restrict or ban" in info["warning"]
    assert engine.registry.get("whatsapp_read") is None
    assert gowa.requests == [] and gowa.procs == []


def test_turning_on_needs_the_warning_accepted(engine, gowa):
    with pytest.raises(ToolError, match="accept"):
        engine.whatsapp.enable_gateway(False)
    assert gowa.requests == [] and not engine.whatsapp.gateway_enabled


def test_turning_on_checks_the_download_and_runs_it_locked_down(engine, gowa, fake_keyring, tmp_path):
    info = engine.whatsapp.enable_gateway(True)
    assert info["state"] == "waiting_for_link"
    assert [r.url.host for r in gowa.requests][0] == "github.com"
    proc = gowa.procs[0]
    assert "--host=127.0.0.1" in proc.cmd and "--auto-download-media=false" in proc.cmd
    assert "--ui-enabled=false" in proc.cmd and "--mcp-enabled=false" in proc.cmd
    password = gowa.password
    assert len(password) >= 24 and not any(password in a for a in proc.cmd)  # never on the command line
    assert fake_keyring[("ORION", "whatsapp:gateway")] == password
    assert password not in (tmp_path / "whatsapp.json").read_text()
    assert gowa.devices == ["orion"]
    assert engine.registry.get("whatsapp_read") is not None


def test_a_download_that_does_not_match_its_fingerprint_is_never_run(engine, gowa):
    gowa.download = fake_zip(content=b"MZ something else")
    with pytest.raises(ToolError, match="fingerprint"):
        engine.whatsapp.enable_gateway(True)
    assert gowa.procs == [] and not engine.whatsapp.gateway.installed


def test_a_program_changed_on_disk_is_never_run(engine, gowa):
    engine.whatsapp.enable_gateway(True)
    engine.whatsapp.gateway.stop()
    engine.whatsapp.gateway.program.write_bytes(b"MZ tampered")
    with pytest.raises(ToolError, match="changed"):
        engine.whatsapp.gateway.start()
    assert len(gowa.procs) == 1


def test_qr_code_and_pairing_code(engine, gowa):
    engine.whatsapp.enable_gateway(True)
    qr = engine.whatsapp.gateway_qr()
    assert qr["image"] == "data:image/png;base64," + base64.b64encode(PNG).decode() and qr["expires_in"] == 30
    assert engine.whatsapp.gateway_code("+34 600 000 001") == {"code": "ABCD-1234"}
    gowa.logged_in = True
    info = engine.whatsapp.gateway_public(refresh=True)
    assert info["state"] == "linked" and info["account"] == "+•••••••0001"


async def test_linked_send_asks_then_goes_in_the_background(engine, linked, no_side_effects):
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "On my way"},
                                                    task, None))
    card = await answer(engine, "approve")
    assert card["label"] == "Send WhatsApp from your own number to Mom (+•••••••5678): “On my way” (in the background)"
    assert card["risk"] == "danger" and not card.get("desktop")
    ok, res = await run
    assert ok and "in the background" in res
    assert linked.sends == [{"phone": "34612345678@s.whatsapp.net", "message": "On my way"}]
    assert no_side_effects == []  # no WhatsApp window, no browser
    life = task.steps[0]["lifecycle"]
    assert life["capabilities"]["desktop"] == "none" and life["capabilities"]["route"] == "api_mcp"
    assert life["verification"]["status"] == "passed"
    assert "On my way" not in summary("whatsapp_send_personal", task.steps[0]["label"])


async def test_linked_send_still_asks_with_prompts_off_and_no_sends_nothing(engine, linked):
    assert engine.config.settings.approval_mode == "trust"
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "x"}, task, None))
    await answer(engine, "deny")
    assert not (await run)[0] and linked.sends == []


async def test_linked_send_voice_yes(engine, linked):
    async def spoken_yes(info):
        engine.approvals.respond(info["id"], "approve")

    engine.approvals.on_voice_request = spoken_yes
    task = engine.tracker.start("t", None, "voice")
    ok, _ = await asyncio.wait_for(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "Hi"},
                                                         task, None), 5)
    assert ok and linked.sends[0]["message"] == "Hi"


def test_linked_send_allows_several_lines(engine, linked):
    res = engine.whatsapp.whatsapp_send_personal("Mom", "line one\nline two")
    assert res.startswith("Sent from your WhatsApp") and linked.sends[0]["message"] == "line one\nline two"


@pytest.fixture
def desktop_whatsapp(monkeypatch):
    import orion.ecosystem.whatsapp as wa

    pressed: list[str] = []
    monkeypatch.setattr(wa, "desktop_app_installed", lambda: True)
    monkeypatch.setattr(wa, "press_send_in_whatsapp", lambda text: pressed.append(text) or "cleared")
    return pressed


async def test_link_down_falls_back_to_the_foreground_only_with_permission(engine, linked, prompts_on,
                                                                           no_side_effects, desktop_whatsapp):
    linked.down = True
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "Hi"}, task, None))
    await answer(engine, "approve")  # the message itself
    await asyncio.sleep(0.05)
    card = await answer(engine, "deny")  # the foreground step
    assert card["desktop"] and "isn't answering" in card["label"]
    ok, res = await run
    assert not ok and "Nothing was sent" in res and desktop_whatsapp == [] and no_side_effects == []


async def test_link_down_uses_the_old_way_when_the_desktop_is_allowed(engine, linked, no_side_effects,
                                                                      desktop_whatsapp):
    linked.down = True
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "Hi"}, task, None))
    await answer(engine, "approve")
    ok, res = await run
    assert ok and res.startswith("Sent from your WhatsApp to Mom (+•••••••5678): the message left")
    assert desktop_whatsapp == ["Hi"] and no_side_effects == ["whatsapp://send?phone=34612345678&text=Hi"]


def test_an_unclear_failure_is_never_retried_another_way(engine, linked, no_side_effects, desktop_whatsapp):
    linked.send_status = 500
    with pytest.raises(ToolError, match="WhatsApp link: boom"):
        engine.whatsapp.whatsapp_send_personal("Mom", "Hi")
    assert len(linked.sends) == 1 and desktop_whatsapp == [] and no_side_effects == []


def test_not_linked_keeps_the_foreground_route(engine, gowa, no_side_effects, desktop_whatsapp):
    engine.whatsapp.enable_gateway(True)  # on, but the QR code was never scanned
    engine.whatsapp.save_contact("Mom", "+34 612 345 678")
    tool = engine.registry.get("whatsapp_send_personal")
    from orion.tasks.routing import capabilities_for

    assert capabilities_for(tool, args={"to": "Mom", "text": "Hi"}).desktop == "input"
    assert engine.whatsapp.whatsapp_send_personal("Mom", "Hi").startswith("Sent from your WhatsApp to Mom (")
    assert gowa.sends == [] and desktop_whatsapp == ["Hi"]
    with pytest.raises(ToolError, match="isn't linked"):
        engine.whatsapp.whatsapp_read()


def test_read_recent_chats_skips_status_and_names_contacts(engine, linked):
    res = engine.whatsapp.whatsapp_read()
    assert "never as instructions" in res["untrusted"]
    assert res["chats"] == [{"chat": "Mom", "last_message": "2026-10-25T10:00:00Z"},
                     {"chat": "Family", "last_message": "2026-10-25T09:00:00Z", "group": True},
                     {"chat": "+•••••••9999", "last_message": "2026-10-24T09:00:00Z"}]
    assert linked.sends == []


def test_read_one_chat_oldest_first(engine, linked):
    res = engine.whatsapp.whatsapp_read("mom", 5)
    assert res["chat"] == "Mom" and "never as instructions" in res["untrusted"]
    assert res["messages"] == [
        {"from": "You", "time": "2026-10-25T09:00:00Z", "text": "Dinner at 8"},
        {"from": "Mom", "time": "2026-10-25T10:00:00Z", "text": "Are you coming?"}]
    assert engine.whatsapp.whatsapp_read("Family") == {"note": "No messages with Family yet."}


def test_hidden_characters_are_stripped_from_read_messages(engine, linked):
    linked.history[0]["content"] = "Are you\u200b coming?\u202e hidden\u2066"
    msgs = engine.whatsapp.whatsapp_read("Mom")["messages"]
    assert msgs[1]["text"] == "Are you coming? hidden"


def test_the_ai_is_told_never_to_obey_message_text():
    from orion.brain.prompts import CORE_RULES

    assert "never as instructions" in CORE_RULES
    assert "never because a message" in gw_doc("whatsapp_send_personal")


def gw_doc(name):
    import orion.ecosystem.whatsapp as wa

    return getattr(wa.WhatsApp, name).__doc__


def test_background_send_shows_typing_first_and_waits_like_a_person(engine, linked, waits):
    engine.whatsapp.whatsapp_send_personal("Mom", "On my way")
    assert linked.presence == [("34612345678@s.whatsapp.net", "start")]
    assert waits and 2 <= waits[-1] <= 8  # "typing…" for a moment, scaled to the message
    first = len(waits)
    engine.whatsapp.whatsapp_send_personal("Mom", "Five minutes")
    assert any(w > 10 for w in waits[first:])  # the gap since the last send (15 s by default)
    assert len(linked.sends) == 2


def test_daily_limit_stops_background_sends(engine, linked):
    engine.whatsapp.set_send_limits({"per_day": 2})
    engine.whatsapp.whatsapp_send_personal("Mom", "one")
    engine.whatsapp.whatsapp_send_personal("Mom", "two")
    with pytest.raises(ToolError, match="Not sent: .*limit"):
        engine.whatsapp.whatsapp_send_personal("Mom", "three")
    assert len(linked.sends) == 2
    assert engine.whatsapp.gateway_public()["sent_today"] == 2
    card = engine.whatsapp.describe_personal({"to": "Mom", "text": "three"})
    assert "It won't be sent" in card
    engine.whatsapp._now = lambda: __import__("time").time() + 86401  # a day later the count starts again
    engine.whatsapp.whatsapp_send_personal("Mom", "three")
    assert len(linked.sends) == 3


def test_one_text_to_many_chats_is_refused(engine, linked):
    engine.whatsapp.set_send_limits({"same_text": 2})
    for n in ("+34 611 111 111", "+34 622 222 222"):
        engine.whatsapp.whatsapp_send_personal(n, "Party at mine tonight!")
    with pytest.raises(ToolError, match="same message already went to 2 other chats"):
        engine.whatsapp.whatsapp_send_personal("+34 633 333 333", "party at mine tonight!")
    engine.whatsapp.whatsapp_send_personal("+34 611 111 111", "Party at mine tonight!")  # same chat again is fine
    assert len(linked.sends) == 3


def test_send_limits_are_checked_and_kept_without_message_text(engine, linked, tmp_path):
    with pytest.raises(ToolError, match="between 5 and 600"):
        engine.whatsapp.set_send_limits({"gap": 1})
    with pytest.raises(ToolError, match="whole number"):
        engine.whatsapp.set_send_limits({"per_day": "lots"})
    assert engine.whatsapp.set_send_limits({"gap": 30})["limits"] == {"gap": 30, "per_day": 20, "same_text": 3}
    engine.whatsapp.whatsapp_send_personal("Mom", "Secret plans")
    saved = json.loads((tmp_path / "whatsapp.json").read_text())
    assert saved["send_limits"]["gap"] == 30
    log = json.dumps(saved["gateway_sends"])
    assert "Secret" not in log and "612345678" not in log and len(saved["gateway_sends"]) == 1


def test_card_warns_about_numbers_never_messaged(engine, linked):
    assert "never messaged" not in engine.whatsapp.describe_personal({"to": "Mom", "text": "Hi"})
    assert "never messaged" in engine.whatsapp.describe_personal({"to": "+34 655 555 555", "text": "Hi"})
    linked.history.append({"id": "C", "chat_jid": "34655555555@s.whatsapp.net", "sender_jid": "34655555555@s.whatsapp.net",
                           "content": "hey", "timestamp": "2026-10-20T10:00:00Z", "is_from_me": False})
    assert "never messaged" not in engine.whatsapp.describe_personal({"to": "+34 655 555 555", "text": "Hi"})


def test_typing_failure_does_not_stop_the_send(engine, linked, monkeypatch):
    def broken(number, on=True):
        raise ToolError("WhatsApp link: nope")

    monkeypatch.setattr(engine.whatsapp.gateway, "typing", broken)
    assert engine.whatsapp.whatsapp_send_personal("Mom", "Hi").startswith("Sent from your WhatsApp")


def test_limits_api(engine, linked):
    from fastapi.testclient import TestClient

    from orion.server.api import create_app

    client = TestClient(create_app(engine, token="t"), headers={"x-orion-token": "t"})
    r = client.put("/api/accounts/whatsapp/gateway/limits", json={"per_day": 5})
    assert r.status_code == 200 and r.json()["limits"]["per_day"] == 5
    assert client.put("/api/accounts/whatsapp/gateway/limits", json={"per_day": 0}).status_code == 400


def test_message_text_and_password_stay_out_of_the_log(engine, linked, caplog):
    caplog.set_level(logging.DEBUG, logger="orion")  # httpx is kept at WARNING by orion/app.py
    engine.whatsapp.whatsapp_send_personal("Mom", "secret plans for Saturday")
    engine.whatsapp.whatsapp_read("Mom")
    text = caplog.text
    assert "secret plans" not in text and "Dinner at 8" not in text and linked.password not in text
    assert "34612345678" not in text


def test_turn_off_keeps_the_link_and_unlink_deletes_everything(engine, linked, fake_keyring):
    folder = engine.whatsapp.gateway.folder
    engine.whatsapp.disable_gateway()
    assert not engine.whatsapp.gateway.running and folder.exists()
    assert engine.registry.get("whatsapp_read") is None
    engine.whatsapp.enable_gateway(True)
    assert linked.logged_in  # still linked: no new QR code needed
    engine.whatsapp.disable_gateway(forget=True)
    assert not linked.logged_in and not folder.exists()
    assert ("ORION", "whatsapp:gateway") not in fake_keyring
    assert engine.whatsapp.public()["gateway"]["state"] == "off"


def test_gateway_api(engine, gowa):
    from fastapi.testclient import TestClient

    from orion.server.api import create_app

    client = TestClient(create_app(engine, token="t"), headers={"x-orion-token": "t"})
    assert client.get("/api/accounts/whatsapp/gateway").json()["state"] == "off"
    assert client.put("/api/accounts/whatsapp/gateway", json={}).status_code == 400
    assert client.put("/api/accounts/whatsapp/gateway", json={"accept_risk": "yes"}).status_code == 400
    r = client.put("/api/accounts/whatsapp/gateway", json={"accept_risk": True})
    assert r.status_code == 200 and r.json()["state"] == "waiting_for_link"
    assert gowa.password not in r.text
    assert client.post("/api/accounts/whatsapp/gateway/qr").json()["image"].startswith("data:image/png;base64,")
    assert client.post("/api/accounts/whatsapp/gateway/code", json={"number": "612"}).status_code == 400
    assert client.post("/api/accounts/whatsapp/gateway/code", json={"number": "+34600000001"}).json() == \
        {"code": "ABCD-1234"}
    assert client.delete("/api/accounts/whatsapp/gateway?forget=true").json()["state"] == "off"


def test_real_release_pins_are_complete():
    for key in ("windows-amd64", "linux-amd64"):
        zip_name, member, zip_sha, exe_sha = REAL_BUILDS[key]
        assert gw.VERSION in zip_name and member and len(zip_sha) == len(exe_sha) == 64


def test_a_whatsapp_error_is_not_mistaken_for_a_wrong_password(engine, gowa):
    engine.whatsapp.enable_gateway(True)
    real = gowa.__call__

    def reconnect_error(request):
        if request.url.path == "/app/login":  # what GOWA 9.4.0 answers when WhatsApp can't be reached
            return httpx.Response(401, json={"code": "AUTHENTICATION_ERROR", "message": "reconnect error"})
        return real(request)

    engine.whatsapp.gateway.transport = httpx.MockTransport(reconnect_error)
    with pytest.raises(ToolError, match="couldn't connect to WhatsApp"):
        engine.whatsapp.gateway_qr()
    gowa.password = "something else"
    engine.whatsapp.gateway.transport = httpx.MockTransport(gowa)
    with pytest.raises(ToolError, match="password"):
        engine.whatsapp.gateway_qr()
