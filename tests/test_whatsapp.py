"""WhatsApp (Cloud API): every message waits for approval of that exact message, and nothing leaks the token."""

import asyncio
import json
import sys
import types

import httpx
import pytest

from orion.ecosystem.integrations import status
from orion.ecosystem.whatsapp import mask, normalize_number
from orion.tools.kits import select_kits
from orion.tools.registry import ToolError

TOKEN = "EAAtest-token-never-shown"


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    """Never touch the real Windows Credential Manager from tests."""
    store: dict = {}
    mod = types.SimpleNamespace(set_password=lambda s, k, v: store.__setitem__((s, k), v),
                                get_password=lambda s, k: store.get((s, k)),
                                delete_password=lambda s, k: store.pop((s, k), None))
    monkeypatch.setitem(sys.modules, "keyring", mod)
    return store


class FakeMeta:
    """A stand-in for graph.facebook.com that records every request."""

    def __init__(self, send_response=None, status=200):
        self.requests: list[httpx.Request] = []
        self.send_response = send_response or {"messaging_product": "whatsapp",
                                               "contacts": [{"input": "34612345678", "wa_id": "34612345678"}],
                                               "messages": [{"id": "wamid.HBgLMzQ2MTIzNDU2NzgVAgARGBI"}]}
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": {"code": 190, "message": "Invalid OAuth access token"}})
        path = request.url.path
        if request.method == "GET" and path.endswith("/message_templates"):
            return httpx.Response(200, json={"data": [{"name": "hello_world", "language": "en_US",
                                                       "status": "APPROVED", "components": [
                                                           {"type": "BODY", "text": "Hello {{1}}"}]}]})
        if request.method == "GET":
            return httpx.Response(200, json={"display_phone_number": "+1 555 010 0199", "verified_name": "ORION Test",
                                              "id": "1234567890"})
        return httpx.Response(self.status, json=self.send_response)

    @property
    def sends(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


@pytest.fixture
def meta(engine):
    fake = FakeMeta()
    engine.whatsapp.transport = httpx.MockTransport(fake)
    return fake


def setup_real(engine, meta):
    engine.whatsapp.configure({"phone_number_id": "1234567890", "business_account_id": "987654321",
                               "access_token": TOKEN})
    engine.whatsapp.save_contact("Mom", "+34 612 345 678")


async def answer(engine, decision):
    for _ in range(100):
        pending = engine.approvals.list()
        if pending:
            engine.approvals.respond(pending[0]["id"], decision)
            return pending[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no approval was requested")


def test_numbers_are_normalized_and_masked():
    assert normalize_number("+34 612-345-678") == "34612345678"
    assert normalize_number("0034 612 345 678") == "34612345678"
    with pytest.raises(ToolError, match="country code"):
        normalize_number("0612 345 678")
    with pytest.raises(ToolError):
        normalize_number("call me")
    assert mask("34612345678") == "+•••••••5678"


def test_only_personal_drafts_until_set_up(engine):
    assert engine.registry.get("whatsapp_send") is None
    assert engine.registry.get("whatsapp_draft").risk == "confirm"
    assert next(s for s in status(engine) if s["id"] == "whatsapp")["state"] == "partial"


@pytest.mark.parametrize("app,url", [
    (True, "whatsapp://send?phone=34612345678&text=Running%2010%20min%20late%20%26%20sorry"),
    (False, "https://wa.me/34612345678?text=Running%2010%20min%20late%20%26%20sorry"),
])
async def test_personal_draft_opens_the_chat_typed_in_and_sends_nothing(engine, meta, no_side_effects, monkeypatch,
                                                                        app, url):
    import orion.ecosystem.whatsapp as wa

    monkeypatch.setattr(wa, "desktop_app_installed", lambda: app)
    engine.whatsapp.save_contact("Mom", "+34 612 345 678")
    task = engine.tracker.start("t", None)
    ok, res = await engine.agent.run_tool("whatsapp_draft", {"to": "Mom", "text": "Running 10 min late & sorry"},
                                          task, None)
    assert ok and "NOT sent yet" in res and no_side_effects == [url] and meta.requests == []
    step = task.steps[0]
    assert step["label"] == "Open WhatsApp with a message to Mom (+•••••••5678): “Running 10 min late & sorry” " \
                            "(you press Send)"
    life = step["lifecycle"]
    assert life["capabilities"]["desktop"] == "visible" and life["verification"]["status"] == "unverified"


async def test_personal_draft_waits_for_the_desktop_gate_when_asked(engine, no_side_effects, prompts_on, monkeypatch):
    import orion.ecosystem.whatsapp as wa

    monkeypatch.setattr(wa, "desktop_app_installed", lambda: True)
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_draft", {"text": "hi"}, task, None))
    card = await answer(engine, "deny")
    assert card["desktop"] and "a chat you pick" in card["label"]
    assert not (await run)[0] and no_side_effects == []


def test_setup_checks_meta_and_keeps_the_token_secret(engine, meta, fake_keyring, tmp_path):
    setup_real(engine, meta)
    assert meta.requests[0].method == "GET" and meta.requests[0].url.path.endswith("/1234567890")
    pub = engine.whatsapp.public()
    assert pub["verified_name"] == "ORION Test" and pub["has_token"] and pub["display_number"] == "+•••••••0199"
    assert pub["contacts"] == [{"name": "Mom", "number": "+•••••••5678"}]
    assert TOKEN not in json.dumps(pub) and TOKEN not in (tmp_path / "whatsapp.json").read_text()
    assert list(fake_keyring.values()) == [TOKEN]
    tool = engine.registry.get("whatsapp_send")
    assert tool.risk == "danger" and tool.always_ask and "whatsapp" in engine.registry.kits()
    assert engine.registry.get("whatsapp_templates") is not None
    assert select_kits("send a WhatsApp to mom") == {"whatsapp"}


def test_a_bad_token_is_not_saved(engine, meta, fake_keyring):
    with pytest.raises(ToolError, match="access token has expired or is invalid"):
        engine.whatsapp.configure({"phone_number_id": "1234567890", "access_token": "wrong"})
    assert not fake_keyring and engine.registry.get("whatsapp_send") is None


async def test_send_asks_even_with_prompts_off_and_shows_the_exact_message(engine, meta):
    assert engine.config.settings.approval_mode == "trust"  # 2.6 default: prompts off
    setup_real(engine, meta)
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", {"to": "mom", "text": "Running 10 min late"},
                                                    task, None))
    card = await answer(engine, "approve")
    assert card["label"] == "Send WhatsApp to Mom (+•••••••5678): “Running 10 min late”"
    assert card["risk"] == "danger"  # the card offers no "allow for this session"
    ok, res = await run
    assert ok and "accepted the message to Mom" in res and "wamid." in res and "Delivery isn't confirmed" in res
    assert meta.sends == [{"messaging_product": "whatsapp", "recipient_type": "individual", "to": "34612345678",
                           "type": "text", "text": {"preview_url": False, "body": "Running 10 min late"}}]
    life = task.steps[0]["lifecycle"]
    assert life["approval"]["granted"] and life["approval"]["by"] == "user"
    assert life["verification"]["status"] == "unverified" and "wamid." in life["verification"]["evidence"][0]


async def test_declined_message_is_never_sent(engine, meta):
    setup_real(engine, meta)
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", {"to": "Mom", "text": "hi"}, task, None))
    await answer(engine, "deny")
    ok, res = await run
    assert not ok and "declined" in res and meta.sends == []


async def test_allow_for_session_never_covers_the_next_message(engine, meta):
    setup_real(engine, meta)
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", {"to": "Mom", "text": "one"}, task, None))
    await answer(engine, "always")
    assert (await run)[0]
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", {"to": "Mom", "text": "two"}, task, None))
    card = await answer(engine, "deny")  # asked again
    assert "“two”" in card["label"] and not (await run)[0] and len(meta.sends) == 1


async def test_even_the_users_own_button_asks(engine, meta):
    setup_real(engine, meta)
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", {"to": "Mom", "text": "x"}, task, None,
                                                    origin="user"))
    await answer(engine, "deny")
    assert not (await run)[0] and meta.sends == []


async def test_template_for_people_outside_the_24_hour_window(engine, meta):
    setup_real(engine, meta)
    task = engine.tracker.start("t", None)
    args = {"to": "+44 7700 900123", "template": "hello_world", "template_language": "en_US",
            "template_params": ["Lluc"]}
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", args, task, None))
    card = await answer(engine, "approve")
    assert card["label"] == "Send WhatsApp to +••••••••0123: template “hello_world” (en_US) with Lluc"
    ok, res = await run
    assert ok and "won't deliver free text" not in res
    assert meta.sends[0]["template"] == {"name": "hello_world", "language": {"code": "en_US"}, "components": [
        {"type": "body", "parameters": [{"type": "text", "text": "Lluc"}]}]}
    assert engine.whatsapp.whatsapp_templates() == [{"name": "hello_world", "language": "en_US",
                                                     "status": "APPROVED", "body": "Hello {{1}}"}]


async def test_meta_errors_are_explained_and_reported_as_failed(engine, meta):
    setup_real(engine, meta)
    meta.status = 400
    meta.send_response = {"error": {"code": 131047, "message": "Re-engagement message", "type": "OAuthException"}}
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", {"to": "Mom", "text": "hi"}, task, None))
    await answer(engine, "approve")
    ok, res = await run
    assert not ok and "24 hours" in res and "template" in res
    assert task.steps[0]["lifecycle"]["stage"] == "failed"


def test_unknown_recipient_lists_saved_contacts(engine, meta):
    setup_real(engine, meta)
    with pytest.raises(ToolError, match="Saved contacts: Mom"):
        engine.whatsapp.whatsapp_send("Grandpa", "hi")
    assert meta.sends == []


async def test_dry_run_needs_no_meta_account_and_sends_nothing(engine, meta):
    engine.whatsapp.configure({"dry_run": True})
    engine.whatsapp.save_contact("Anna", "+34 699 000 111")
    assert next(s for s in status(engine) if s["id"] == "whatsapp")["state"] == "partial"
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send", {"to": "Anna", "text": "test"}, task, None))
    card = await answer(engine, "approve")
    assert card["label"].endswith("[dry run: nothing will be sent]")
    ok, res = await run
    assert ok and res.startswith("Dry run: nothing was sent") and meta.requests == []


def test_remove_forgets_the_token_but_keeps_contacts(engine, meta, fake_keyring):
    setup_real(engine, meta)
    engine.whatsapp.remove()
    assert not fake_keyring and engine.registry.get("whatsapp_send") is None
    assert engine.whatsapp.public()["contacts"][0]["name"] == "Mom"


def test_settings_api(engine, meta):
    from fastapi.testclient import TestClient

    from orion.server.api import create_app

    client = TestClient(create_app(engine, token="secret-token"), headers={"x-orion-token": "secret-token"})
    r = client.put("/api/accounts/whatsapp", json={"phone_number_id": "1234567890", "access_token": TOKEN})
    assert r.status_code == 200 and TOKEN not in r.text
    assert client.put("/api/accounts/whatsapp/contacts", json={"name": "Mom", "number": "612"}).status_code == 400
    r = client.put("/api/accounts/whatsapp/contacts", json={"name": "Mom", "number": "+34612345678"})
    assert r.json()["contacts"] == [{"name": "Mom", "number": "+•••••••5678"}]
    assert client.delete("/api/accounts/whatsapp/contacts/mom").json()["contacts"] == []


@pytest.fixture
def desktop_whatsapp(monkeypatch):
    """WhatsApp Desktop as far as ORION can see it: installed, and pressing Send works unless told otherwise."""
    import orion.ecosystem.whatsapp as wa

    pressed: list[str] = []
    monkeypatch.setattr(wa, "desktop_app_installed", lambda: True)

    def press(text):
        pressed.append(text)
        return "cleared"

    monkeypatch.setattr(wa, "press_send_in_whatsapp", press)
    return pressed


async def test_personal_send_asks_yes_or_no_then_presses_send(engine, no_side_effects, desktop_whatsapp):
    engine.whatsapp.save_contact("Mom", "+34 612 345 678")
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "On my way"},
                                                    task, None))
    card = await answer(engine, "approve")
    assert card["label"] == "Send WhatsApp from your own number to Mom (+•••••••5678): “On my way” " \
                            "(ORION opens WhatsApp and presses Send)"
    assert card["risk"] == "danger"
    ok, res = await run
    assert ok and res.startswith("Sent from your WhatsApp to Mom")
    assert no_side_effects == ["whatsapp://send?phone=34612345678&text=On%20my%20way"]
    assert desktop_whatsapp == ["On my way"]
    life = task.steps[0]["lifecycle"]
    assert life["capabilities"]["desktop"] == "input" and life["verification"]["status"] == "passed"


async def test_personal_send_no_opens_nothing(engine, no_side_effects, desktop_whatsapp):
    engine.whatsapp.save_contact("Mom", "+34 612 345 678")
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "x"}, task, None))
    await answer(engine, "deny")
    assert not (await run)[0] and no_side_effects == [] and desktop_whatsapp == []


async def test_personal_send_voice_yes(engine, no_side_effects, desktop_whatsapp):
    async def spoken_yes(info):
        engine.approvals.respond(info["id"], "approve")

    engine.approvals.on_voice_request = spoken_yes
    engine.whatsapp.save_contact("Mom", "+34 612 345 678")
    task = engine.tracker.start("t", None, "voice")
    ok, _ = await asyncio.wait_for(engine.agent.run_tool("whatsapp_send_personal", {"to": "Mom", "text": "Hi"},
                                                         task, None), 5)
    assert ok and desktop_whatsapp == ["Hi"]


def test_personal_send_needs_the_desktop_app_and_one_line(engine, no_side_effects, monkeypatch):
    import orion.ecosystem.whatsapp as wa

    engine.whatsapp.save_contact("Mom", "+34 612 345 678")
    monkeypatch.setattr(wa, "desktop_app_installed", lambda: False)
    with pytest.raises(ToolError, match="WhatsApp Desktop isn't installed"):
        engine.whatsapp.whatsapp_send_personal("Mom", "hi")
    with pytest.raises(ToolError, match="first line"):
        engine.whatsapp.whatsapp_send_personal("Mom", "line one\nline two")
    assert no_side_effects == []
