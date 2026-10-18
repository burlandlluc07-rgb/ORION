import json
import sys
import types as pytypes

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

from orion.brain import backup
from orion.brain.backup import (
    BackupError,
    NeedsGemini,
    estimate_tokens,
    fit_body,
    pick_default_model,
    to_messages,
    to_tools,
)
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
    groq = ["whisper-large-v3", "meta-llama/llama-guard-4-12b", "openai/gpt-oss-120b", "qwen/qwen3-32b"]
    assert pick_default_model("groq", groq) == "openai/gpt-oss-120b"


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
    with pytest.raises(BackupError, match="daily quota is used up.*free plan is used up for now. Try again in a minute"):
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


# ── Requests too big for a small plan (Groq's 8000 tokens per minute) ─────
def groq_limit(limit=8000):
    """A Groq-like server that refuses requests over ``limit`` tokens (prompt + max_tokens) with its real 413.
    Its tokenizer counts about 0.6 of ORION's cautious estimate, as Groq's did for the request Lluc saw."""
    seen: list[dict] = []

    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        requested = int((estimate_tokens(body) - body["max_tokens"]) * 0.6) + body["max_tokens"]
        if requested > limit:
            return httpx.Response(413, json={"error": {"type": "tokens", "code": "rate_limit_exceeded", "message": (
                "Request too large for model `openai/gpt-oss-120b` in organization `org_01m2ea95jfe3hv76mztrtrfgc0` "
                f"service tier `on_demand` on tokens per minute (TPM): Limit {limit}, Requested {requested}, please "
                "reduce your message size and try again.")}})
        if body.get("stream"):
            return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "Fits now"}}]}),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Fits now"}}]})
    return seen, handle


def big_history(engine, turns=12):
    """A real ORION request: its own system prompt and every tool, plus a long chat with tool calls."""
    contents = []
    for i in range(turns):
        contents.append(types.Content(role="user", parts=[types.Part(text=f"question {i} " + "words " * 150)]))
        contents.append(types.Content(role="model", parts=[types.Part.from_function_call(name="web_search", args={"q": str(i)})]))
        contents.append(types.Content(role="user", parts=[types.Part.from_function_response(
            name="web_search", response={"result": "found " * 600})]))
        contents.append(types.Content(role="model", parts=[types.Part(text=f"answer {i} " + "text " * 100)]))
    contents.append(types.Content(role="user", parts=[types.Part(text="what's on my calendar tomorrow?")]))
    system = engine.agent.system_prompt("what's on my calendar tomorrow?")
    engine.agent._kits["c"] = {"calendar"}
    return contents, system, engine.agent.tool_declarations("c")


async def test_too_large_request_is_trimmed_to_fit_and_sent_again(on_omniroute, monkeypatch):
    engine = on_omniroute
    gemini_out(engine, monkeypatch)
    seen, handle = groq_limit()
    real = httpx.AsyncClient
    monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    contents, system, tools = big_history(engine)

    out = [kv async for kv in engine.brain.stream(contents, system=system, tools=tools)]
    assert "".join(v.text for k, v in out if k == "part") == "Fits now"
    assert len(seen) == 2 and seen[1]["max_tokens"] < seen[0]["max_tokens"]
    fitted = seen[1]["messages"]
    assert fitted[0]["role"] == "system" and fitted[-1] == {"role": "user", "content": "what's on my calendar tomorrow?"}
    names = {t["function"]["name"] for t in seen[1]["tools"]}
    assert {"calendar_add_event", "open_toolkit", "web_search"} <= names and len(names) < 12  # the tools it needs

    # The limit is remembered: the next request is trimmed before it's sent, with no refused round trip.
    seen.clear()
    assert await engine.brain.generate_text(contents, system=system) == "Fits now"
    assert len(seen) == 1


async def test_still_too_large_says_so_plainly(on_omniroute, monkeypatch):
    engine = on_omniroute
    gemini_out(engine, monkeypatch)
    seen, handle = groq_limit(limit=300)  # smaller than the request ORION needs at its smallest
    real = httpx.AsyncClient
    monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    contents, system, tools = big_history(engine, turns=1)
    with pytest.raises(BackupError, match="daily quota is used up.*too big for the backup brain's plan") as err:
        async for _ in engine.brain.stream(contents, system=system, tools=tools):
            pass
    assert "org_" not in str(err.value) and len(seen) == 3


async def test_groq_requests_are_lean_from_the_first_send(engine, monkeypatch):
    """Groq's free plan is 8000 tokens a minute: a plain "hi" must cost a fraction of it, not the whole minute."""
    engine.config.settings.backup_provider = "groq"
    monkeypatch.setenv("ORION_BACKUP_API_KEY", "gsk_test")
    engine.config.settings.backup_model = "openai/gpt-oss-120b"
    gemini_out(engine, monkeypatch)
    seen, handle = groq_limit()
    real = httpx.AsyncClient
    monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    contents, system, tools = big_history(engine)
    full = estimate_tokens(engine.brain.backup._body(contents, system, tools, "m", stream=True))

    hi = types.Content(role="user", parts=[types.Part(text="hi")])
    out = [kv async for kv in engine.brain.stream([hi], system=system, tools=tools)]
    assert "".join(v.text for k, v in out if k == "part") == "Fits now"
    assert len(seen) == 1  # no refused round trip first
    assert estimate_tokens(seen[0]) < 3500  # was about 14,500 with every tool
    assert {t["function"]["name"] for t in seen[0]["tools"]} == {"open_toolkit", "web_search", "remember"}

    # A long chat keeps only the last few turns, shortened.
    seen.clear()
    _ = [kv async for kv in engine.brain.stream(contents, system=system, tools=tools)]
    assert len(seen) == 1 and estimate_tokens(seen[0]) < full / 3
    assert sum(m["role"] == "user" and isinstance(m["content"], str) and m["content"].startswith("question")
               for m in seen[0]["messages"]) == backup.LEAN_TURNS
    assert seen[0]["messages"][-1] == {"role": "user", "content": "what's on my calendar tomorrow?"}


def test_lean_picks_the_tools_a_request_needs():
    tools = to_tools([{"name": n, "description": d} for n, d in (
        ("set_volume", "Set the master volume to a percentage."), ("get_weather", "Weather forecast for a place."),
        ("open_toolkit", "Load a toolkit."), ("web_search", "Search the web."), ("remember", "Save a fact."),
        ("kill_process", "End a running program."))])
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {
        "name": "kill_process", "arguments": "{}"}}]}
    body = {"messages": [{"role": "user", "content": "turn the volume down"}, call,
                         {"role": "tool", "tool_call_id": "c", "content": "ok"}], "tools": tools, "max_tokens": 4096}
    lean = backup.lean_body(body)
    assert [t["function"]["name"] for t in lean["tools"]] == [
        "set_volume", "open_toolkit", "web_search", "remember", "kill_process"]  # matched, core, already used
    assert lean["max_tokens"] == backup.LEAN_REPLY and len(body["tools"]) == 6


def test_rate_limit_error_says_how_long_to_wait():
    r = httpx.Response(429, json={"error": {"message": "Rate limit reached for model on tokens per minute (TPM): "
                                                        "Limit 8000, Used 7900, Requested 2600. Please try again in 18.2s."}})
    assert "Try again in about 18 seconds" in str(backup.BackupBrain._error(r))
    r = httpx.Response(429, json={"error": {"message": "tokens per day (TPD). Please try again in 12m30s."}})
    assert "Try again in about 12 minutes" in str(backup.BackupBrain._error(r))


def test_fit_keeps_tool_calls_with_their_results():
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "web_search", "arguments": "{}"}}]}
    old = [{"role": "user", "content": "old " * 2000}, call, {"role": "tool", "tool_call_id": "c1", "content": "r " * 3000},
           {"role": "assistant", "content": "done"}]
    now = [{"role": "user", "content": "now"}, {**call, "tool_calls": [{**call["tool_calls"][0], "id": "c2"}]},
           {"role": "tool", "tool_call_id": "c2", "content": "result " * 3000}]
    body = {"model": "m", "messages": [{"role": "system", "content": "sys " * 3000}, *old, *now], "max_tokens": 4096}
    fitted = fit_body(body, 2500)
    msgs = fitted["messages"]
    assert estimate_tokens(fitted) <= 2500
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]  # the old turn went whole
    assert msgs[1]["content"] == "now" and msgs[3]["tool_call_id"] == "c2" and msgs[3]["content"].endswith("backup brain]")
    assert body["messages"][1]["content"].startswith("old")  # the original is untouched


async def test_short_rate_limit_is_waited_out(on_omniroute, monkeypatch):
    engine = on_omniroute
    gemini_out(engine, monkeypatch)
    calls = []

    def handle(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached. Please try again in 2.5s."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "after the wait"}}]})

    waits = []

    async def no_sleep(s):
        waits.append(s)

    real = httpx.AsyncClient
    monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(backup.asyncio, "sleep", no_sleep)
    assert await engine.brain.generate_text("hi") == "after the wait"
    assert waits == [3.0]


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

