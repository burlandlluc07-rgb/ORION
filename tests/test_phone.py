"""Phone app server: pairing, per-device auth, throttling, restrictions, control, voice, push."""

import base64
import socket

from fastapi.testclient import TestClient

from orion.server.api import create_app


def phone(engine):
    return TestClient(create_app(engine, None, remote=True))


def paired(engine, name="Pixel"):
    client = phone(engine)
    code = engine.remote.devices.new_pairing_code()
    r = client.post("/api/pair", json={"code": code, "name": name})
    assert r.status_code == 200
    return client, {"x-orion-token": r.json()["token"]}


async def test_pairing_is_one_time_and_tokens_are_per_device(engine):
    client = phone(engine)
    code = engine.remote.devices.new_pairing_code()
    tok = client.post("/api/pair", json={"code": code, "name": "Pixel"}).json()["token"]
    assert client.post("/api/pair", json={"code": code, "name": "Again"}).status_code == 400  # single use
    assert client.get("/api/state", headers={"x-orion-token": tok}).status_code == 200
    assert client.get("/api/state").status_code == 401
    devs = engine.remote.devices.list()
    assert [d["name"] for d in devs] == ["Pixel"] and "token_hash" not in devs[0]
    engine.remote.devices.revoke(devs[0]["id"])
    assert client.get("/api/state", headers={"x-orion-token": tok}).status_code == 401


async def test_brute_force_is_blocked(engine):
    client = phone(engine)
    for _ in range(12):
        client.get("/api/state", headers={"x-orion-token": "guess"})
    assert client.get("/api/state", headers={"x-orion-token": "guess"}).status_code == 429


async def test_phone_cannot_change_settings_but_can_chat(engine):
    client, h = paired(engine)
    assert client.post("/api/chat", headers=h, json={"text": "what time is it"}).status_code == 200
    for method, path, body in [("patch", "/api/settings", {"user_name": "x"}), ("post", "/api/key", {"key": "k" * 30}),
                               ("put", "/api/connectors/x", {}), ("post", "/api/plugins/reload", {}),
                               ("get", "/api/remote", None), ("delete", "/api/devices/abc", None),
                               ("post", "/api/window/show", {}), ("put", "/api/accounts/email", {})]:
        r = getattr(client, method)(path, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path


async def test_direct_control_runs_whitelisted_tools_only(engine):
    client, h = paired(engine)
    r = client.post("/api/control/system_status", headers=h, json={})
    assert r.json()["ok"] and "cpu" in r.json()["result"]
    assert client.post("/api/control/run_command", headers=h, json={"command": "whoami"}).status_code == 403
    assert client.post("/api/control/delete_path", headers=h, json={"path": "x"}).status_code == 403
    assert any(t["title"].startswith("Phone:") for t in engine.store.list_tasks())  # audit trail


async def test_phone_voice_transcribes_then_handles(engine, monkeypatch):
    client, h = paired(engine)

    async def fake_transcribe(wav):
        return "what time is it"

    monkeypatch.setattr(engine.brain, "transcribe", fake_transcribe)
    wav = b"RIFF" + b"\x00" * 4000
    r = client.post("/api/phone/voice", headers=h, json={"wav_b64": base64.b64encode(wav).decode()})
    assert r.status_code == 200 and r.json()["text"] == "what time is it" and r.json()["conv_id"]


async def test_screen_endpoint(engine, monkeypatch):
    from orion.tools import screen

    monkeypatch.setattr(screen, "capture", lambda monitor=1, max_side=1600: (b"\xff\xd8jpeg", (800, 600)))
    client, h = paired(engine)
    r = client.get("/api/screen", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["x-screen-size"] == "800x600"


async def test_push_subscription_and_send(engine, monkeypatch):
    client, h = paired(engine)
    key = client.get("/api/push/key").json()["key"]
    assert len(base64.urlsafe_b64decode(key + "==")) == 65  # uncompressed P-256 point
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.post("/api/push/subscribe", headers=h, json={"subscription": sub}).status_code == 200
    sent = []
    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", lambda sub, payload, **kw: sent.append((sub["endpoint"], payload)))
    assert engine.remote.push.send("Hi", "Body", {"x": 1}) == 1
    assert sent[0][0] == "https://push.example/abc" and '"title": "Hi"' in sent[0][1]


async def test_phone_server_lifecycle_and_qr(engine):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        engine.config.settings.remote_port = s.getsockname()[1]
    await engine.remote.start()
    try:
        st = engine.remote.status(with_qr=True)
        assert st["running"] and "/#/pair/" in st["pair_url"] and st["qr_svg"].startswith("<svg")
        assert st["access"] == "lan" and st["secure"] is False
    finally:
        await engine.remote.stop()
    assert not engine.remote.running


async def test_tailscale_missing_is_explained(engine, monkeypatch):
    from orion.server import tunnel

    monkeypatch.setattr(tunnel, "tailscale_cli", lambda: None)
    await engine.remote.tunnel.start("tailscale", 8790)
    st = engine.remote.tunnel.status()
    assert st["state"] == "error" and "tailscale.com" in st["detail"]
