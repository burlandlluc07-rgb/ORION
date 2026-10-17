"""Windows lab for the background agent (run on a throwaway Windows desktop,
e.g. a GitHub Actions windows-latest runner).

Checks the parts that only exist on Windows: the daemon with tray, toasts and
hotkey; hidden shell commands; Task Scheduler autostart; crash restart under
the supervisor; and, when GEMINI_API_KEY is set, live Gemini tasks. Around
every step it records the mouse position and the foreground window, to show
the agent never moved the mouse or took focus. Two control cases (this script
opens Notepad and moves the mouse itself) prove the probe can see a change.

    python scripts/windows_lab.py --out lab_report.json [--frozen dist/orion-bg/orion-bg-cli.exe]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HOME = Path(tempfile.mkdtemp(prefix="orion-lab-"))
WORK = HOME / "work"
WORK.mkdir()
os.environ["ORION_HOME"] = str(HOME)

from orion_bg import config, paths  # noqa: E402
from orion_bg.client import Client, DaemonNotRunning  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
RESULTS: list[dict] = []


# -- probes ------------------------------------------------------------------

def desktop() -> dict:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return {"mouse": [pt.x, pt.y], "hwnd": int(hwnd or 0), "title": buf.value}


def same_desktop(before: dict, after: dict) -> bool:
    return before["mouse"] == after["mouse"] and before["hwnd"] == after["hwnd"]


def check(name: str, required: bool = True):
    def wrap(fn):
        def run(*a, **kw):
            before = desktop()
            t0 = time.time()
            entry = {"name": name, "required": required}
            try:
                detail = fn(*a, **kw)
                entry.update(ok=True, detail=detail)
            except Skip as exc:
                entry.update(ok=None, detail=f"skipped: {exc}")
            except Exception as exc:
                entry.update(ok=False, detail=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc())
            entry["seconds"] = round(time.time() - t0, 1)
            entry["desktop_before"], entry["desktop_after"] = before, desktop()
            entry["desktop_unchanged"] = same_desktop(before, entry["desktop_after"])
            RESULTS.append(entry)
            mark = {True: "PASS", False: "FAIL", None: "SKIP"}[entry["ok"]]
            print(f"[{mark}] {name} ({entry['seconds']} s): {entry['detail']}", flush=True)
            return entry
        return run
    return wrap


class Skip(Exception):
    pass


def expect(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def wait_for(fn, timeout: float, interval: float = 0.3, what: str = "condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except (DaemonNotRunning, OSError, ValueError):
            pass
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {what} (last: {last!r})")


def log_text() -> str:
    path = paths.log_dir() / "background-agent.log"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def runtime() -> dict | None:
    f = paths.runtime_file()
    return json.loads(f.read_text()) if f.exists() else None


def client() -> Client:
    return Client.from_runtime(timeout=10)


def wait_task(task_id: str, statuses=("completed", "failed", "cancelled"), timeout=30.0) -> dict:
    return wait_for(lambda: (t := client().task(task_id))["status"] in statuses and t, timeout, what=f"task {task_id} in {statuses}")


def cli(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "orion_bg", *args], cwd=ROOT, capture_output=True,
                          text=True, timeout=timeout)


def write_config() -> None:
    paths.config_path().write_text(
        "[daemon]\nworkers = 2\n"
        "[tasks]\ndummy_seconds = 1.0\ntimeout_seconds = 300\n"
        f'[safety]\nallowed_paths = ["{WORK.as_posix()}"]\n'
        "[notify]\ntoast = true\n"
        '[hotkey]\nenabled = true\ncombo = "ctrl+alt+o"\n'
        "[tray]\nenabled = true\n"
        "[logging]\nlevel = \"INFO\"\n",
        encoding="utf-8",
    )


# -- checks ------------------------------------------------------------------

DAEMON: subprocess.Popen | None = None


@check("daemon starts with tray, toast and hotkey, without taking focus")
def start_daemon():
    global DAEMON
    DAEMON = subprocess.Popen([sys.executable, "-m", "orion_bg", "daemon"], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(lambda: runtime() and client().ping(), 30, what="daemon ready")
    wait_for(lambda: "tray icon shown" in log_text(), 15, what="tray icon")
    text = log_text()
    expect("hotkey ctrl+alt+o registered" in text, "hotkey was not registered")
    return f"pid {DAEMON.pid}, tray shown, hotkey registered"


@check("dummy task end to end + toast")
def dummy_task():
    t = client().submit("lab dummy", kind="dummy", source="lab")
    done = wait_task(t["id"])
    expect(done["status"] == "completed", f"status {done['status']}")
    time.sleep(1)
    text = log_text()
    expect("notify: Orion finished a task" in text, "no completion notification")
    expect("toast failed" not in text, "winotify raised")
    import winotify  # noqa: F401  # the toast backend is really there
    return done["result"]


@check("approval gate: needs_approval, approve, deny")
def approval():
    out = []
    for verdict in ("approve", "deny"):
        t = client().submit("lab [approval]", kind="dummy")
        wait_task(t["id"], ("needs_approval",))
        (a,) = [x for x in client().approvals() if x["task_id"] == t["id"]]
        getattr(client(), verdict)(a["id"])
        done = wait_task(t["id"])
        expect(done["status"] == "completed", done["status"])
        out.append(done["result"])
    expect("approved" in out[0] and "denied" in out[1], str(out))
    return out


@check("cancel and timeout")
def cancel_timeout():
    t = client().submit("lab slow", kind="dummy", timeout=0.3)
    done = wait_task(t["id"])
    expect(done["status"] == "failed" and "Timed out" in (done["error"] or ""), str(done))
    client().pause()
    q = client().submit("lab queued", kind="dummy")
    expect(client().cancel(q["id"])["status"] == "cancelled", "queued cancel failed")
    client().resume()
    return "timeout failed the task; queued task cancelled"


@check("hotkey queues the clipboard text")
def hotkey():
    text = "lab hotkey task: reply with the word OK"
    set_clipboard(text)
    press_hotkey()
    found = wait_for(lambda: [t for t in client().tasks(limit=50) if t["source"] == "hotkey"], 10, what="hotkey task")
    expect(found[0]["prompt"] == text, found[0]["prompt"])
    if found[0]["status"] in ("queued", "running"):
        client().cancel(found[0]["id"])
    return f"task {found[0]['id']} queued from hotkey"


@check("shell commands run with no window and no focus change")
def hidden_shell():
    from orion_bg.runners import RunContext
    from orion_bg.tools import Toolbox

    cfg = config.load()
    tb = Toolbox(cfg)
    ctx = RunContext({}, time.monotonic() + 60, lambda: False, lambda s: None, lambda *a: None)
    outs = []
    for cmd in ("ver", "cmd /c echo hidden-cmd", "powershell -NoProfile -Command Write-Output hidden-ps",
                "python -c \"print('hidden-py')\""):
        before = desktop()
        out = tb.run("run_shell", {"command": cmd}, ctx)
        time.sleep(0.5)
        after = desktop()
        expect("exit code 0" in out, f"{cmd}: {out}")
        expect(same_desktop(before, after), f"{cmd} changed the desktop: {before} -> {after}")
        outs.append(out.splitlines()[-1])
    return outs


@check("history page served")
def history_page():
    import urllib.request
    c = client()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    html = opener.open(c.ui_url(), timeout=10).read().decode()
    expect("Orion background tasks" in html, "page missing")
    return "ok"


@check("live Gemini: write, read, overwrite with approval, web search", required=False)
def live_gemini():
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise Skip("no GEMINI_API_KEY secret")
    c = client()
    t = c.submit("Create a text file named lab.txt in the allowed folder containing exactly: hello from gemini. "
                 "Then read it back and tell me its exact content.")
    done = wait_task(t["id"], timeout=240)
    expect(done["status"] == "completed", f"{done['status']}: {done.get('error')}")
    content = (WORK / "lab.txt").read_text(encoding="utf-8").strip()
    expect(content == "hello from gemini", f"file has {content!r}")
    t2 = c.submit("Overwrite lab.txt in the allowed folder so it contains exactly: second version")
    parked = wait_task(t2["id"], ("needs_approval", "completed", "failed"), timeout=240)
    expect(parked["status"] == "needs_approval", f"expected needs_approval, got {parked['status']}: {parked.get('error')}")
    expect((WORK / "lab.txt").read_text(encoding="utf-8").strip() == "hello from gemini", "file changed before approval")
    for a in [x for x in c.approvals() if x["task_id"] == t2["id"]]:
        c.approve(a["id"])
    done2 = wait_task(t2["id"], timeout=240)
    expect(done2["status"] == "completed", f"{done2['status']}: {done2.get('error')}")
    expect((WORK / "lab.txt").read_text(encoding="utf-8").strip() == "second version", "overwrite not applied")
    t3 = c.submit("Use web search to find the capital city of Australia. Answer with just the city name.")
    done3 = wait_task(t3["id"], timeout=240)
    expect(done3["status"] == "completed", f"{done3['status']}: {done3.get('error')}")
    expect("canberra" in done3["result"].lower(), done3["result"])
    switched = [l for l in log_text().splitlines() if "out of quota; switching" in l]
    return {"write": done["result"][:200], "overwrite": done2["result"][:200], "search": done3["result"][:200],
            "model_switches": [l[-120:] for l in switched]}


@check("clean stop: exit code 0 and daemon.json removed")
def stop_daemon():
    client().shutdown()
    code = DAEMON.wait(30)
    expect(code == 0, f"exit code {code}")
    expect(not paths.runtime_file().exists(), "daemon.json left behind")
    return "exit 0"


@check("supervisor restarts a killed daemon; schedules survive")
def supervisor():
    sup = subprocess.Popen([sys.executable, "-m", "orion_bg", "run"], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        first = wait_for(lambda: runtime() and client().ping() and runtime(), 40, what="first daemon")
        s = client().add_schedule("lab morning brief", "daily 08:00", kind="dummy")
        subprocess.run(["taskkill", "/F", "/PID", str(first["pid"])], capture_output=True)
        second = wait_for(lambda: (r := runtime()) and r["pid"] != first["pid"] and client().ping() and r,
                          60, what="restarted daemon")
        ids = [x["id"] for x in client().schedules()]
        expect(s["id"] in ids, "schedule lost across restart")
        client().shutdown()
        code = sup.wait(30)
        expect(code == 0, f"supervisor exit code {code}")
        return f"daemon pid {first['pid']} killed, came back as {second['pid']}; supervisor exited 0"
    finally:
        if sup.poll() is None:
            sup.kill()


@check("autostart: scheduled task installs, runs hidden, uninstalls")
def autostart():
    r = cli("install-autostart")
    expect(r.returncode == 0, r.stderr or r.stdout)
    q = subprocess.run(["schtasks", "/Query", "/TN", "Orion Background Agent", "/XML"], capture_output=True, text=True)
    expect(q.returncode == 0, q.stderr)
    expect("<Hidden>true</Hidden>" in q.stdout and "LogonTrigger" in q.stdout and "RestartOnFailure" in q.stdout,
           "task XML missing settings")
    # Run it now. It has no ORION_HOME, so it uses the runner's real %APPDATA%.
    appdata_rt = Path(os.environ["APPDATA"]) / "Orion" / "background" / "daemon.json"
    before = desktop()
    run = subprocess.run(["schtasks", "/Run", "/TN", "Orion Background Agent"], capture_output=True, text=True)
    expect(run.returncode == 0, run.stderr)

    def started():
        if not appdata_rt.exists():
            return None
        info = json.loads(appdata_rt.read_text())
        c = Client(f"http://{info['host']}:{info['port']}", info["token"], timeout=5)
        return c if c.ping() else None

    c = wait_for(started, 60, what="daemon started by Task Scheduler")
    time.sleep(2)
    after = desktop()
    expect(same_desktop(before, after), f"autostart changed the desktop: {before} -> {after}")
    t = c.submit("autostart dummy", kind="dummy")
    wait_for(lambda: c.task(t["id"])["status"] == "completed", 30, what="autostart task")
    c.shutdown()
    wait_for(lambda: not appdata_rt.exists(), 30, what="autostart daemon stop")
    u = cli("uninstall-autostart")
    expect(u.returncode == 0, u.stderr or u.stdout)
    gone = subprocess.run(["schtasks", "/Query", "/TN", "Orion Background Agent"], capture_output=True, text=True)
    expect(gone.returncode != 0, "task still registered")
    return "installed, ran a task hidden, removed"


@check("frozen build runs a task", required=False)
def frozen(exe: str | None):
    if not exe:
        raise Skip("no --frozen exe given")
    home = Path(tempfile.mkdtemp(prefix="orion-frozen-"))
    # No Gemini key for the frozen daemon: the import check below must fail on
    # the missing key, not spend quota on a real call.
    env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
    env["ORION_HOME"] = str(home)
    (home / "background-agent.toml").write_text(
        f'[safety]\nallowed_paths = ["{WORK.as_posix()}"]\n[tray]\nenabled = false\n[hotkey]\nenabled = false\n'
        "[tasks]\ndummy_seconds = 0.5\n[gemini]\nuse_orion_keyring = false\n", encoding="utf-8")
    d = subprocess.Popen([exe, "daemon"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = None
        for _ in range(60):
            r = subprocess.run([exe, "submit", "frozen dummy", "--kind", "dummy", "--wait"], env=env,
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 2:  # 2 = daemon not reachable yet
                break
            time.sleep(1)
        if r is None or r.returncode != 0 or "Dummy task done: frozen dummy" not in r.stdout:
            listing = subprocess.run([exe, "list"], env=env, capture_output=True, text=True, timeout=30).stdout
            flog = home / "logs" / "background-agent.log"
            tail = flog.read_text(encoding="utf-8", errors="replace")[-3000:] if flog.exists() else "(no log)"
            raise AssertionError(f"submit said: {(r.stdout + r.stderr) if r else ''}\nlist:\n{listing}\nlog tail:\n{tail}")
        g = subprocess.run([exe, "submit", "frozen gemini import check", "--wait"], env=env,
                           capture_output=True, text=True, timeout=60)
        expect("Set the GEMINI_API_KEY" in g.stdout + g.stderr, g.stdout + g.stderr)
        subprocess.run([exe, "stop"], env=env, capture_output=True, timeout=30)
        expect(d.wait(30) == 0, "frozen daemon did not exit cleanly")
        return "dummy task completed from the frozen exe; google-genai imports"
    finally:
        if d.poll() is None:
            d.kill()


# -- control cases: prove the probe sees real changes -------------------------

@check("control: probe detects a focus change (this script opens Notepad)")
def control_focus():
    before = desktop()
    p = subprocess.Popen(["notepad.exe"])
    try:
        wait_for(lambda: desktop()["hwnd"] != before["hwnd"], 10, what="the probe to see the focus change")
        return f"foreground changed to {desktop()['title']!r}"
    finally:
        p.kill()
        time.sleep(1)


@check("control: probe detects a mouse move (this script moves the cursor)")
def control_mouse():
    before = desktop()
    user32.SetCursorPos(before["mouse"][0] + 50, before["mouse"][1] + 50)
    after = desktop()
    user32.SetCursorPos(*before["mouse"])
    expect(after["mouse"] != before["mouse"], "probe missed the mouse move")
    return f"{before['mouse']} -> {after['mouse']}"


# -- Windows input helpers (used only by this lab) ------------------------------

def set_clipboard(text: str) -> None:
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.1)
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0042, size)  # GHND
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(13, handle)  # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()


def press_hotkey() -> None:
    VK_CONTROL, VK_MENU, KEYUP = 0x11, 0x12, 0x2
    for vk in (VK_CONTROL, VK_MENU, ord("O")):
        user32.keybd_event(vk, 0, 0, 0)
    for vk in (ord("O"), VK_MENU, VK_CONTROL):
        user32.keybd_event(vk, 0, KEYUP, 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="lab_report.json")
    ap.add_argument("--frozen")
    args = ap.parse_args()
    write_config()
    print(f"lab home: {HOME}\nstart desktop: {desktop()}", flush=True)

    start_daemon()
    if RESULTS[-1]["ok"]:
        dummy_task()
        approval()
        cancel_timeout()
        hotkey()
        hidden_shell()
        history_page()
        live_gemini()
        stop_daemon()
    supervisor()
    autostart()
    frozen(args.frozen)
    control_focus()
    control_mouse()

    # Steps that involve the agent must leave the desktop alone. The hotkey
    # step presses keys itself and the control cases change it on purpose.
    agent_steps = [r for r in RESULTS if not r["name"].startswith(("control:", "hotkey")) and r["ok"] is not None]
    moved = [r["name"] for r in agent_steps if not r["desktop_unchanged"]]
    controls_ok = all(r["ok"] for r in RESULTS if r["name"].startswith("control:"))
    failed = [r["name"] for r in RESULTS if r["ok"] is False and r["required"]]
    optional_failed = [r["name"] for r in RESULTS if r["ok"] is False and not r["required"]]
    summary = {
        "passed": sum(1 for r in RESULTS if r["ok"]),
        "failed": failed,
        "optional_failed": optional_failed,
        "skipped": [r["name"] for r in RESULTS if r["ok"] is None],
        "desktop_changed_by_agent": moved,
        "controls_detected_changes": controls_ok,
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "results": RESULTS}, indent=2), encoding="utf-8")
    log_copy = Path(args.out).with_name("background-agent.log")
    log_copy.write_text(log_text(), encoding="utf-8")

    lines = ["## Background agent Windows lab", "", "| Result | Check | Seconds | Desktop unchanged | Detail |", "|---|---|---|---|---|"]
    for r in RESULTS:
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[r["ok"]]
        detail = str(r["detail"]).replace("|", "/").replace("\n", " ")[:160]
        lines.append(f"| {mark} | {r['name']} | {r['seconds']} | {r['desktop_unchanged']} | {detail} |")
    lines += ["", f"Summary: `{json.dumps(summary)}`"]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if failed or moved or not controls_ok else 0


if __name__ == "__main__":
    sys.exit(main())
