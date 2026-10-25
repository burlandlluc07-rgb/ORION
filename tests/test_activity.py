"""The "What ORION did" log: entries built from task steps and background tasks, filters, and the API."""

import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from orion.server.api import create_app
from orion.tasks import activity
from orion.tools.registry import Tool, ToolError, schema_for

from .test_background_service import bg, finished, work  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _gates_on(prompts_on):
    """Approvals are asked, so the log can show who approved."""


def add_tool(engine, fn, risk="safe"):
    schema, ctx_param, desc = schema_for(fn)
    engine.registry.add(Tool(name=fn.__name__, description=desc, parameters=schema, fn=fn, risk=risk,
                             wants_context=ctx_param))


def look(x: str) -> str:
    """Look.

    Args:
        x: value
    """
    return f"looked at {x}"


def change(x: str) -> str:
    """Change.

    Args:
        x: value
    """
    return f"changed {x}"


def broken(x: str) -> str:
    """Broken.

    Args:
        x: value
    """
    raise ToolError("disk is full")


async def approve_next(engine, decision):
    events = engine.bus.subscribe()

    async def run():
        while True:
            ev = await events.get()
            if ev["type"] == "approval.request":
                engine.approvals.respond(ev["approval"]["id"], decision)
                return
    return asyncio.create_task(run())


def headers():
    return {"x-orion-token": "secret-token"}


async def test_every_step_is_listed_newest_first_with_route_approval_and_evidence(engine):
    for fn, risk in ((look, "safe"), (change, "confirm"), (broken, "safe")):
        add_tool(engine, fn, risk)
    task = engine.tracker.start("Tidy my files", "conv1")
    await engine.agent.run_tool("look", {"x": "a"}, task, "conv1")
    waiter = await approve_next(engine, "approve")
    await engine.agent.run_tool("change", {"x": "b"}, task, "conv1")
    await waiter
    await engine.agent.run_tool("broken", {"x": "c"}, task, "conv1")
    engine.tracker.finish(task)

    client = TestClient(create_app(engine, token="secret-token"))
    r = client.get("/api/activity", headers=headers()).json()
    tools = [e["tool"] for e in r["entries"]]
    assert tools == ["broken", "change", "look"]  # newest first
    by = {e["tool"]: e for e in r["entries"]}
    assert by["look"]["outcome"] == "done" and by["look"]["approved_by"] == "Allowed automatically (settings)"
    assert by["look"]["evidence"] == ["read-only: the result itself is the answer"]
    assert by["change"]["approved_by"] == "You approved" and by["change"]["outcome"] == "unverified"
    assert by["broken"]["outcome"] == "failed" and "disk is full" in by["broken"]["error"]
    assert all(e["task"] == "Tidy my files" and e["conv_id"] == "conv1" and e["route"] for e in r["entries"])
    assert r["counts"]["problem"] == 1 and r["counts"]["ok"] == 2

    assert [e["tool"] for e in client.get("/api/activity?outcome=problem", headers=headers()).json()["entries"]] == ["broken"]
    assert [e["tool"] for e in client.get("/api/activity?q=change", headers=headers()).json()["entries"]] == ["change"]
    page = client.get("/api/activity?limit=1", headers=headers()).json()
    assert len(page["entries"]) == 1 and page["more"] is True
    older = client.get(f"/api/activity?limit=5&after={page['entries'][0]['id']}", headers=headers()).json()
    assert [e["tool"] for e in older["entries"]] == ["change", "look"]


async def test_declined_steps_and_live_tasks_show_up(engine):
    add_tool(engine, change, "confirm")
    task = engine.tracker.start("Change it", None)
    waiter = await approve_next(engine, "deny")
    await engine.agent.run_tool("change", {"x": "b"}, task, None)
    await waiter
    # still live: comes from the tracker, not the tasks table
    client = TestClient(create_app(engine, token="secret-token"))
    entries = client.get("/api/activity", headers=headers()).json()["entries"]
    assert entries[0]["outcome"] == "declined" and entries[0]["approved_by"] == "You declined"


def test_message_text_is_never_in_the_log():
    step = {"id": "s1", "tool": "whatsapp_send_personal", "risk": "danger", "status": "done", "started": 1.0,
            "label": "Send WhatsApp from your own number to Mum (+34 600): “the secret plan is on” "
                     "(ORION opens WhatsApp and presses Send)",
            "result": "Sent “the secret plan is on” to Mum"}
    entry = activity.from_step({"id": "t1", "title": "Text Mum", "created": 1.0}, step)
    assert "secret" not in str(entry) and "Mum" in entry["summary"]
    assert "(message not shown)" in entry["summary"]


def test_results_are_only_shown_for_failures():
    step = {"id": "s1", "tool": "email_read", "risk": "safe", "status": "done", "started": 1.0,
            "label": "Read email 42", "result": "Dear Lluc, your bank PIN is 1234"}
    entry = activity.from_step({"id": "t1", "title": "Read", "created": 1.0}, step)
    assert "PIN" not in str(entry) and entry["outcome"] == "done"


def test_old_records_without_a_lifecycle_still_list():
    step = {"id": "s1", "tool": "open_app", "label": "Open app: notepad", "risk": "safe", "status": "denied",
            "result": "Declined by user", "started": 5.0, "ended": 6.0}
    entry = activity.from_step({"id": "t", "title": "Old", "created": 1.0}, step)
    assert entry["outcome"] == "declined" and entry["route_label"] == "Not recorded"


async def test_phone_can_read_the_log(engine):
    client = TestClient(create_app(engine, None, remote=True))
    code = engine.remote.devices.new_pairing_code()
    token = client.post("/api/pair", json={"code": code, "name": "Pixel"}).json()["token"]
    r = client.get("/api/activity", headers={"x-orion-token": token})
    assert r.status_code == 200 and r.json()["entries"] == []


async def test_background_tasks_are_listed(engine, bg):  # noqa: F811
    svc = bg()
    t = svc.submit("count to three", kind="dummy")
    await finished(svc, t["id"])
    client = TestClient(create_app(engine, token="secret-token"))
    entries = client.get("/api/activity?route=background", headers=headers()).json()["entries"]
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "background" and e["summary"] == "count to three" and e["route_label"] == "Background agent"
    assert e["outcome"] in ("unverified", "failed")


def test_steps_with_the_same_start_time_keep_their_order():
    """Windows' clock can give consecutive steps the same start time: the later step still lists first."""
    steps = [{"id": str(i), "tool": t, "label": t, "started": 5.0, "status": "done"}
             for i, t in enumerate(["look", "change", "broken"])]
    assert [e["tool"] for e in activity.collect([{"id": "t", "title": "x", "steps": steps}])] == ["broken", "change", "look"]
    entries = activity.collect([{"id": "t", "title": "x", "steps": steps}])
    first = activity.filter_entries(entries, limit=1)
    rest = activity.filter_entries(entries, after=first["entries"][0]["id"])
    assert [e["tool"] for e in rest["entries"]] == ["change", "look"]  # paging never skips a tie


# ── Weekly recap ─────────────────────────────────────────────────────────────
WED_NOON = datetime(2026, 10, 21, 12, 0).timestamp()  # a Wednesday, local time
DAY = 86400


def entry(i, tool, outcome, at, approved_by="Allowed automatically (settings)", route="local", desktop="none",
          evidence=(), error="", task_id="t1"):
    return {"id": f"e{i}", "tool": tool, "outcome": outcome, "at": at, "approved_by": approved_by, "route": route,
            "desktop": desktop, "evidence": list(evidence), "error": error, "task_id": task_id, "summary": tool}


def test_week_starts_on_monday_local_time():
    start, end = activity.week_bounds(WED_NOON)
    assert datetime.fromtimestamp(start) == datetime(2026, 10, 19) and datetime.fromtimestamp(end) == datetime(2026, 10, 26)
    prev, _ = activity.week_bounds(WED_NOON, 1)
    assert datetime.fromtimestamp(prev) == datetime(2026, 10, 12)


def test_recap_counts_one_week_with_proof_and_failures():
    entries = [
        entry(1, "email_sort", "verified", WED_NOON - 1 * DAY, evidence=["12 emails labelled, checked in Gmail"]),
        entry(2, "email_sort", "failed", WED_NOON - 1 * DAY + 60, error="Gmail said 429: quota", task_id="t2"),
        entry(3, "open_app", "unverified", WED_NOON - 2 * DAY, approved_by="You approved", desktop="visible",
              route="app_automation"),
        entry(4, "whatsapp_send", "declined", WED_NOON - 2 * DAY + 5, approved_by="You declined", route="api_mcp"),
        entry(5, "calendar_events", "done", WED_NOON - 3600, approved_by="Your own command"),
        entry(6, "background_task", "attention", WED_NOON - 60, route="background",
              approved_by="You approved file_write; You declined shell", error="file is still empty", task_id="b1"),
        entry(7, "email_sort", "verified", WED_NOON - 8 * DAY),   # last week
        entry(8, "email_sort", "verified", WED_NOON - 15 * DAY),  # two weeks ago
    ]
    r = activity.recap(entries, WED_NOON)
    assert r["total"] == 6 and r["previous_total"] == 1 and r["has_older"] is True and r["tasks"] == 3
    assert r["worked"] == 3 and r["checked"] == 1 and r["read_only"] == 1 and r["not_checked"] == 1
    assert r["problems"] == 2 and r["declined"] == 1 and r["desktop"] == 1
    assert r["approvals"] == {"you_approved": 2, "you_declined": 2, "automatic": 2, "your_command": 1}
    assert [p["id"] for p in r["problem_entries"]] == ["e6", "e2"]  # newest first, with the error
    assert r["problem_entries"][1]["error"] == "Gmail said 429: quota"
    assert [p["id"] for p in r["proof"]] == ["e1"] and r["proof"][0]["evidence"]
    assert r["top"][0] == {"tool": "email_sort", "label": "Email sort", "count": 2, "problems": 1}
    assert {x["id"]: x["count"] for x in r["routes"]}["local"] == 3
    days = {d["date"]: d for d in r["days"]}
    assert len(r["days"]) == 7 and r["days"][0]["date"] == "2026-10-19"
    assert days["2026-10-20"]["count"] == 2 and days["2026-10-20"]["problems"] == 1
    assert days["2026-10-21"]["count"] == 2 and days["2026-10-22"]["future"] is True


def test_recap_of_an_earlier_week_and_an_empty_week():
    entries = [entry(1, "email_sort", "verified", WED_NOON - 8 * DAY, evidence=["ok"])]
    last = activity.recap(entries, WED_NOON, weeks_ago=1)
    assert last["total"] == 1 and last["weeks_ago"] == 1 and last["proof"][0]["id"] == "e1"
    assert not any(d["future"] for d in last["days"])
    empty = activity.recap([], WED_NOON)
    assert empty["total"] == 0 and empty["problem_entries"] == [] and empty["has_older"] is False


async def test_recap_api_reads_the_log_and_keeps_message_text_out(engine):
    add_tool(engine, look)
    add_tool(engine, broken)
    task = engine.tracker.start("Check things", "conv1")
    await engine.agent.run_tool("look", {"x": "a"}, task, "conv1")
    await engine.agent.run_tool("broken", {"x": "b"}, task, "conv1")
    engine.tracker.finish(task)
    engine.store.save_task({"id": "old", "title": "Text Mum", "conv_id": None, "status": "done", "created": 1.0,
                            "updated": 1.0, "plan": [], "steps": [
                                {"id": "s", "tool": "whatsapp_send", "label": "Send to Mum: “secret”", "risk": "confirm",
                                 "status": "done", "started": 1.0}]})

    client = TestClient(create_app(engine, token="secret-token"))
    r = client.get("/api/activity/recap", headers=headers()).json()
    assert r["total"] == 2 and r["problems"] == 1 and r["weeks_ago"] == 0
    assert r["problem_entries"][0]["tool"] == "broken" and "disk is full" in r["problem_entries"][0]["error"]
    assert "secret" not in str(r)  # the 1970 task is outside both weeks, and never read
    assert client.get("/api/activity/recap?weeks_ago=999", headers=headers()).json()["weeks_ago"] == 52
    assert client.get("/api/activity/recap").status_code == 401
