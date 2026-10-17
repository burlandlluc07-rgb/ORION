"""Gemini provider (the default)."""
from conftest import FakeGenai, gemini_reply

from orion_bg.agent_loop import DENIED
from orion_bg.gemini_agent import NUDGE, GeminiAgentRunner, gemini_api_key


def make(make_agent, cfg, script):
    client = FakeGenai(script)
    agent = make_agent(runners={"agent": GeminiAgentRunner(cfg, client=client)})
    agent.start()
    return agent, client


def results(call):
    return call["contents"][-1]["parts"]


def test_default_provider_is_gemini(cfg):
    from orion_bg.agent_loop import make_runner
    runner = make_runner(cfg)
    assert isinstance(runner, GeminiAgentRunner)
    assert cfg["gemini"]["model"] == "gemini-flash-latest"


def test_reads_file_and_answers(make_agent, cfg, workdir):
    (workdir / "notes.txt").write_text("buy milk")
    agent, client = make(make_agent, cfg, [
        gemini_reply("Reading. ", ("read_file", {"path": "notes.txt"})),
        gemini_reply("Your note says: buy milk"),
    ])
    done = agent.wait(agent.submit("What is in notes.txt?")["id"], 5)
    assert done["status"] == "completed", done["error"]
    assert done["result"] == "Your note says: buy milk"
    first, second = client.models.calls
    assert first["model"] == "gemini-flash-latest"
    conf = first["config"]
    assert conf.automatic_function_calling.disable is True
    assert "background worker" in conf.system_instruction
    names = [f.name for f in conf.tools[0].function_declarations]
    assert {"read_file", "write_file", "delete_file", "run_shell", "notify", "web_search"} <= set(names)
    # the model turn is replayed unchanged, thought signature included
    model_turn = second["contents"][1]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][1]["thought_signature"] == "c2lnLXJlYWRfZmlsZQ=="
    (part,) = results(second)
    assert part["function_response"] == {"name": "read_file", "response": {"result": "buy milk"}}


def test_overwrite_needs_approval_and_resumes(make_agent, cfg, workdir):
    (workdir / "a.txt").write_text("old")
    agent, client = make(make_agent, cfg, [
        gemini_reply(("write_file", {"path": "a.txt", "content": "new"}), ("list_dir", {"path": "."})),
        gemini_reply("Updated a.txt"),
    ])
    task = agent.submit("update a.txt")
    agent.wait(task["id"], 5, until=("needs_approval",))
    assert (workdir / "a.txt").read_text() == "old"
    (pending,) = agent.pending_approvals(task["id"])
    assert pending["tool"] == "write_file"
    agent.approve(pending["id"])
    assert agent.wait(task["id"], 5)["status"] == "completed"
    assert (workdir / "a.txt").read_text() == "new"
    parts = results(client.models.calls[1])
    assert [p["function_response"]["name"] for p in parts] == ["write_file", "list_dir"]


def test_denied_delete(make_agent, cfg, workdir):
    (workdir / "keep.txt").write_text("x")
    agent, client = make(make_agent, cfg, [
        gemini_reply(("delete_file", {"path": "keep.txt"})),
        gemini_reply("Left it alone"),
    ])
    task = agent.submit("delete keep.txt")
    agent.wait(task["id"], 5, until=("needs_approval",))
    agent.deny(agent.pending_approvals(task["id"])[0]["id"])
    assert agent.wait(task["id"], 5)["status"] == "completed"
    assert (workdir / "keep.txt").exists()
    (part,) = results(client.models.calls[1])
    assert part["function_response"]["response"] == {"error": DENIED}


def test_empty_reply_is_nudged(make_agent, cfg):
    agent, client = make(make_agent, cfg, [gemini_reply(), gemini_reply("done after nudge")])
    done = agent.wait(agent.submit("x")["id"], 5)
    assert done["result"] == "done after nudge"
    assert client.models.calls[1]["contents"][-1]["parts"][-1]["text"] == NUDGE


def test_safety_block_fails(make_agent, cfg):
    agent, _ = make(make_agent, cfg, [gemini_reply("partial", finish="SAFETY")])
    done = agent.wait(agent.submit("x")["id"], 5)
    assert done["status"] == "failed" and "SAFETY" in done["error"]


def test_rate_limit_is_retried(make_agent, cfg, monkeypatch):
    from google.genai import errors
    from orion_bg import config
    retrying = config.Config(config._merge(cfg, {"gemini": {"retries": 2}}))
    monkeypatch.setattr("orion_bg.runners.RunContext.sleep", lambda self, s: None)
    busy = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    client = FakeGenai([busy, gemini_reply("ok")])
    agent = make_agent(runners={"agent": GeminiAgentRunner(retrying, client=client)})
    agent.start()
    assert agent.wait(agent.submit("x")["id"], 5)["result"] == "ok"


def test_api_error_fails_cleanly(make_agent, cfg):
    from google.genai import errors
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}})
    agent, _ = make(make_agent, cfg, [bad])
    done = agent.wait(agent.submit("x")["id"], 5)
    assert done["status"] == "failed" and "Gemini API error 400" in done["error"]


def test_web_search_tool(make_agent, cfg):
    from google.genai import types
    grounded = types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text="It is sunny.")]), finish_reason="STOP",
        grounding_metadata=types.GroundingMetadata(grounding_chunks=[
            types.GroundingChunk(web=types.GroundingChunkWeb(uri="https://weather.example", title="Weather"))]))])
    agent, client = make(make_agent, cfg, [
        gemini_reply(("web_search", {"query": "weather"})),
        grounded,
        gemini_reply("Sunny today."),
    ])
    assert agent.wait(agent.submit("weather?")["id"], 5)["result"] == "Sunny today."
    search_call = client.models.calls[1]
    assert search_call["contents"] == "weather"
    assert search_call["config"].tools[0].google_search is not None
    (part,) = results(client.models.calls[2])
    out = part["function_response"]["response"]["result"]
    assert "It is sunny." in out and "https://weather.example" in out


def test_key_lookup(cfg, monkeypatch):
    assert gemini_api_key(cfg) is None
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
    assert gemini_api_key(cfg) == "g-key"
    monkeypatch.setenv("GEMINI_API_KEY", " gem-key ")
    assert gemini_api_key(cfg) == "gem-key"


def test_no_key_fails_cleanly(make_agent):
    agent = make_agent()
    agent.start()
    done = agent.wait(agent.submit("x")["id"], 5)
    assert done["status"] == "failed" and "GEMINI_API_KEY" in done["error"]


def quota_error():
    from google.genai import errors
    return errors.ClientError(429, {"error": {"code": 429, "message": "You exceeded your current quota", "status": "RESOURCE_EXHAUSTED"}})


def test_quota_error_falls_back_to_next_model(make_agent, cfg):
    runner_cfg = cfg  # fallback_models defaults to gemini-flash-lite-latest
    client = FakeGenai([quota_error(), gemini_reply(("list_dir", {"path": "."})), gemini_reply("done on lite")])
    runner = GeminiAgentRunner(runner_cfg, client=client)
    agent = make_agent(runners={"agent": runner})
    agent.start()
    assert agent.wait(agent.submit("x")["id"], 5)["result"] == "done on lite"
    models = [c["model"] for c in client.models.calls]
    # the exhausted model is skipped for the rest of the task
    assert models == ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-flash-lite-latest"]


def test_quota_error_on_last_model_fails(make_agent, cfg):
    from orion_bg import config
    single = config.Config(config._merge(cfg, {"gemini": {"fallback_models": []}}))
    agent = make_agent(runners={"agent": GeminiAgentRunner(single, client=FakeGenai([quota_error()]))})
    agent.start()
    done = agent.wait(agent.submit("x")["id"], 5)
    assert done["status"] == "failed" and "429" in done["error"]
