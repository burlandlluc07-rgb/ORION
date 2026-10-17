"""The background agent running inside ORION: engine wiring, approvals through ORION's broker, the
shared free-tier governor, the chat tools and the API."""

import asyncio
import time

import pytest
from google.genai import errors

from orion.background.service import BackgroundService
from orion.brain.governor import quota_day
from orion.tools.registry import ToolContext, ToolError

from .background.conftest import FakeGenai, gemini_reply


@pytest.fixture
def work(config):
    path = config.root / "work"
    path.mkdir()
    (config.root / "background-agent.toml").write_text(
        f'[tasks]\ndummy_seconds = 0.1\n\n[safety]\nallowed_paths = ["{path.as_posix()}"]\n', encoding="utf-8")
    return path


@pytest.fixture
async def bg(engine, work):
    engine._loop = asyncio.get_running_loop()
    started = []

    def start(client=None):
        engine.background.start(engine._loop, client=client)
        started.append(True)
        return engine.background

    yield start
    await asyncio.to_thread(engine.background.stop)


async def until(fn, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def finished(svc, tid):
    return await until(lambda: (t := svc.get(tid)) and t["status"] in ("completed", "failed", "cancelled") and t)


async def test_dummy_task_reports_through_orion(engine, bg):
    events = engine.bus.subscribe()
    notes = []
    engine.notifier = lambda title, msg: notes.append(title)
    svc = bg()
    task = svc.submit("hello", kind="dummy")
    assert "state" not in task
    done = await finished(svc, task["id"])
    assert done["status"] == "completed" and "hello" in done["result"]
    await until(lambda: notes)
    assert notes == ["ORION finished a task"]
    seen = []
    while not events.empty():
        ev = events.get_nowait()
        if ev["type"] == "background.task":
            seen.append(ev["event"])
    assert seen[0] == "submitted" and "completed" in seen
    assert engine.state()["background"]["enabled"] is True


async def test_approval_is_asked_through_orions_broker(engine, bg):
    svc = bg()
    task = svc.submit("tidy [approval]", kind="dummy")
    prompt = await until(lambda: engine.approvals.list())
    assert prompt[0]["tool"] == "background:dummy_action" and prompt[0]["desktop"] is False
    assert "tidy" in prompt[0]["label"]
    assert engine.approvals.respond(prompt[0]["id"], "approve")
    done = await finished(svc, task["id"])
    assert done["result"].startswith("Dummy action was approved")


async def test_deny_through_broker(engine, bg):
    svc = bg()
    task = svc.submit("x [approval]", kind="dummy")
    prompt = await until(lambda: engine.approvals.list())
    engine.approvals.respond(prompt[0]["id"], "deny")
    assert (await finished(svc, task["id"]))["result"].startswith("Dummy action was denied")


async def test_api_decision_closes_orions_prompt(engine, bg):
    svc = bg()
    task = svc.submit("y [approval]", kind="dummy")
    await until(lambda: engine.approvals.list())
    pending = svc.pending_approvals()
    assert len(pending) == 1
    assert svc.decide(pending[0]["id"], True)
    await until(lambda: not engine.approvals.list())
    assert (await finished(svc, task["id"]))["result"].startswith("Dummy action was approved")
    assert not svc.decide(pending[0]["id"], False)  # already decided


async def test_cancel_closes_prompt(engine, bg):
    svc = bg()
    task = svc.submit("z [approval]", kind="dummy")
    await until(lambda: engine.approvals.list())
    assert svc.cancel(task["id"])["status"] == "cancelled"
    await until(lambda: not engine.approvals.list())
    assert svc.pending_approvals() == []


async def test_pending_approval_survives_restart(engine, bg):
    svc = bg()
    task = svc.submit("later [approval]", kind="dummy")
    await until(lambda: engine.approvals.list())
    await asyncio.to_thread(svc.stop)  # ORION closes: the prompt goes, the approval stays
    await until(lambda: not engine.approvals.list())
    svc.start(engine._loop)
    assert svc.get(task["id"])["status"] == "needs_approval"
    prompt = await until(lambda: engine.approvals.list())
    engine.approvals.respond(prompt[0]["id"], "approve")
    assert (await finished(svc, task["id"]))["status"] == "completed"


async def test_engine_stop_keeps_approval_pending(engine, bg):
    svc = bg()
    task = svc.submit("keep [approval]", kind="dummy")
    await until(lambda: engine.approvals.list())
    await asyncio.to_thread(svc.stop)
    engine.approvals.deny_all()
    await asyncio.sleep(0.2)
    from orion.background.store import Store

    store = Store(engine.config.root / "background" / "tasks.db")
    assert store.get(task["id"])["status"] == "needs_approval"
    assert store.approvals("pending", task_id=task["id"])
    store.close()


async def test_requests_go_through_orions_governor(engine, bg, work):
    (work / "notes.txt").write_text("buy milk")
    client = FakeGenai([
        gemini_reply(("read_file", {"path": "notes.txt"})),
        gemini_reply("It says buy milk"),
    ])
    svc = bg(client)
    task = svc.submit("What is in notes.txt?")
    done = await finished(svc, task["id"])
    assert done["status"] == "completed", done["error"]
    smart = engine.config.settings.model_smart
    assert [c["model"] for c in client.models.calls] == [smart, smart]
    assert engine.governor.used_today(smart) == 2
    assert engine.store.usage_for(quota_day())[smart]["requests"] == 2


async def test_quota_error_cools_model_and_falls_back(engine, bg):
    client = FakeGenai([
        errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
        gemini_reply("done"),
    ])
    svc = bg(client)
    done = await finished(svc, svc.submit("hi")["id"])
    assert done["status"] == "completed", done["error"]
    s = engine.config.settings
    assert [c["model"] for c in client.models.calls] == [s.model_smart, s.model_fast]
    assert engine.governor._st(s.model_smart).cooldown_until > time.time()


async def test_exhausted_quota_fails_with_governors_message(engine, bg, monkeypatch):
    from orion.brain.governor import QuotaExhausted

    async def out(*a, **k):
        raise QuotaExhausted("Gemini's free daily quota is used up.")

    monkeypatch.setattr(engine.governor, "acquire", out)
    client = FakeGenai([])
    svc = bg(client)
    done = await finished(svc, svc.submit("hi")["id"])
    assert done["status"] == "failed" and "quota is used up" in done["error"]
    assert client.models.calls == []


async def test_no_key_fails_clearly(engine, bg, monkeypatch):
    monkeypatch.setattr(engine.config, "get_api_key", lambda: "")
    svc = bg()
    done = await finished(svc, svc.submit("hi")["id"])
    assert done["status"] == "failed" and "Gemini API key" in done["error"]


async def test_chat_tools(engine, bg):
    from orion.tools.productivity import cancel_background_task, list_background_tasks, queue_background_task

    ctx = ToolContext(engine=engine)
    with pytest.raises(ToolError):
        queue_background_task("x", ctx=ctx)  # off until started
    svc = bg()
    svc.pause()
    out = queue_background_task("write a summary", ctx=ctx)
    tid = out.split()[3].rstrip(".")
    assert list_background_tasks(ctx=ctx)[0]["id"] == tid
    assert "cancelled" in cancel_background_task(tid, ctx=ctx)
    with pytest.raises(ToolError):
        cancel_background_task("nope", ctx=ctx)
    names = {t.name for t in engine.registry.all()}
    assert {"queue_background_task", "list_background_tasks", "cancel_background_task"} <= names


def test_background_tools_never_touch_the_desktop():
    import orion.tools.productivity  # noqa: F401
    from orion.tasks.routing import capabilities_for
    from orion.tools.registry import decorated_tools

    tools = {t.name: t for t in decorated_tools()}
    for name in ("queue_background_task", "list_background_tasks", "cancel_background_task"):
        caps = capabilities_for(tools[name])
        assert caps.desktop == "none" and not caps.needs_desktop and caps.route == "local"


async def test_api(engine, bg):
    from fastapi.testclient import TestClient

    from orion.server.api import create_app

    c = TestClient(create_app(engine, token="t"), headers={"x-orion-token": "t"})
    assert c.post("/api/background/tasks", json={"prompt": "x"}).status_code == 409  # off
    svc = bg()
    t = c.post("/api/background/tasks", json={"prompt": "api [approval]", "kind": "dummy"}).json()
    assert c.post("/api/background/tasks", json={"prompt": " "}).status_code == 400
    assert c.post("/api/background/tasks", json={"prompt": "x", "kind": "nope"}).status_code == 400
    await until(lambda: svc.pending_approvals())
    state = c.get("/api/background").json()
    assert state["status"]["enabled"] and state["approvals"][0]["task_id"] == t["id"]
    aid = state["approvals"][0]["id"]
    assert c.post(f"/api/background/approvals/{aid}", json={"decision": "maybe"}).status_code == 400
    assert c.post(f"/api/background/approvals/{aid}", json={"decision": "approve"}).json() == {"ok": True}
    await finished(svc, t["id"])
    got = c.get(f"/api/background/tasks/{t['id']}").json()
    assert got["status"] == "completed" and got["approvals"][0]["status"] == "approved"
    assert c.get("/api/background/tasks/nope").status_code == 404
    assert c.post("/api/background/pause").json()["paused"] is True
    assert c.post("/api/background/resume").json()["paused"] is False


async def test_settings_toggle(engine, bg):
    engine._loop = asyncio.get_running_loop()
    engine.apply_settings({"background_agent": True})
    await until(lambda: engine.background.running)
    engine.apply_settings({"background_agent": False})
    await until(lambda: not engine.background.running)
    assert engine.state()["background"] == {"enabled": False}


def test_service_starts_off():
    svc = BackgroundService(engine=None)  # type: ignore[arg-type]
    assert not svc.running and svc.status() == {"enabled": False}
