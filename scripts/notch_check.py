"""Windows check: the notch sits flush at the top centre, grows and shrinks, and never steals focus by itself.

Launches `python -m orion` with Notepad in front, then drives the notch through the API the way the page does:
status (peek), an approval-style open without focus, and the hotkey (which may take focus, because you asked).
Writes notch/*.png and notch/report.json.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import httpx
from PIL import ImageGrab

HOME = Path(os.environ.get("ORION_HOME") or Path(os.environ["RUNNER_TEMP"]) / "orion-notch")
OUT = Path("notch")
OUT.mkdir(exist_ok=True)
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
results: list[dict] = []
SIZES = {"idle": (220, 44), "peek": (460, 72), "open": (700, 500)}  # orion/desktop/notch.py
PILLS = {"idle": (148, 30), "peek": (440, 58), "open": (680, 480)}


def record(name: str, ok: bool, detail: str) -> None:
    results.append({"name": name, "ok": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}", flush=True)


def find(title: str) -> int:
    return int(user32.FindWindowW(None, title) or 0)


def rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def shot(name: str, full: bool = False) -> None:
    sw = user32.GetSystemMetrics(0)
    bbox = None if full else (sw // 2 - 520, 0, sw // 2 + 520, 620)
    ImageGrab.grab(bbox=bbox).save(OUT / f"{name}.png")


def check_geometry(hwnd: int, mode: str) -> None:
    left, top, right, bottom = rect(hwnd)
    sw = user32.GetSystemMetrics(0)
    w, h = right - left, bottom - top
    scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
    ew, eh = (round(v * scale) for v in SIZES[mode])
    centred = abs((left + right) / 2 - sw / 2) <= 2
    record(f"{mode}: size, flush top, centred", top == 0 and centred and abs(w - ew) <= 2 and abs(h - eh) <= 2,
           f"rect=({left},{top},{right},{bottom}) expected {ew}x{eh} on a {sw}px screen")
    gdi32 = ctypes.windll.gdi32
    rgn = gdi32.CreateRectRgn(0, 0, 0, 0)
    kind = user32.GetWindowRgn(hwnd, rgn)
    box = wintypes.RECT()
    gdi32.GetRgnBox(rgn, ctypes.byref(box))
    gdi32.DeleteObject(rgn)
    pw, ph = (round(v * scale) for v in PILLS[mode])
    bw, bh = box.right - box.left, box.bottom - max(box.top, 0)
    pill_centred = abs((box.left + box.right) / 2 - w / 2) <= 2
    record(f"{mode}: window clipped to the pill", kind > 1 and abs(bw - pw) <= 2 and abs(bh - ph) <= 2 and pill_centred,
           f"region kind={kind} box=({box.left},{box.top},{box.right},{box.bottom}) expected pill {pw}x{ph}")


def taskbar_windows(pid: int) -> list[str]:
    """Titles of this process's windows that get a taskbar button: visible, unowned and not tool windows."""
    found: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(h):
            ex = user32.GetWindowLongW(h, -20)
            unowned = not user32.GetWindow(h, 4)  # GW_OWNER
            if (ex & 0x40000) or (unowned and not ex & 0x80):
                title = title_of(h)
                if title:
                    found.append(title)
        return True

    user32.EnumWindows(visit, 0)
    return found


def main() -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "settings.json").write_text(json.dumps({"user_name": "Lluc", "onboarded": True, "theme": "light"}))
    env = {**os.environ, "ORION_HOME": str(HOME), "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY") or "dummy"}
    notepad = subprocess.Popen(["notepad.exe"])
    proc = subprocess.Popen([sys.executable, "-m", "orion", "--minimized"], env=env)
    try:
        for _ in range(90):
            if (HOME / "instance.json").exists() and find("ORION Notch"):
                break
            time.sleep(1)
        else:
            record("launch", False, "the notch window never appeared")
            return finish()
        time.sleep(8)  # page load
        info = json.loads((HOME / "instance.json").read_text())
        c = httpx.Client(base_url=f"http://127.0.0.1:{info['port']}", headers={"x-orion-token": info["token"]}, timeout=30)
        hwnd = find("ORION Notch")
        np = 0
        for _ in range(20):
            np = int(user32.FindWindowW("Notepad", None) or 0)
            if np:
                break
            time.sleep(0.5)
        user32.SetForegroundWindow(np)  # lab runner: put Notepad in front, like an app you're typing in
        time.sleep(1)

        check_geometry(hwnd, "idle")
        exstyle = user32.GetWindowLongW(hwnd, -20)
        record("idle: can't take focus (WS_EX_NOACTIVATE)", bool(exstyle & 0x08000000), f"exstyle={exstyle:#x}")
        record("notch has no taskbar button of its own (tool window)",
               bool(exstyle & 0x80) and not exstyle & 0x40000, f"exstyle={exstyle:#x}")
        shot("1-idle")

        # One app: with the main window open, ORION has exactly one taskbar button.
        c.post("/api/window/show").raise_for_status()
        time.sleep(3)
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        pid = owner.value  # ORION's process (the launcher may have handed off to it)
        buttons = taskbar_windows(pid)
        record("one taskbar button with the window open", buttons == ["ORION"], f"taskbar windows={buttons}")
        shot("0-one-app", full=True)
        # Closing the main window keeps ORION and its notch running.
        main_hwnd = find("ORION")
        user32.PostMessageW(main_hwnd, 0x0010, 0, 0)  # WM_CLOSE, like the X button
        time.sleep(3)
        record("closing the window keeps ORION running", bool(user32.IsWindow(hwnd)) and bool(user32.IsWindow(main_hwnd)),
               "")
        record("closing the window hides it", not user32.IsWindowVisible(main_hwnd), "")
        record("closing the window keeps the notch on screen", bool(user32.IsWindowVisible(hwnd)), "")
        left = taskbar_windows(pid)
        record("no taskbar button left after closing", left == [], f"taskbar windows={left}")
        shot("0-closed-notch-stays", full=True)
        user32.SetForegroundWindow(np)
        time.sleep(1)

        for mode in ("peek", "open"):
            c.post(f"/api/window/notch-{mode}").raise_for_status()
            time.sleep(1.5)
            check_geometry(hwnd, mode)
            fg = user32.GetForegroundWindow()
            record(f"{mode}: focus stays in Notepad", fg == np, f"foreground={title_of(fg)!r}")
            shot(f"2-{mode}")
        c.post("/api/window/notch-idle").raise_for_status()
        time.sleep(1.5)
        check_geometry(hwnd, "idle")

        # The hotkey (same action as Alt+Space): you asked, so the notch opens and takes the keyboard.
        c.post("/api/window/quick").raise_for_status()
        time.sleep(2.5)
        check_geometry(hwnd, "open")
        fg = user32.GetForegroundWindow()
        record("hotkey: opens and takes focus", fg == hwnd, f"foreground={title_of(fg)!r}")
        shot("3-hotkey-open")
        c.post("/api/window/quick").raise_for_status()
        time.sleep(2.5)
        check_geometry(hwnd, "idle")
        exstyle = user32.GetWindowLongW(hwnd, -20)
        record("closed again: back to no-focus", bool(exstyle & 0x08000000), f"exstyle={exstyle:#x}")
        shot("4-closed")
        return finish()
    finally:
        for p in (proc, notepad):
            p.terminate()
            try:
                p.wait(15)
            except Exception:
                p.kill()


def finish() -> int:
    (OUT / "report.json").write_text(json.dumps(results, indent=2))
    failed = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
