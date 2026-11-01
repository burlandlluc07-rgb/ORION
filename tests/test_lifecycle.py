"""Background-execution redesign: lifecycle records, the desktop permission gate, verification and staged cancel."""

import asyncio
import threading
import time

import pytest

from orion.tasks.lifecycle import TaskRecord
from orion.tasks.routing import capabilities_for
from orion.tools.registry import Tool, ToolContext, ToolError, schema_for


@pytest.fixture(autouse=True)
def _gates_on(prompts_on):
    """These tests exercise the permission gates, which are off by default since 2.6."""


def add_tool(engine, fn, name=None, risk="safe"):
    schema, ctx_param, desc = schema_for(fn)
    t = Tool(name=name or fn.__name__, description=desc, parameters=schema, fn=fn, risk=risk, wants_context=ctx_param)
    engine.registry.add(t)
    return t


def stages(step):
    return [h["stage"] for h in step["lifecycle"]["history"]]


def answer_approvals(engine, decision, seen):
    events = engine.bus.subscribe()  # subscribe now, before the step asks
    return asyncio.create_task(_answer(engine, events, decision, seen))


async def _answer(engine, events, decision, seen):
    while True:
        ev = await events.get()
        if ev["type"] == "approval.request":
            seen.append(ev["approval"])
            engine.approvals.respond(ev["approval"]["id"], decision)


def probe(x: str) -> str:
    """Probe.

    Args:
        x: value
    """
    return f"probed {x}"


async def test_every_step_gets_a_full_lifecycle_record(engine):
    add_tool(engine, probe)
    task = engine.tracker.start("t", None)
    ok, res = await engine.agent.run_tool("probe", {"x": "1"}, task, None)
    assert ok and res == "probed 1"
    step = task.steps[0]
    rec = step["lifecycle"]
    assert stages(step) == ["queued", "validating", "running", "verifying", "completed"]
    assert rec["executor"] == "tool" and rec["type"] == "probe" and rec["task_id"] == task.id
    assert rec["approval"]["granted"] and rec["approval"]["by"] == "policy"
    assert rec["verification"]["status"] == "not_applicable"
    assert rec["outputs"] == ["probed 1"] and rec["capabilities"]["desktop"] == "none"
    assert step["status"] == "done"
    engine.tracker.finish(task)
    saved = engine.store.list_tasks()[0]["steps"][0]  # the record is stored with the task history
    assert saved["lifecycle"]["stage"] == "completed"


async def test_validation_rejects_missing_arguments(engine):
    add_tool(engine, probe)
    task = engine.tracker.start("t", None)
    ok, res = await engine.agent.run_tool("probe", {}, task, None)
    assert not ok and "Bad arguments" in res
    assert task.steps[0]["lifecycle"]["stage"] == "failed" and task.steps[0]["status"] == "failed"


def test_illegal_lifecycle_steps_are_refused():
    r = TaskRecord(id="1", type="x", executor="tool")
    with pytest.raises(ValueError):
        r.advance("completed")  # can't finish without running and verifying
    r.advance("validating")
    r.advance("running")
    r.advance("verifying")
    r.advance("needs_attention")
    assert r.done


def test_router_classifies_desktop_use(engine):
    reg = engine.registry
    assert capabilities_for(reg.get("type_text")).desktop == "input"
    assert capabilities_for(reg.get("mouse_click")).route == "foreground"
    assert capabilities_for(reg.get("open_app")).desktop == "visible"
    assert capabilities_for(reg.get("window_action")).desktop == "input"
    assert capabilities_for(reg.get("write_file")).desktop == "none"
    assert capabilities_for(reg.get("create_document")).route == "local"
    assert capabilities_for(reg.get("click_control")).route == "app_automation"
    assert capabilities_for(reg.get("run_command")).route == "cli"
    assert capabilities_for(reg.get("browser_open")).route == "headless_browser"
    assert capabilities_for(reg.get("browser_open"), browser_visible=True).desktop == "visible"
    assert capabilities_for(reg.get("media_control")).desktop == "none"  # a shell message, not a key press


async def test_foreground_action_without_approval_is_refused(engine, no_side_effects):
    seen = []
    answerer = (answer_approvals(engine, "deny", seen))
    task = engine.tracker.start("t", None)
    ok, res = await asyncio.wait_for(engine.agent.run_tool("open_app", {"name": "notepad"}, task, None), 5)
    answerer.cancel()
    assert not ok and "declined" in res
    assert no_side_effects == []  # nothing was launched
    assert seen[0]["desktop"] is True and "window on your screen" in seen[0]["label"]
    step = task.steps[0]
    assert step["status"] == "denied" and step["lifecycle"]["stage"] == "cancelled"
    assert step["lifecycle"]["approval"]["granted"] is False


async def test_trust_mode_does_not_open_the_desktop_gate(engine, no_side_effects):
    engine.config.settings.approval_mode = "trust"
    seen = []
    answerer = (answer_approvals(engine, "approve", seen))
    task = engine.tracker.start("t", None)
    ok, _ = await asyncio.wait_for(engine.agent.run_tool("open_website", {"url": "example.com"}, task, None), 5)
    answerer.cancel()
    assert ok and len(seen) == 1 and seen[0]["desktop"]
    assert no_side_effects == ["https://example.com"]
    assert task.steps[0]["lifecycle"]["approval"]["scope"].endswith("for this task only")


async def test_desktop_approval_is_never_remembered_for_the_session(engine, no_side_effects):
    seen = []
    answerer = (answer_approvals(engine, "always", seen))
    for _ in range(2):
        task = engine.tracker.start("t", None)
        await asyncio.wait_for(engine.agent.run_tool("open_website", {"url": "example.com"}, task, None), 5)
    answerer.cancel()
    assert len(seen) == 2  # asked both times
    assert "open_website" not in engine.approvals.session_allow


async def test_desktop_setting_allow_and_the_users_own_commands_skip_the_prompt(engine, no_side_effects):
    task = engine.tracker.start("t", None)
    ok, _ = await asyncio.wait_for(engine.agent.run_tool("open_website", {"url": "a.com"}, task, None,
                                                         origin="command"), 5)
    assert ok and task.steps[0]["lifecycle"]["approval"]["by"] == "the user's own command"
    engine.config.settings.desktop_actions = "allow"
    ok, _ = await asyncio.wait_for(engine.agent.run_tool("open_website", {"url": "b.com"}, task, None), 5)
    assert ok and no_side_effects == ["https://a.com", "https://b.com"]


async def test_instant_command_runs_without_a_prompt(engine, no_side_effects):
    res = await asyncio.wait_for(engine.agent.handle("open example.com"), 5)
    assert res["model"] == "local" and no_side_effects == ["https://example.com"]
    assert engine.approvals.list() == []


async def test_tool_fallback_to_the_real_mouse_asks_first(engine):
    asked = []

    def stubborn(ctx: ToolContext = None) -> str:  # type: ignore[assignment]
        """A control that only takes a real click."""
        if ctx.request_desktop("needs a real click"):
            return "clicked"
        raise ToolError("not allowed")

    add_tool(engine, stubborn)
    answerer = (answer_approvals(engine, "deny", asked))
    task = engine.tracker.start("t", None)
    ok, res = await asyncio.wait_for(engine.agent.run_tool("stubborn", {}, task, None), 5)
    answerer.cancel()
    assert not ok and res == "not allowed" and asked and asked[0]["desktop"]
    assert "awaiting_permission" in stages(task.steps[0])
    assert ToolContext().request_desktop("x") is False  # outside the agent nothing may take the desktop


async def test_wrong_output_is_needs_attention_not_completed(engine, tmp_path):
    import docx

    def create_document(path: str, content: str, title: str = "", overwrite: bool = False) -> str:
        """Pretends to work but saves the wrong text.

        Args:
            path: p
            content: c
            title: t
            overwrite: o
        """
        p = tmp_path / "out.docx"
        d = docx.Document()
        d.add_paragraph("something else entirely")
        d.save(str(p))
        return f"Word document saved: {p} (1 KB)."

    add_tool(engine, create_document, risk="safe")
    task = engine.tracker.start("t", None)
    ok, res = await engine.agent.run_tool("create_document", {"path": "x.docx", "content": "Quarterly budget plan"},
                                          task, None)
    step = task.steps[0]
    assert step["lifecycle"]["stage"] == "needs_attention" and step["status"] == "attention"
    assert step["lifecycle"]["verification"]["status"] == "failed"
    assert "isn't right" in res and "quarterly budget plan" in res.lower()


async def test_real_document_is_reopened_and_checked(engine, tmp_path):
    engine.config.settings.approval_mode = "trust"
    path = str(tmp_path / "notes.docx")
    from orion.tools import files

    files.set_allowed_roots([str(tmp_path)])
    task = engine.tracker.start("t", None)
    ok, res = await engine.agent.run_tool(
        "create_document", {"path": path, "content": "1. First point here\n2. Second", "title": "Plan"}, task, None)
    assert ok, res
    ver = task.steps[0]["lifecycle"]["verification"]
    assert ver["status"] == "passed" and any("expected content" in e for e in ver["evidence"])


async def test_cancel_is_staged_and_recorded(engine):
    started = threading.Event()

    def slow(ctx: ToolContext = None) -> str:  # type: ignore[assignment]
        """Cooperative slow job."""
        started.set()
        for _ in range(200):
            ctx.check_cancelled()
            time.sleep(0.02)
        return "finished"

    add_tool(engine, slow)
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("slow", {}, task, None))
    await asyncio.to_thread(started.wait, 2)
    task.cancel.set()
    ok, res = await asyncio.wait_for(run, 5)
    step = task.steps[0]
    assert not ok and res == "Stopped by user."
    assert stages(step)[-2:] == ["cancel_requested", "cancelled"] and step["status"] == "stopped"
    assert any("cooperatively" in line for line in step["lifecycle"]["logs"])


async def test_cancel_forces_after_the_grace_period(engine, monkeypatch):
    import orion.brain.agent as agent_mod

    monkeypatch.setattr(agent_mod, "CANCEL_GRACE", 0.3)
    release = threading.Event()

    def stubborn_sleep() -> str:
        """Ignores cancellation."""
        release.wait(5)
        return "late"

    add_tool(engine, stubborn_sleep)
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("stubborn_sleep", {}, task, None))
    await asyncio.sleep(0.3)
    task.cancel.set()
    ok, _ = await asyncio.wait_for(run, 3)
    release.set()
    step = task.steps[0]
    assert not ok and step["lifecycle"]["stage"] == "cancelled"
    assert any("ORION-owned worker" in line for line in step["lifecycle"]["logs"])


async def test_timeout_checks_the_result_before_reporting(engine, monkeypatch, tmp_path):
    import orion.brain.agent as agent_mod
    from orion.tasks import verify

    monkeypatch.setattr(agent_mod, "TOOL_TIMEOUT", 0.5)
    target = tmp_path / "late.zip"
    release = threading.Event()

    def zip_files(paths: list[str], output: str) -> str:
        """Writes the zip, then hangs.

        Args:
            paths: p
            output: o
        """
        import zipfile

        with zipfile.ZipFile(target, "w") as z:
            z.writestr("a.txt", "hello")
        release.wait(5)
        return "never seen"

    add_tool(engine, zip_files)
    monkeypatch.setattr(verify, "path_from", lambda result, args, *keys: target)
    task = engine.tracker.start("t", None)
    ok, res = await asyncio.wait_for(engine.agent.run_tool("zip_files", {"paths": ["a"], "output": str(target)},
                                                           task, None), 5)
    release.set()
    assert ok and "checked" in res
    assert task.steps[0]["lifecycle"]["stage"] == "completed"
    assert "verifying" in stages(task.steps[0])


async def test_phone_buttons_are_recorded_as_the_users_own_action(engine):
    task = engine.tracker.start("Phone: system status", None, "phone")
    ok, _ = await engine.agent.run_tool("system_status", {}, task, None, origin="user")
    assert ok and task.steps[0]["lifecycle"]["origin"] == "user"


async def test_unanswered_desktop_request_expires_without_running(engine, no_side_effects, monkeypatch):
    monkeypatch.setattr(engine.approvals, "timeout", lambda: 0.6)  # Settings > Safety: requests lapse
    task = engine.tracker.start("t", None)
    ok, _ = await asyncio.wait_for(engine.agent.run_tool("open_app", {"name": "notepad"}, task, None), 5)
    assert not ok and no_side_effects == []  # nobody answered: nothing opened
    assert task.steps[0]["lifecycle"]["stage"] == "cancelled" and engine.approvals.list() == []


async def test_stop_while_waiting_for_permission_runs_nothing(engine, no_side_effects):
    task = engine.tracker.start("t", None)
    run = asyncio.create_task(engine.agent.run_tool("type_text", {"text": "x"}, task, None))
    for _ in range(40):
        if engine.approvals.list():
            break
        await asyncio.sleep(0.05)
    task.cancel.set()
    ok, _ = await asyncio.wait_for(run, 5)
    assert not ok and task.steps[0]["lifecycle"]["stage"] == "cancelled"
    assert "running" not in stages(task.steps[0])
