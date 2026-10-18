import json
import sys
import types as pytypes

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

from orion.brain import backup
from orion.brain.backup import BackupError, NeedsGemini, pick_default_model, to_messages, to_tools
from orion.brain.governor import QuotaExhausted
from orion.server.api import create_app


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


@pytest.fixture
def provider(monkeypatch):
    """A fake OpenAI-compatible server; returns the list of requests it received."""
    seen: list[dict] = []
    replies: dict = {"stream": sse({"choices": [{"delta": {"content": "Hello "}}]},
                                   {"choices": [{"delta": {"content": "from backup"}}]},
                                   {"choices": [], "usage": {"total_tokens": 42}}),
                     "text": "OK", "models": ["auto", "other"], "status": 200}

    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        seen.append({"path": req.url.path, "auth": req.headers.get("authorization"), "body": body})
        if replies["status"] != 200:
            return httpx.Response(replies["status"], json={"error": {"message": "nope"}})
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in replies["models"]]})
        if body.get("stream"):
            return httpx.Response(200, text=replies["stream"], headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": replies["text"]}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    seen_replies = pytypes.SimpleNamespace(seen=seen, replies=replies)
    return seen_replies


@pytest.fixture
def on_omniroute(engine, monkeypatch):
    monkeypatch.delenv("ORION_BACKUP_API_KEY", raising=False)
    engine.config.settings.backup_provider = "omniroute"
    return engine


def gemini_out(engine, monkeypatch, exc=None):
    exc = exc or QuotaExhausted("Gemini's free daily quota is used up.")

    async def boom(*a, **k):
        raise exc
        yield  # pragma: no cover

    async def boom_text(*a, **k):
        raise exc

    monkeypatch.setattr(engine.brain, "_gemini_stream", boom)
    monkeypatch.setattr(engine.brain, "_gemini_text", boom_text)


# ── Translation ────────────────────────────────────────────────────────────
def test_contents_and_tools_translate_to_chat_completions():
    call = types.Part.from_function_call(name="open_app", args={"name": "Spotify"})
    contents = [
        types.Content(role="user", parts=[types.Part(text="open spotify"),
                                          types.Part.from_bytes(data=b"\x89PNG", mime_type="image/png")]),
        types.Content(role="model", parts=[types.Part(text="On it."), call]),
        types.Content(role="user", parts=[types.Part.from_function_response(name="open_app", response={"result": "ok"}),
                                          types.Part(text="(continue)")]),
    ]
    msgs = to_messages(contents, "be brief")
    assert msgs[0] == {"role": "system", "content": "be brief"}
    assert msgs[1]["content"][0] == {"type": "text", "text": "open spotify"}
    assert msgs[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    tc = msgs[2]["tool_calls"][0]
    assert msgs[2]["content"] == "On it." and tc["function"]["name"] == "open_app"
    assert json.loads(tc["function"]["arguments"]) == {"name": "Spotify"}
    assert msgs[3] == {"role": "tool", "tool_call_id": tc["id"], "content": '{"result": "ok"}'}
    assert msgs[4] == {"role": "user", "content": "(continue)"}

    tools = to_tools([{"name": "open_app", "description": "Open", "parameters_json_schema": {"type": "object"}}])
    assert tools == [{"type": "function", "function": {"name": "open_app", "description": "Open",
                                                       "parameters": {"type": "object"}}}]


def test_audio_needs_gemini():
    with pytest.raises(NeedsGemini):
        to_messages([types.Content(role="user", parts=[types.Part.from_bytes(data=b"RIFF", mime_type="audio/wav")])], None)


def test_default_model_pick():
    assert pick_default_model("openrouter", ["openai/gpt-5", "google/gemma-3-27b-it:free"]) == "google/gemma-3-27b-it:free"
    assert pick_default_model("openrouter", ["openai/gpt-5"]) == ""
    assert pick_default_model("omniroute", ["x", "auto"]) == "auto"


# ── Switching over ─────────────────────────────────────────────────────────
async def test_off_by_default_keeps_gemini_error(engine, provider, monkeypatch):
    gemini_out(engine, monkeypatch)
    with pytest.raises(QuotaExhausted):
        async for _ in engine.brain.stream([types.Content(role="user", parts=[types.Part(text="hi")])]):
            pass
    assert provider.seen == []


async def test_stream_switches_to_backup_when_quota_is_out(on_omniroute, provider, monkeypatch):
    engine = on_omniroute
    events = []
    monkeypatch.setattr(engine.bus, "emit", lambda kind, **kw: events.append((kind, kw)))
    gemini_out(engine, monkeypatch)
    out = [kv async for kv in engine.brain.stream([types.Content(role="user", parts=[types.Part(text="hi")])],
                                                  system="sys", tools=[{"name": "t", "parameters_json_schema": {}}])]
    assert out[0] == ("model", "backup · auto")
    assert "".join(v.text for k, v in out if k == "part") == "Hello from backup"
    assert out[-1] == ("usage", 42)
    req = provider.seen[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] is None  # OmniRoute without a key
    assert req["body"]["model"] == "auto" and req["body"]["tools"][0]["function"]["name"] == "t"
    assert engine.brain.on_backup and events[0][0] == "brain.backup" and events[0][1]["active"] is True


async def test_stream_tool_calls_come_back_as_gemini_function_calls(on_omniroute, provider, monkeypatch):
    engine = on_omniroute
    gemini_out(engine, monkeypatch)
    provider.replies["stream"] = sse(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "open_app", "arguments": "{\"na"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "me\": \"Spotify\"}"}}]}}]},
    )
    parts = [v async for k, v in engine.brain.stream([types.Content(role="user", parts=[types.Part(text="x")])]) if k == "part"]
    assert len(parts) == 1
    fc = parts[0].function_call
    assert fc.name == "open_app" and fc.args == {"name": "Spotify"} and fc.id == "c1"


async def test_rate_limits_on_every_gemini_model_also_switch(on_omniroute, provider, monkeypatch):
    engine = on_omniroute

    async def limited(model, contents, config):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})

    engine.brain._client = pytypes.SimpleNamespace(aio=pytypes.SimpleNamespace(
        models=pytypes.SimpleNamespace(generate_content=limited)))
    engine.brain._client_key = engine.config.get_api_key()
    assert await engine.brain.generate_text("hi") == "OK"
    assert engine.brain.on_backup


async def test_back_on_gemini_when_it_answers(on_omniroute, monkeypatch):
    engine = on_omniroute
    events = []
    monkeypatch.setattr(engine.bus, "emit", lambda kind, **kw: events.append((kind, kw)))
    engine.brain.on_backup = True

    async def fine(*a, **k):
        return "gemini again"

    monkeypatch.setattr(engine.brain, "_gemini_text", fine)
    assert await engine.brain.generate_text("hi") == "gemini again"
    assert not engine.brain.on_backup and events == [("brain.backup", {"active": False})]


async def test_audio_keeps_gemini_error_and_backup_errors_explain_both(on_omniroute, provider, monkeypatch):
    engine = on_omniroute
    gemini_out(engine, monkeypatch)
    with pytest.raises(QuotaExhausted):
        await engine.brain.transcribe(b"RIFF....")
    assert provider.seen == []
    provider.replies["status"] = 429
    with pytest.raises(BackupError, match="daily quota is used up.*rate-limited too"):
        await engine.brain.generate_text("hi")
    assert not engine.brain.on_backup


async def test_unreachable_omniroute_says_so(on_omniroute, monkeypatch):
    engine = on_omniroute
    gemini_out(engine, monkeypatch)

    def down(req):
        raise httpx.ConnectError("refused")

    real = httpx.AsyncClient
    monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(down), **kw))
    with pytest.raises(BackupError, match="Is OmniRoute running"):
        async for _ in engine.brain.stream([types.Content(role="user", parts=[types.Part(text="hi")])]):
            pass


# ── Settings API and key storage ───────────────────────────────────────────
class FakeKeyring(pytypes.ModuleType):
    def __init__(self):
        super().__init__("keyring")
        self.vault: dict = {}

    def get_password(self, service, user):
        return self.vault.get((service, user))

    def set_password(self, service, user, value):
        self.vault[(service, user)] = value

    def delete_password(self, service, user):
        self.vault.pop((service, user), None)


async def test_setup_tests_then_saves_key_in_vault_not_settings(engine, provider, monkeypatch, tmp_path):
    monkeypatch.delenv("ORION_BACKUP_API_KEY", raising=False)
    vault = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", vault)
    client = TestClient(create_app(engine, token="t"))
    h = {"x-orion-token": "t"}

    assert client.post("/api/backup", headers=h, json={"provider": "groq"}).status_code == 400  # needs a key
    provider.replies["models"] = ["llama-3.3-70b-versatile"]
    r = client.post("/api/backup", headers=h, json={"provider": "groq", "key": "gsk_secret_value_123"})
    assert r.status_code == 200, r.text
    assert r.json()["ready"] is True and r.json()["model"] == "llama-3.3-70b-versatile"
    assert provider.seen[0]["auth"] == "Bearer gsk_secret_value_123"
    assert vault.vault[("ORION", "backup_api_key")] == "gsk_secret_value_123"
    assert engine.config.settings.backup_provider == "groq"
    assert "gsk_secret_value_123" not in engine.config.path.read_text("utf-8")
    st = client.get("/api/state", headers=h).json()
    assert st["backup"]["ready"] and st["backup"]["has_key"] and "gsk_" not in json.dumps(st)

    provider.replies["status"] = 401  # a bad key is reported and nothing changes
    r = client.post("/api/backup", headers=h, json={"provider": "openrouter", "key": "sk-or-bad-key-0000"})
    assert r.status_code == 400 and "rejected the key" in r.json()["error"]
    assert engine.config.settings.backup_provider == "groq"

    r = client.post("/api/backup", headers=h, json={"provider": "off", "forget_key": True})
    assert r.status_code == 200 and engine.config.settings.backup_provider == "off"
    assert ("ORION", "backup_api_key") not in vault.vault
