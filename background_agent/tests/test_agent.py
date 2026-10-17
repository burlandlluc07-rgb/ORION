"""Claude provider (optional). The Gemini provider is in test_gemini.py."""
from conftest import FakeClient, text, tool_use

from orion_bg.agent_loop import DENIED
from orion_bg.claude_agent import FALLBACK_BETA, ClaudeAgentRunner


def make(make_agent, cfg, script, **over):
    client = FakeClient(script)
    runner = ClaudeAgentRunner(cfg, client=client)
    agent = make_agent(runners={"agent": runner}, **over)
    agent.start()
    return agent, client


def last_tool_results(call):
    return call["messages"][-1]["content"]


def test_reads_file_and_answers(make_agent, cfg, workdir):
    (workdir / "notes.txt").write_text("buy milk")
    agent, client = make(make_agent, cfg, [
        ([text("Reading."), tool_use("t1", "read_file", path="notes.txt")], "tool_use"),
        ([text("Your note says: buy milk")], "end_turn"),
    ])
    task = agent.submit("What is in notes.txt?")
    done = agent.wait(task["id"], 5)
    assert done["status"] == "completed", done["error"]
    assert done["result"] == "Your note says: buy milk"
    first, second = client.messages.calls
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"}
    assert first["model"] == "claude-opus-5-5"
    assert first["betas"] == [FALLBACK_BETA] and first["fallbacks"] == "default"
    names = [t["name"] for t in first["tools"]]
    assert {"read_file", "write_file", "delete_file", "run_shell", "notify", "web_search"} <= set(names)
    (result,) = last_tool_results(second)
    assert result["tool_use_id"] == "t1" and result["content"] == "buy milk" and not result["is_error"]


def test_new_file_needs_no_approval(make_agent, cfg, workdir):
    agent, _ = make(make_agent, cfg, [
        ([tool_use("t1", "write_file", path="out/report.md", content="# hi")], "tool_use"),
        ([text("Wrote it")], "end_turn"),
    ])
    done = agent.wait(agent.submit("write a report")["id"], 5)
    assert done["status"] == "completed"
    assert (workdir / "out" / "report.md").read_text() == "# hi"


def test_overwrite_waits_for_approval_then_runs(make_agent, cfg, workdir):
    (workdir / "a.txt").write_text("old")
    agent, client = make(make_agent, cfg, [
        ([tool_use("t1", "write_file", path="a.txt", content="new"), tool_use("t2", "list_dir", path=".")], "tool_use"),
        ([text("Updated a.txt")], "end_turn"),
    ])
    task = agent.submit("update a.txt")
    parked = agent.wait(task["id"], 5, until=("needs_approval",))
    assert parked["status"] == "needs_approval"
    assert (workdir / "a.txt").read_text() == "old"  # nothing happened yet
    (pending,) = agent.pending_approvals(task["id"])
    assert pending["tool"] == "write_file" and "Overwrites" in pending["reason"]
    agent.approve(pending["id"])
    done = agent.wait(task["id"], 5)
    assert done["status"] == "completed"
    assert (workdir / "a.txt").read_text() == "new"
    results = last_tool_results(client.messages.calls[1])
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]  # order kept, one message


def test_denied_delete_leaves_file(make_agent, cfg, workdir):
    (workdir / "keep.txt").write_text("x")
    agent, client = make(make_agent, cfg, [
        ([tool_use("t1", "delete_file", path="keep.txt")], "tool_use"),
        ([text("OK, not deleted")], "end_turn"),
    ])
    task = agent.submit("delete keep.txt")
    agent.wait(task["id"], 5, until=("needs_approval",))
    agent.deny(agent.pending_approvals(task["id"])[0]["id"])
    assert agent.wait(task["id"], 5)["status"] == "completed"
    assert (workdir / "keep.txt").exists()
    (result,) = last_tool_results(client.messages.calls[1])
    assert result["is_error"] and result["content"] == DENIED


def test_outside_allowlist_refused_without_asking(make_agent, cfg, tmp_path):
    (tmp_path / "secret.txt").write_text("s")
    agent, client = make(make_agent, cfg, [
        ([tool_use("t1", "read_file", path=str(tmp_path / "secret.txt"))], "tool_use"),
        ([text("Not allowed")], "end_turn"),
    ])
    done = agent.wait(agent.submit("read secret")["id"], 5)
    assert done["status"] == "completed"
    assert not agent.store.approvals(None)
    (result,) = last_tool_results(client.messages.calls[1])
    assert result["is_error"] and "outside the allowed folders" in result["content"]


def test_safe_shell_runs_unknown_shell_asks(make_agent, cfg, workdir):
    agent, client = make(make_agent, cfg, [
        ([tool_use("t1", "run_shell", command="echo hello-shell")], "tool_use"),
        ([tool_use("t2", "run_shell", command="python -c \"print(1)\"")], "tool_use"),
        ([text("done")], "end_turn"),
    ])
    task = agent.submit("shell things")
    agent.wait(task["id"], 5, until=("needs_approval",))
    (r1,) = last_tool_results(client.messages.calls[1])
    assert "hello-shell" in r1["content"] and "exit code 0" in r1["content"]
    (pending,) = agent.pending_approvals(task["id"])
    assert pending["input"]["command"].startswith("python")
    agent.approve(pending["id"])
    assert agent.wait(task["id"], 10)["status"] == "completed"
    (r2,) = last_tool_results(client.messages.calls[2])
    assert "1" in r2["content"]


def test_pause_turn_is_resent(make_agent, cfg):
    agent, client = make(make_agent, cfg, [
        ([{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "x"}}], "pause_turn"),
        ([text("found it")], "end_turn"),
    ])
    done = agent.wait(agent.submit("search")["id"], 5)
    assert done["result"] == "found it"
    assert client.messages.calls[1]["messages"][-1]["role"] == "assistant"


def test_refusal_and_step_limit(make_agent, cfg):
    agent, _ = make(make_agent, cfg, [([], "refusal")])
    assert "declined" in agent.wait(agent.submit("x")["id"], 5)["error"]


def test_step_limit(make_agent, cfg):
    from orion_bg import config
    limited = config.Config(config._merge(cfg, {"tasks": {"max_steps": 3}}))
    loop = [([tool_use(f"t{i}", "list_dir", path=".")], "tool_use") for i in range(3)]
    agent, _ = make(make_agent, limited, loop)
    done = agent.wait(agent.submit("loop")["id"], 5)
    assert done["status"] == "failed" and "3 steps" in done["error"]


def test_fallbacks_off_uses_plain_messages(make_agent, cfg):
    from orion_bg import config
    plain = config.Config(config._merge(cfg, {"claude": {"fallbacks": False}, "agent": {"web_search": False}}))
    client = FakeClient([([text("hi")], "end_turn")])
    agent = make_agent(runners={"agent": ClaudeAgentRunner(plain, client=client)})
    agent.start()
    assert agent.wait(agent.submit("x")["id"], 5)["status"] == "completed"
    (call,) = client.messages.calls
    assert "betas" not in call and "fallbacks" not in call
    assert "web_search" not in [t["name"] for t in call["tools"]]


def test_claude_without_key_fails_cleanly(make_agent, cfg):
    import pytest
    pytest.importorskip("anthropic")
    agent = make_agent(runners={"agent": ClaudeAgentRunner(cfg)})
    agent.start()
    done = agent.wait(agent.submit("x")["id"], 5)
    assert done["status"] == "failed" and "ANTHROPIC_API_KEY" in done["error"]
