import time

from fastapi.testclient import TestClient

from orion.server.api import create_app


def make(engine):
    app = create_app(engine, token="secret-token")
    return TestClient(app), {"x-orion-token": "secret-token"}


async def test_auth_required(engine):
    client, _ = make(engine)
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"x-orion-token": "wrong"}).status_code == 401


async def test_state_tools_skills(engine):
    client, h = make(engine)
    st = client.get("/api/state", headers=h).json()
    assert st["has_key"] is True and st["settings"]["fast_path"] is True
    tools = client.get("/api/tools", headers=h).json()
    assert any(t["name"] == "open_app" for t in tools)
    skills = client.get("/api/skills", headers=h).json()
    assert {"morning-briefing", "deep-research", "pc-doctor"} <= {s["name"] for s in skills}


async def test_user_skill_crud(engine):
    client, h = make(engine)
    r = client.post("/api/skills", headers=h, json={"name": "weekly-report", "description": "Make my weekly report",
                                                     "body": "1. Read notes\n2. Summarise"})
    assert r.status_code == 200 and r.json()["builtin"] is False
    assert client.get("/api/skills/weekly-report", headers=h).json()["body"].startswith("1. Read")
    assert client.post("/api/skills", headers=h, json={"name": "Bad Name!", "description": "x"}).status_code == 400
    assert client.delete("/api/skills/weekly-report", headers=h).status_code == 200
    assert client.delete("/api/skills/pc-doctor", headers=h).status_code == 400  # built-ins can't be deleted


async def test_routines_and_memories(engine):
    client, h = make(engine)
    r = client.post("/api/routines", headers=h, json={"name": "Brief", "prompt": "/morning-briefing", "schedule": "weekdays 08:00"})
    assert r.status_code == 200 and r.json()["next_run"] > time.time()
    assert client.post("/api/routines", headers=h, json={"name": "x", "prompt": "y", "schedule": "sometimes"}).status_code == 400
    client.post("/api/memories", headers=h, json={"text": "Likes tea", "profile": True})
    assert client.get("/api/memories", headers=h).json()[0]["kind"] == "profile"


async def test_settings_patch_validates(engine):
    client, h = make(engine)
    r = client.patch("/api/settings", headers=h, json={"user_name": "Lluc", "voice": {"mode": "classic"}})
    assert r.status_code == 200
    assert engine.config.settings.user_name == "Lluc" and engine.config.settings.voice.mode == "classic"
    assert engine.config.path.exists()
    assert client.patch("/api/settings", headers=h, json={"history_turns": "lots"}).status_code == 400


async def test_ws_rejects_bad_token_and_origin(engine):
    client, _ = make(engine)
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=nope") as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=secret-token", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
