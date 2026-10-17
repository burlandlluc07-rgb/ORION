"""Day 6: integration readiness is reported honestly and read-only connectors can't change anything."""

from types import SimpleNamespace

from orion.ecosystem.connectors import Connection
from orion.ecosystem.integrations import autocad_installs, status
from orion.tools.registry import Registry


def fake_tool(name, read_only=False, destructive=False):
    return SimpleNamespace(name=name, description=name, inputSchema={"type": "object", "properties": {}},
                           annotations=SimpleNamespace(readOnlyHint=read_only, destructiveHint=destructive))


def test_read_only_connector_never_offers_write_tools():
    reg = Registry()
    conn = Connection("google", {"read_only": True})
    for t in (fake_tool("list_events", read_only=True), fake_tool("create_event"), fake_tool("delete_file",
                                                                                            destructive=True)):
        conn._register(reg, t)
    assert conn.tools == ["google__list_events"] and reg.get("google__list_events").risk == "safe"
    assert reg.get("google__create_event") is None and reg.get("google__delete_file") is None
    assert conn.blocked == ["google__create_event", "google__delete_file"]
    assert conn.info()["read_only"] is True and conn.info()["blocked"] == conn.blocked


def test_ordinary_connector_still_registers_everything():
    reg = Registry()
    conn = Connection("notion", {})
    conn._register(reg, fake_tool("update_page"))
    assert reg.get("notion__update_page").risk == "confirm" and conn.blocked == []


def test_autocad_core_console_is_detected_without_starting_it(tmp_path, monkeypatch):
    for ver in ("2023", "2025"):
        d = tmp_path / "Autodesk" / f"AutoCAD {ver}"
        d.mkdir(parents=True)
        (d / "accoreconsole.exe").write_bytes(b"MZ")
    (tmp_path / "Autodesk" / "AutoCAD LT 2024").mkdir()  # LT has no Core Console
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramW6432", raising=False)
    found = autocad_installs()
    assert [f["version"] for f in found] == ["2025", "2023"]


async def test_status_reports_every_integration_and_what_is_blocked(engine, monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramW6432", raising=False)
    by_id = {s["id"]: s for s in status(engine)}
    assert by_id["google"]["state"] == "not_set_up" and by_id["google"]["next"]
    assert by_id["whatsapp"]["state"] == "partial" and "whatsapp_draft" in by_id["whatsapp"]["can"][0]
    assert by_id["autocad"]["state"] == "not_found"
    task = engine.tracker.start("t", None)
    ok, res = await engine.agent.run_tool("integration_status", {}, task, None)
    assert ok and "whatsapp" in res.lower() and task.steps[0]["lifecycle"]["stage"] == "completed"
    assert engine.approvals.list() == []  # a status check never asks: it changes nothing


async def test_google_read_only_shows_what_works(engine, monkeypatch):
    monkeypatch.setattr(engine.accounts, "public", lambda: {
        "email": {"address": "me@gmail.com", "imap_host": "imap.gmail.com"},
        "calendars": [{"name": "Main", "host": "calendar.google.com"}]})
    monkeypatch.setattr(engine.connectors, "list", lambda: [
        {"name": "google-workspace", "read_only": True, "tools": ["a", "b"], "blocked": ["c"]}])
    g = next(s for s in status(engine) if s["id"] == "google")
    assert g["state"] == "ready" and len(g["can"]) == 3
    assert any("1 write tool(s) blocked" in c for c in g["can"])
