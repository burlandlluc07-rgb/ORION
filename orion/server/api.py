"""Local API + WebSocket event stream between the engine and the UI.

Bound to 127.0.0.1 only and protected by a random per-launch token, so other
programs or websites on the machine can't drive ORION's tools.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import mimetypes
import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..brain.gemini import GeminiBrain
from ..ecosystem.connectors import CATALOG
from ..engine import Engine
from ..tasks.schedule import ScheduleError, describe, next_run
from ..tools.platform import open_target
from ..tools.registry import ToolError

log = logging.getLogger("orion.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
# What the phone's Control and Screen tabs may run directly (the user's own button presses).
REMOTE_CONTROLS = {
    "media_control", "set_volume", "change_volume", "mute", "set_brightness", "lock_pc", "power", "cancel_shutdown",
    "take_screenshot", "open_app", "close_app", "list_installed_apps", "list_windows", "window_action",
    "mouse_click", "scroll", "press_keys", "type_text", "system_status", "open_website", "play_youtube", "play_spotify",
    "home_control", "home_scene",
}
MAX_ATTACH = 15 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css", ".xml", ".yaml", ".yml", ".log",
            ".ini", ".toml", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".sql", ".sh", ".ps1", ".bat"}


def create_app(engine: Engine, token: str | None = None, shell: Any = None, remote: bool = False) -> FastAPI:
    """``remote=True`` builds the restricted phone API: chat, tasks and approvals, but no settings or ecosystem changes."""
    token = token or secrets.token_urlsafe(24)
    app = FastAPI(title="ORION", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.shell = shell
    bg: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        t = asyncio.create_task(coro)
        bg.add(t)
        t.add_done_callback(bg.discard)

    def check_token(supplied: str, ip: str) -> dict | None:
        """Desktop: the per-launch token. Phone server: a paired device's token (throttled per IP)."""
        if not remote:
            return {"id": "desktop"} if supplied and hmac.compare_digest(supplied, token) else None
        devices = engine.remote.devices
        if devices.blocked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
        dev = devices.authenticate(supplied)
        if dev is None:
            devices.record_failure(ip)
        return dev

    def auth(request: Request) -> None:
        supplied = request.headers.get("x-orion-token") or request.query_params.get("token") or ""
        ip = request.client.host if request.client else "?"
        dev = check_token(supplied, ip)
        if dev is None:
            raise HTTPException(401, "Not paired" if remote else "Bad token")
        request.state.device = dev

    def admin(request: Request) -> None:
        auth(request)
        if remote:
            raise HTTPException(403, "Not available from the phone remote.")

    A = Depends(auth)
    X = Depends(admin)  # desktop-only endpoints

    # ── State & settings ───────────────────────────────────────────────────
    @app.get("/api/state", dependencies=[A])
    async def state():
        return engine.state()

    @app.post("/api/key", dependencies=[X])
    async def set_key(body: dict = Body(...)):
        key = str(body.get("key", "")).strip()
        if len(key) < 20:
            raise HTTPException(400, "That doesn't look like a Gemini API key.")
        ok, msg, models = await GeminiBrain.validate_key(key)
        if not ok:
            return JSONResponse({"ok": False, "error": msg}, status_code=400)
        where = engine.config.set_api_key(key)
        engine.brain.reset()
        spawn(engine._discover())
        if engine.voice and engine.voice.state in ("error", "off"):
            spawn(engine.voice.restart())
        return {"ok": True, "stored_in": where, "models": len(models)}

    @app.patch("/api/settings", dependencies=[X])
    async def patch_settings(patch: dict = Body(...)):
        try:
            engine.apply_settings(patch)
            if "model_limits" in patch:  # a manual edit beats limits learned from Google's 429s
                learned = engine.store.kv_get("learned_limits", {}) or {}
                engine.store.kv_set("learned_limits", {m: v for m, v in learned.items() if m not in patch["model_limits"]})
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        if shell is not None and "launch_on_startup" in patch:
            shell.set_autostart(bool(patch["launch_on_startup"]))
        return engine.config.settings.model_dump()

    @app.get("/api/quota", dependencies=[A])
    async def quota():
        return {"models": engine.governor.snapshot(), "available": engine.store.kv_get("models", [])}

    # ── Conversations & chat ───────────────────────────────────────────────
    @app.get("/api/conversations", dependencies=[A])
    async def conversations():
        return engine.store.list_conversations()

    @app.get("/api/conversations/{cid}", dependencies=[A])
    async def conversation(cid: str):
        conv = engine.store.get_conversation(cid)
        if not conv:
            raise HTTPException(404)
        tasks = {t["id"]: t for t in engine.store.list_tasks(200) if t["conv_id"] == cid}
        tasks.update({t.id: t.to_dict() for t in engine.tracker.active.values() if t.conv_id == cid})
        return {"conversation": conv, "messages": engine.store.messages(cid), "tasks": list(tasks.values())}

    @app.patch("/api/conversations/{cid}", dependencies=[A])
    async def edit_conversation(cid: str, body: dict = Body(...)):
        if "title" in body:
            engine.store.rename_conversation(cid, str(body["title"]))
        if "pinned" in body:
            engine.store.pin_conversation(cid, bool(body["pinned"]))
        engine.bus.emit("conversations.changed")
        return {"ok": True}

    @app.delete("/api/conversations/{cid}", dependencies=[X])
    async def delete_conversation(cid: str):
        engine.store.delete_conversation(cid)
        engine.bus.emit("conversations.changed")
        return {"ok": True}

    @app.post("/api/chat", dependencies=[A])
    async def chat(body: dict = Body(...)):
        text = str(body.get("text", ""))
        cid = body.get("conv_id")
        attachments = []
        for a in body.get("attachments") or []:
            raw = base64.b64decode(a.get("b64", ""))
            if len(raw) > MAX_ATTACH:
                raise HTTPException(413, f"{a.get('name')} is larger than 15 MB.")
            name = str(a.get("name", "file"))
            mime = str(a.get("mime") or "application/octet-stream")
            if Path(name).suffix.lower() in TEXT_EXT or mime.startswith("text/"):
                attachments.append({"name": name, "text": raw.decode("utf-8", errors="replace")[:100_000]})
            else:
                attachments.append({"name": name, "mime": mime, "data": raw})
        if not cid or not engine.store.get_conversation(cid):
            from ..brain.agent import make_title

            conv = engine.store.create_conversation(make_title(text or "Attachment"))
            engine.bus.emit("conversation.created", conversation=conv)
            cid = conv["id"]
        spawn(engine.agent.handle(text, cid, mode=str(body.get("mode", "auto")), attachments=attachments,
                                  source=str(body.get("source", "text"))))
        return {"conv_id": cid}

    @app.post("/api/stop", dependencies=[A])
    async def stop():
        return {"stopped": engine.stop_everything()}

    @app.post("/api/approvals/{aid}", dependencies=[A])
    async def approve(aid: str, body: dict = Body(...)):
        decision = body.get("decision")
        if decision not in ("approve", "deny", "always"):
            raise HTTPException(400, "decision must be approve, deny or always")
        return {"ok": engine.approvals.respond(aid, decision)}

    @app.get("/api/missions", dependencies=[A])
    async def missions():
        return engine.missions.list()

    @app.post("/api/missions", dependencies=[A])
    async def start_mission(body: dict = Body(...)):
        goal = str(body.get("goal", "")).strip()
        if len(goal) < 8:
            raise HTTPException(400, "Describe the mission in a sentence or two.")
        return engine.missions.start(goal, body.get("title") or None)

    @app.post("/api/missions/{mid}/cancel", dependencies=[A])
    async def cancel_mission(mid: str):
        return {"ok": engine.missions.cancel(mid)}

    @app.get("/api/alerts", dependencies=[A])
    async def alerts():
        return list(reversed(engine.proactive.alerts))

    @app.get("/api/tasks", dependencies=[A])
    async def tasks():
        active = [t.to_dict() for t in engine.tracker.active.values()]
        return {"active": active, "history": engine.store.list_tasks(60)}

    # ── Routines ───────────────────────────────────────────────────────────
    def _routine_view(r: dict) -> dict:
        return {**r, "schedule_text": describe(r["schedule"]), "kind": "reminder" if r["prompt"].startswith("!notify:") else "routine",
                "prompt": r["prompt"].removeprefix("!notify:")}

    @app.get("/api/routines", dependencies=[A])
    async def routines():
        return [_routine_view(r) for r in engine.store.list_routines()]

    @app.post("/api/routines", dependencies=[A])
    async def create_routine(body: dict = Body(...)):
        try:
            next_run(body["schedule"])
            r = engine.scheduler.add(str(body["name"])[:80], str(body["prompt"]), str(body["schedule"]))
        except (KeyError, ScheduleError, ValueError) as exc:
            raise HTTPException(400, f"Invalid routine: {exc}") from exc
        return _routine_view(r)

    @app.patch("/api/routines/{rid}", dependencies=[A])
    async def edit_routine(rid: str, body: dict = Body(...)):
        try:
            r = engine.scheduler.update(rid, body)
        except (ScheduleError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if not r:
            raise HTTPException(404)
        return _routine_view(r)

    @app.delete("/api/routines/{rid}", dependencies=[A])
    async def delete_routine(rid: str):
        engine.scheduler.remove(rid)
        return {"ok": True}

    @app.post("/api/routines/{rid}/run", dependencies=[A])
    async def run_routine(rid: str):
        spawn(engine.scheduler.run_now(rid))
        return {"ok": True}

    # ── Memory ─────────────────────────────────────────────────────────────
    @app.get("/api/memories", dependencies=[A])
    async def memories():
        return engine.store.list_memories()

    @app.post("/api/memories", dependencies=[A])
    async def add_memory(body: dict = Body(...)):
        mid = engine.store.remember(str(body["text"]), "profile" if body.get("profile") else "fact")
        engine.bus.emit("memory.changed")
        return {"id": mid}

    @app.delete("/api/memories/{mid}", dependencies=[X])
    async def delete_memory(mid: int):
        engine.store.forget(mid)
        engine.bus.emit("memory.changed")
        return {"ok": True}

    # ── Ecosystem: skills, tools, plugins, connectors ──────────────────────
    @app.get("/api/skills", dependencies=[A])
    async def skills():
        return [s.to_dict() for s in engine.skills.skills.values()]

    @app.get("/api/skills/{name}", dependencies=[A])
    async def skill(name: str):
        s = engine.skills.skills.get(name)
        if not s:
            raise HTTPException(404)
        return s.to_dict(include_body=True)

    @app.post("/api/skills", dependencies=[X])
    async def save_skill(body: dict = Body(...)):
        try:
            s = engine.skills.save_user_skill(body["name"], body.get("description", ""), body.get("body", ""),
                                              body.get("tier", "fast"), body.get("icon", "sparkles"))
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        engine.bus.emit("ecosystem.changed")
        return s.to_dict(include_body=True)

    @app.patch("/api/skills/{name}", dependencies=[X])
    async def toggle_skill(name: str, body: dict = Body(...)):
        engine.set_skill_enabled(name, bool(body.get("enabled", True)))
        engine.bus.emit("ecosystem.changed")
        return {"ok": True}

    @app.delete("/api/skills/{name}", dependencies=[X])
    async def delete_skill(name: str):
        if not engine.skills.delete_user_skill(name):
            raise HTTPException(400, "Built-in skills can be disabled but not deleted.")
        engine.bus.emit("ecosystem.changed")
        return {"ok": True}

    @app.get("/api/tools", dependencies=[A])
    async def tools():
        return [{"name": t.name, "description": t.description, "risk": t.risk, "category": t.category,
                 "source": t.source, "enabled": not engine.registry.is_disabled(t.name)}
                for t in engine.registry.all(include_disabled=True)]

    @app.post("/api/tools/{name}/run", dependencies=[X])
    async def run_tool_direct(name: str, body: dict = Body(default={})):
        """Run one tool directly (PC app only). Same path as the assistant uses: risky tools still ask for approval."""
        if engine.registry.get(name) is None:
            raise HTTPException(404, "Unknown or disabled tool.")
        task = engine.tracker.start(f"Run {name.replace('_', ' ')}", None, "app")
        try:
            ok, result = await asyncio.wait_for(engine.agent.run_tool(name, body.get("args") or {}, task, None),
                                                float(body.get("timeout", 300)))
        except asyncio.TimeoutError:
            task.cancel.set()
            ok, result = False, "Timed out."
        finally:
            if task.id in engine.tracker.active:
                engine.tracker.finish(task, "done")
        return {"ok": ok, "result": result}

    @app.patch("/api/tools/{name}", dependencies=[X])
    async def toggle_tool(name: str, body: dict = Body(...)):
        engine.set_tool_enabled(name, bool(body.get("enabled", True)))
        return {"ok": True}

    @app.get("/api/plugins", dependencies=[X])
    async def plugins():
        return {"folder": str(engine.plugins.folder), "plugins": engine.plugins.list()}

    @app.post("/api/plugins/reload", dependencies=[X])
    async def reload_plugins():
        engine.plugins.load_all()
        engine.bus.emit("ecosystem.changed")
        return engine.plugins.list()

    @app.get("/api/connectors", dependencies=[X])
    async def connectors():
        return {"connectors": engine.connectors.list(), "catalog": CATALOG}

    @app.put("/api/connectors/{name}", dependencies=[X])
    async def put_connector(name: str, body: dict = Body(...)):
        try:
            await engine.connectors.upsert(name, body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/connectors/{name}", dependencies=[X])
    async def delete_connector(name: str):
        await engine.connectors.remove(name)
        return {"ok": True}

    # ── Voice ──────────────────────────────────────────────────────────────
    @app.get("/api/voice", dependencies=[A])
    async def voice_status():
        from ..voice.audio import list_devices
        from ..voice.tts import VOICES

        return {"status": engine.voice.status() if engine.voice else {"state": "unavailable"},
                "devices": await asyncio.to_thread(list_devices), "tts_voices": VOICES,
                "live_voices": ["Charon", "Puck", "Kore", "Fenrir", "Aoede", "Orus", "Zephyr", "Leda"]}

    @app.post("/api/voice/{action}", dependencies=[X])
    async def voice_action(action: str):
        v = engine.voice
        if v is None:
            raise HTTPException(409, "Voice is unavailable on this machine (no audio device or PortAudio).")
        if action == "toggle":
            await v.toggle()
        elif action == "start":
            await v.activate()
        elif action == "stop":
            await v.deactivate()
        elif action == "mute":
            v.set_muted(not v.muted)
        elif action == "interrupt":
            v.interrupt()
        else:
            raise HTTPException(404)
        return v.status()

    # ── Accounts (email / calendar) ────────────────────────────────────────
    @app.get("/api/accounts", dependencies=[X])
    async def accounts():
        return engine.accounts.public()

    @app.put("/api/accounts/email", dependencies=[X])
    async def put_email(body: dict = Body(...)):
        try:
            return await asyncio.to_thread(engine.accounts.configure_email, body)
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/accounts/email", dependencies=[X])
    async def delete_email():
        engine.accounts.remove_email()
        return engine.accounts.public()

    @app.put("/api/accounts/calendars", dependencies=[X])
    async def put_calendars(body: dict = Body(...)):
        try:
            cals = body.get("calendars")
            if cals is None:  # {"add": [...]} appends to the stored feeds (their URLs never leave the engine)
                cals = engine.accounts.data.get("calendars", []) + list(body.get("add", []))
            return await asyncio.to_thread(engine.accounts.configure_calendars, cals, body.get("add_events_via"))
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ── Smart home ─────────────────────────────────────────────────────────
    @app.get("/api/home", dependencies=[X])
    async def home_status():
        return engine.home.public()

    @app.put("/api/home/ha", dependencies=[X])
    async def home_ha(body: dict = Body(...)):
        try:
            return await asyncio.to_thread(engine.home.connect_ha, str(body.get("url", "")), str(body.get("token", "")))
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/home/hue/discover", dependencies=[X])
    async def hue_discover():
        return {"bridges": await asyncio.to_thread(engine.home.discover_hue)}

    @app.post("/api/home/hue/pair", dependencies=[X])
    async def hue_pair(body: dict = Body(...)):
        try:
            return await asyncio.to_thread(engine.home.pair_hue, str(body.get("ip", "")).strip())
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/home/{which}", dependencies=[X])
    async def home_disconnect(which: str):
        if which not in ("ha", "hue"):
            raise HTTPException(404)
        return engine.home.disconnect(which)

    @app.get("/api/home/devices", dependencies=[A])
    async def home_devices():
        try:
            return await asyncio.to_thread(engine.home._devices)
        except ToolError as exc:
            raise HTTPException(409, str(exc)) from exc

    # ── System check ───────────────────────────────────────────────────────
    @app.post("/api/diagnostics", dependencies=[X])
    async def diagnostics(body: dict = Body(default={})):
        from ..diagnostics import run_all

        return await run_all(engine, include_generation=bool(body.get("generate", True)))

    # ── Phone app: management (desktop only) ───────────────────────────────
    @app.get("/api/remote", dependencies=[X])
    async def remote_status():
        return engine.remote.status(with_qr=True)

    @app.post("/api/remote", dependencies=[X])
    async def remote_configure(body: dict = Body(...)):
        patch = {}
        if "enabled" in body:
            patch["remote_enabled"] = bool(body["enabled"])
        if body.get("access") in ("lan", "tailscale", "cloudflare"):
            patch["remote_access"] = body["access"]
        engine.apply_settings(patch)
        try:
            if engine.config.settings.remote_enabled:
                await engine.remote.start()
            else:
                await engine.remote.stop()
        except OSError as exc:
            engine.apply_settings({"remote_enabled": False})
            raise HTTPException(409, f"Couldn't open the phone port: {exc}") from exc
        return engine.remote.status(with_qr=True)

    @app.delete("/api/devices/{device_id}", dependencies=[X])
    async def revoke_device(device_id: str):
        engine.remote.devices.revoke(device_id)
        engine.bus.emit("remote.changed")
        return engine.remote.status(with_qr=True)

    @app.post("/api/push/test", dependencies=[X])
    async def push_test():
        n = await asyncio.to_thread(engine.remote.push.send, "ORION", "Notifications are working.", {}, "orion-test")
        return {"sent": n}

    # ── Phone app: pairing, push, voice, screen, direct control ────────────
    @app.post("/api/pair")
    async def pair(request: Request, body: dict = Body(...)):
        if not remote:
            raise HTTPException(404)
        ip = request.client.host if request.client else "?"
        if engine.remote.devices.blocked(ip):
            raise HTTPException(429, "Too many attempts. Try again later.")
        tok = engine.remote.devices.pair(str(body.get("code", "")), str(body.get("name", "Phone")))
        if not tok:
            engine.remote.devices.record_failure(ip)
            raise HTTPException(400, "This pairing code has expired. Show a new QR code in ORION → Settings → Phone app.")
        engine.bus.emit("remote.changed")
        engine.notify("Phone paired", f"{body.get('name', 'A phone')} can now control ORION.")
        return {"token": tok}

    @app.get("/api/push/key")
    async def push_key():
        return {"key": engine.remote.push.keys()["public"]}

    @app.post("/api/push/subscribe", dependencies=[A])
    async def push_subscribe(request: Request, body: dict = Body(...)):
        if not remote:
            raise HTTPException(404)
        engine.remote.devices.set_push(request.state.device["id"], body.get("subscription"))
        return {"ok": True}

    @app.get("/api/screen", dependencies=[A])
    async def screen(w: int = 1280, q: int = 70):
        from fastapi.responses import Response

        from ..tools.screen import capture

        try:
            jpeg, size = await asyncio.to_thread(capture, 1, max(320, min(w, 1920)))
        except Exception as exc:
            raise HTTPException(503, f"Screen capture unavailable: {exc}") from exc
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Screen-Size": f"{size[0]}x{size[1]}"})

    @app.post("/api/phone/voice", dependencies=[A])
    async def voice_ask(body: dict = Body(...)):
        """Phone push-to-talk: WAV in → transcript, then ORION handles it like a typed request."""
        wav = base64.b64decode(body.get("wav_b64", ""))
        if len(wav) < 2000:
            raise HTTPException(400, "Too short — hold the button while you speak.")
        if len(wav) > MAX_ATTACH:
            raise HTTPException(413, "Recording too long.")
        try:
            text = await engine.brain.transcribe(wav)
        except Exception as exc:
            from ..brain.gemini import friendly_error

            raise HTTPException(502, friendly_error(exc)) from exc
        if not text:
            return {"text": "", "conv_id": body.get("conv_id")}
        cid = body.get("conv_id")
        if not cid or not engine.store.get_conversation(cid):
            from ..brain.agent import make_title

            conv = engine.store.create_conversation(make_title(text))
            engine.bus.emit("conversation.created", conversation=conv)
            cid = conv["id"]
        spawn(engine.agent.handle(text, cid, source="phone"))
        return {"text": text, "conv_id": cid}

    @app.post("/api/control/{name}", dependencies=[A])
    async def control(name: str, body: dict = Body(default={})):
        """Direct remote control from the phone (buttons, taps, typing). These are the user's own actions,
        so they don't need AI-approval prompts — but they're still recorded as a task for the audit trail."""
        if name not in REMOTE_CONTROLS:
            raise HTTPException(403, "That control isn't available remotely.")
        task = engine.tracker.start(f"Phone: {name.replace('_', ' ')}", None, "phone")
        tool = engine.registry.get(name)
        if tool is None:
            engine.tracker.finish(task, "failed")
            raise HTTPException(404, "Unknown control.")
        from ..tools.registry import ToolContext

        step = engine.tracker.add_step(task, name, tool.describe_call(body), tool.risk)
        try:
            result = await asyncio.wait_for(tool.run(body, ToolContext(engine=engine, cancel=task.cancel)), 60)
            engine.tracker.update_step(task, step, "done", result)
            engine.tracker.finish(task)
            return {"ok": True, "result": result}
        except Exception as exc:
            engine.tracker.update_step(task, step, "failed", str(exc))
            engine.tracker.finish(task, "failed")
            return {"ok": False, "error": str(exc)}

    # ── Desktop shell ──────────────────────────────────────────────────────
    @app.post("/api/open-folder/{which}", dependencies=[X])
    async def open_folder(which: str):
        target = {"data": engine.config.root, "plugins": engine.plugins.folder, "skills": engine.skills.user_dir}.get(which)
        if not target:
            raise HTTPException(404)
        open_target(str(target))
        return {"ok": True}

    @app.post("/api/window/{action}", dependencies=[X])
    async def window(action: str):
        if shell is None:
            return {"ok": False}
        shell.window_action(action)
        return {"ok": True}

    # ── Event stream ───────────────────────────────────────────────────────
    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        supplied = socket.query_params.get("token", "")
        origin = socket.headers.get("origin", "")
        same_host = origin.split("://", 1)[-1] == socket.headers.get("host", "")
        local = origin.startswith(("http://127.0.0.1", "http://localhost"))
        ip = socket.client.host if socket.client else "?"
        try:
            ok = check_token(supplied, ip) is not None
        except HTTPException:
            ok = False
        if not ok or (origin and not (local or (remote and same_host))):
            await socket.close(code=4401)
            return
        await socket.accept()
        q = engine.bus.subscribe()
        await socket.send_json({"type": "hello", "state": engine.state()})
        try:
            receiver = asyncio.create_task(socket.receive_text())
            while True:
                getter = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    getter.cancel()
                    receiver.result()  # raises on disconnect
                    receiver = asyncio.create_task(socket.receive_text())  # client pings are ignored
                    continue
                await socket.send_json(getter.result())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            engine.bus.unsubscribe(q)

    # ── UI ─────────────────────────────────────────────────────────────────
    if (WEB_DIR / "index.html").exists():
        if (WEB_DIR / "assets").exists():
            app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            f = (WEB_DIR / path).resolve()
            if path and f.is_file() and WEB_DIR.resolve() in f.parents:
                headers = {"Cache-Control": "no-cache"} if path in ("sw.js", "manifest.webmanifest") else None
                return FileResponse(f, headers=headers)
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

    return app
