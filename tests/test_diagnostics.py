from orion.diagnostics import run_all


async def test_system_check_reports_every_component(engine, monkeypatch):
    from orion.tools import web

    monkeypatch.setattr(web, "web_search", lambda q, n=6: [{"title": "x"}])

    async def no_net(*a, **k):
        raise RuntimeError("offline in tests")

    monkeypatch.setattr(engine.brain, "discover", no_net)
    checks = await run_all(engine, include_generation=False)
    ids = [c["id"] for c in checks]
    assert {"key", "data", "live", "mic", "spk", "tts", "search", "apps", "everything", "mcp", "wake"} <= set(ids)
    by = {c["id"]: c for c in checks}
    assert by["data"]["ok"] is True and by["search"]["ok"] is True
    assert by["key"]["ok"] is False and by["key"]["fix"]
    for c in checks:
        assert c["ok"] in (True, False, None) and isinstance(c["detail"], str)
