"""ORION baseline smoke check (Day 2 of the background-execution plan).

Starts ORION headless with a throwaway data folder, runs a few harmless background-type tasks through
its local API, and records what happened. On Windows it also records the mouse position and the
foreground window before and after each task, so it shows whether ORION moved the mouse or took focus.

Usage (from the repository root, with ORION's dependencies installed):
    python scripts/smoke_check.py [--out report.json] [--controls]

It changes nothing outside a temporary folder: no API key is set, no app is opened, nothing is sent.

--controls (for a throwaway CI desktop only) also runs two tools that are known to take the desktop, open_app
("notepad") and an approved mouse_click, to prove the probe really detects a focus change and a mouse move.
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
    """Mouse position and foreground window (Windows only)."""
    if not IS_WIN:
        return {"available": False}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return {"available": True, "mouse": [pt.x, pt.y], "foreground_hwnd": int(hwnd or 0),
            "foreground_title": buf.value}


def wait_for_instance(home: Path, proc: subprocess.Popen, timeout: float = 90) -> dict:
    f = home / "instance.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"ORION exited early with code {proc.returncode}")
        if f.exists():
            try:
                info = json.loads(f.read_text())
                httpx.get(f"http://127.0.0.1:{info['port']}/api/state",
                          headers={"x-orion-token": info["token"]}, timeout=3).raise_for_status()
                return info
            except (ValueError, KeyError, httpx.HTTPError):
                pass
        time.sleep(0.5)
    raise RuntimeError("ORION did not become ready in time")


def run_control(c: httpx.Client, name: str, targs: dict, expect: str) -> dict:
    """Run a desktop-taking tool, approving its approval prompt through the API like the user would."""
    before = desktop_state()
    box: dict = {}

    def call() -> None:
        with httpx.Client(base_url=c.base_url, headers=c.headers, timeout=90) as own:  # one client per thread
            box["r"] = own.post(f"/api/tools/{name}/run", json={"args": targs, "timeout": 60}).json()

    th = threading.Thread(target=call)
    th.start()
    deadline = time.time() + 30
    while th.is_alive() and time.time() < deadline:
        for appr in c.get("/api/state").json().get("approvals", []):
            if appr.get("tool") == name:
                c.post(f"/api/approvals/{appr['id']}", json={"decision": "approve"})
        time.sleep(0.3)
    th.join(60)
    time.sleep(1.5)  # give the launched window time to come to the front
    after = desktop_state()
    r = box.get("r", {})
    out = {"tool": name, "ok": bool(r.get("ok")), "result": str(r.get("result"))[:200], "expect": expect,
           "mouse_moved": before.get("mouse") != after.get("mouse"),
           "focus_changed": before.get("foreground_hwnd") != after.get("foreground_hwnd"),
           "desktop_before": before, "desktop_after": after}
    out["detected"] = bool(out[expect])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="smoke_report.json")
    ap.add_argument("--controls", action="store_true", help="also run desktop-taking control cases (CI only)")
    args = ap.parse_args()

    home = Path(tempfile.mkdtemp(prefix="orion-smoke-"))
    env = {**os.environ, "ORION_HOME": str(home), "ORION_BROWSER_HEADLESS": "1"}
    env.pop("GEMINI_API_KEY", None)  # keep the check offline and quota-free
    started = time.time()
    proc = subprocess.Popen([sys.executable, "-m", "orion", "--headless"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results: list[dict] = []
    controls: list[dict] = []
    report: dict = {"platform": sys.platform, "python": sys.version.split()[0], "data_dir": str(home)}
    try:
        info = wait_for_instance(home, proc)
        report["startup_seconds"] = round(time.time() - started, 1)
        c = httpx.Client(base_url=f"http://127.0.0.1:{info['port']}",
                         headers={"x-orion-token": info["token"]}, timeout=60)
        state = c.get("/api/state").json()
        tools = c.get("/api/tools").json()
        report["version"] = state.get("version")
        report["tool_count"] = len(tools)

        sample = home / "sample"
        sample.mkdir()
        (sample / "hello.txt").write_text("hello from the smoke check\n", encoding="utf-8")
        tasks = [
            ("get_time_and_date", {}),
            ("system_status", {}),
            ("list_folder", {"path": str(sample)}),
            ("read_file", {"path": str(sample / "hello.txt")}),
        ]
        for name, targs in tasks:
            before = desktop_state()
            t0 = time.perf_counter()
            r = c.post(f"/api/tools/{name}/run", json={"args": targs, "timeout": 60}).json()
            ms = round((time.perf_counter() - t0) * 1000)
            after = desktop_state()
            probed = before.get("available")
            moved = before.get("mouse") != after.get("mouse") if probed else None
            focus = before.get("foreground_hwnd") != after.get("foreground_hwnd") if probed else None
            ok = bool(r.get("ok"))
            if name == "read_file":
                ok = ok and "hello from the smoke check" in str(r.get("result"))
            results.append({"tool": name, "ok": ok, "ms": ms, "mouse_moved": moved, "focus_changed": focus,
                            "result": str(r.get("result"))[:200], "desktop_before": before,
                            "desktop_after": after})

        if args.controls and IS_WIN:
            controls.append(run_control(c, "open_app", {"name": "notepad"}, expect="focus_changed"))
            st = desktop_state()
            x, y = st["mouse"][0] + 120, st["mouse"][1] + 80
            controls.append(run_control(c, "mouse_click", {"x": x, "y": y}, expect="mouse_moved"))

        recorded = c.get("/api/tasks").json()
        history = recorded.get("history", []) if isinstance(recorded, dict) else recorded
        report["tasks_recorded"] = len(history)
        report["task_statuses"] = sorted({t.get("status") for t in history})
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
    report["results"] = results
    report["controls"] = controls
    report["background_untouched"] = bool(results) and not any(r["mouse_moved"] or r["focus_changed"]
                                                               for r in results)
    report["controls_detected"] = all(ct["detected"] for ct in controls) if controls else None
    report["passed"] = "error" not in report and bool(results) and all(r["ok"] for r in results) \
        and report["background_untouched"] and report["controls_detected"] is not False
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['tool']:<18} {r['ms']:>6} ms  "
              f"mouse_moved={r['mouse_moved']}  focus_changed={r['focus_changed']}")
    for ct in controls:
        print(f"{'DETECTED' if ct['detected'] else 'MISSED'} control {ct['tool']:<11} ok={ct['ok']}  "
              f"mouse_moved={ct['mouse_moved']}  focus_changed={ct['focus_changed']}  "
              f"(expected {ct['expect']})")
    if "error" in report:
        print("ERROR:", report["error"])
    print("SMOKE", "PASSED" if report["passed"] else "FAILED", f"(report: {args.out})")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
