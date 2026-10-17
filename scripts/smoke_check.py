"""ORION background-execution check: does ORION leave the user's desktop alone?

Starts ORION headless with a throwaway data folder and drives it through its local API like the app
would. On Windows it records, around every step, the mouse position, the foreground window and the
time of the last keyboard or mouse input (GetLastInputInfo, which injected input also updates), so it
shows whether ORION moved the mouse, took focus or sent input.

Usage (from the repository root, with ORION's dependencies installed):
    python scripts/smoke_check.py [--out report.json] [--controls]

Without --controls it only runs harmless background tasks in a temporary folder: no API key is set,
no app is opened, nothing is sent. --controls (for a throwaway CI desktop only) adds the full set:
foreground requests that are denied and approved, the background browser, staged cancellation, an
honest network failure and a restart.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

IS_WIN = sys.platform == "win32"


def desktop_state() -> dict:
    """Mouse position, foreground window and last-input time (Windows only)."""
    if not IS_WIN:
        return {"available": False}
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    lii = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    user32.GetLastInputInfo(ctypes.byref(lii))
    return {"available": True, "mouse": [pt.x, pt.y], "foreground_hwnd": int(hwnd or 0),
            "foreground_title": buf.value, "last_input": int(lii.dwTime)}


def changes(before: dict, after: dict) -> dict:
    if not before.get("available"):
        return {"mouse_moved": None, "focus_changed": None, "input_sent": None}
    return {"mouse_moved": before["mouse"] != after["mouse"],
            "focus_changed": before["foreground_hwnd"] != after["foreground_hwnd"],
            "input_sent": before["last_input"] != after["last_input"]}


def start_orion(home: Path, env: dict) -> tuple[subprocess.Popen, httpx.Client]:
    (home / "instance.json").unlink(missing_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "orion", "--headless"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = home / "instance.json"
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"ORION exited early with code {proc.returncode}")
        if f.exists():
            try:
                info = json.loads(f.read_text())
                c = httpx.Client(base_url=f"http://127.0.0.1:{info['port']}",
                                 headers={"x-orion-token": info["token"]}, timeout=120)
                c.get("/api/state").raise_for_status()
                return proc, c
            except (ValueError, KeyError, httpx.HTTPError):
                pass
        time.sleep(0.5)
    raise RuntimeError("ORION did not become ready in time")


def stop_orion(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_tool(c: httpx.Client, name: str, targs: dict, decision: str | None = None, settle: float = 0.0,
             during=None) -> dict:
    """Run one tool through ORION's executor, answering its approval prompt the way the user would."""
    before = desktop_state()
    box: dict = {}

    def call() -> None:
        with httpx.Client(base_url=c.base_url, headers=c.headers, timeout=120) as own:  # one client per thread
            box["r"] = own.post(f"/api/tools/{name}/run", json={"args": targs, "timeout": 90}).json()

    t0 = time.perf_counter()
    th = threading.Thread(target=call)
    th.start()
    asked: list[dict] = []
    deadline = time.time() + 100
    while th.is_alive() and time.time() < deadline:
        for appr in c.get("/api/state").json().get("approvals", []):
            if appr.get("tool") == name and appr["id"] not in {a["id"] for a in asked}:
                asked.append(appr)
                c.post(f"/api/approvals/{appr['id']}", json={"decision": decision or "deny"})
        if during is not None and (asked or decision is None):  # once the prompt is answered (or none is expected)
            during()
            during = None
        time.sleep(0.2)
    th.join(120)
    ms = round((time.perf_counter() - t0) * 1000)
    time.sleep(settle)
    after = desktop_state()
    r = box.get("r", {})
    return {"tool": name, "ok": bool(r.get("ok")), "ms": ms, "result": str(r.get("result"))[:240],
            "asked": bool(asked), "asked_desktop": any(a.get("desktop") for a in asked), **changes(before, after),
            "desktop_before": before, "desktop_after": after}


def last_step(c: httpx.Client, tool: str) -> dict:
    hist = c.get("/api/tasks").json().get("history", [])
    for t in hist:
        for st in reversed(t.get("steps", [])):
            if st.get("tool") == tool:
                return st.get("lifecycle") or {}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="smoke_report.json")
    ap.add_argument("--controls", action="store_true", help="also run the desktop-taking cases (CI only)")
    args = ap.parse_args()

    home = Path(tempfile.mkdtemp(prefix="orion-smoke-"))
    env = {**os.environ, "ORION_HOME": str(home)}
    env.pop("GEMINI_API_KEY", None)  # keep the check offline and quota-free
    env.pop("ORION_BROWSER_HEADLESS", None)  # the default (background browser) is what's being checked
    report: dict = {"platform": sys.platform, "python": sys.version.split()[0], "data_dir": str(home)}
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: dict) -> None:
        checks.append({"check": name, "passed": bool(passed), **detail})

    started = time.time()
    proc = None
    try:
        proc, c = start_orion(home, env)
        report["startup_seconds"] = round(time.time() - started, 1)
        report["version"] = c.get("/api/state").json().get("version")
        report["tool_count"] = len(c.get("/api/tools").json())
        quiet = lambda r: not (r["mouse_moved"] or r["focus_changed"] or r["input_sent"])  # noqa: E731

        sample = home / "sample"
        sample.mkdir()
        (sample / "hello.txt").write_text("hello from the smoke check\n", encoding="utf-8")
        c.patch("/api/settings", json={"allowed_roots": [str(home)]})

        # 1. Ordinary background tasks: no prompt, no mouse, no focus, no input.
        for name, targs in [("get_time_and_date", {}), ("system_status", {}), ("list_folder", {"path": str(sample)}),
                            ("read_file", {"path": str(sample / "hello.txt")})]:
            r = run_tool(c, name, targs)
            ok = r["ok"] and (name != "read_file" or "hello from the smoke check" in r["result"])
            check(f"background: {name}", ok and not r["asked"] and quiet(r) is not False, r)
        # A document is written with file libraries, approved like any change, and reopened to verify it.
        doc = str(sample / "report.docx")
        r = run_tool(c, "create_document", {"path": doc, "content": "Background check report", "title": "Check"},
                     decision="approve")
        rec = last_step(c, "create_document")
        r["lifecycle_stage"] = rec.get("stage")
        r["verification"] = (rec.get("verification") or {}).get("status")
        check("background: create_document verified", r["ok"] and r["verification"] == "passed"
              and not r["asked_desktop"] and quiet(r) is not False, r)
        check("lifecycle: full record", [h["stage"] for h in rec.get("history", [])] ==
              ["queued", "validating", "awaiting_permission", "running", "verifying", "completed"], {"record": rec})

        # Integration readiness is a read-only look: no prompt, nothing started, WhatsApp reported as blocked.
        r = run_tool(c, "integration_status", {})
        states = {i["id"]: i["state"] for i in c.get("/api/integrations").json()["integrations"]}
        r["states"] = states
        check("integrations: status is read-only and honest", r["ok"] and not r["asked"] and quiet(r) is not False
              and states.get("whatsapp") == "blocked" and set(states) == {"google", "whatsapp", "autocad"}, r)

        if args.controls and IS_WIN:
            # 2. Media keys go to the shell as a message: no key press is injected.
            r = run_tool(c, "media_control", {"action": "stop"})
            check("background: media_control sends no input", r["ok"] and quiet(r), r)
            # 3. The browser works in the background: no window, no focus change.
            page = "data:text/html,<title>ORION check</title><button>Go</button>"
            r = run_tool(c, "browser_open", {"url": page}, settle=1.0)
            check("background: browser_open has no window", r["ok"] and "ORION check" in r["result"] and quiet(r), r)
            run_tool(c, "browser_close", {})
            # 4. Foreground actions ask first; a "no" means nothing happens.
            r = run_tool(c, "open_app", {"name": "notepad"}, decision="deny", settle=2.0)
            check("gate: open_app denied changes nothing", not r["ok"] and r["asked_desktop"] and quiet(r), r)
            r = run_tool(c, "type_text", {"text": "should never be typed"}, decision="deny", settle=0.5)
            check("gate: type_text denied sends no input", not r["ok"] and r["asked_desktop"] and quiet(r), r)
            # 5. Control cases: allowed foreground steps really are detected by this probe.
            r = run_tool(c, "open_app", {"name": "notepad"}, decision="approve", settle=2.0)
            r["verification"] = (last_step(c, "open_app").get("verification") or {}).get("status")
            check("control: approved open_app takes focus", r["ok"] and r["asked_desktop"] and r["focus_changed"], r)
            st = desktop_state()
            r = run_tool(c, "mouse_click", {"x": st["mouse"][0] + 120, "y": st["mouse"][1] + 80}, decision="approve")
            check("control: approved mouse_click moves the mouse", r["ok"] and r["mouse_moved"] and r["input_sent"], r)
            subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
            # 6. Cancelling mid-task: staged stop, ORION's own worker ended, recorded honestly.
            r = run_tool(c, "run_command", {"command": "Start-Sleep -Seconds 60; 'done'", "timeout": 120},
                         decision="approve", during=lambda: (time.sleep(2), c.post("/api/stop")))
            rec = last_step(c, "run_command")
            r["lifecycle_stage"] = rec.get("stage")
            check("cancel: mid-task stop is recorded", not r["ok"] and rec.get("stage") == "cancelled"
                  and "cancel_requested" in [h["stage"] for h in rec.get("history", [])] and r["ms"] < 30000, r)
            # 7. A failed remote action is reported as failed, not as success.
            r = run_tool(c, "read_webpage", {"url": "http://127.0.0.1:9/nothing"})
            check("honest failure: unreachable site", not r["ok"] and last_step(c, "read_webpage").get("stage") ==
                  "failed", r)
            # 8. Restart: the task history and its lifecycle records survive.
            stop_orion(proc)
            proc, c = start_orion(home, env)
            rec = last_step(c, "create_document")
            check("restart: records survive", rec.get("stage") == "completed", {"stage": rec.get("stage")})
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if proc is not None:
            stop_orion(proc)
    report["checks"] = checks
    report["passed"] = "error" not in report and bool(checks) and all(ch["passed"] for ch in checks)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for ch in checks:
        extra = "  ".join(f"{k}={ch[k]}" for k in ("mouse_moved", "focus_changed", "input_sent", "asked_desktop")
                          if k in ch)
        print(f"{'PASS' if ch['passed'] else 'FAIL'}  {ch['check']:<48} {extra}")
        if not ch["passed"]:
            print("      ", str(ch.get("result", ch))[:400])
    if "error" in report:
        print("ERROR:", report["error"])
    print("SMOKE", "PASSED" if report["passed"] else "FAILED", f"(report: {args.out})")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
