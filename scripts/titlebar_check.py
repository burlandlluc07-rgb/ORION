"""Windows check: ORION's native title bar follows the app theme and keeps its window controls.

Launches `python -m orion` with a real window, screenshots the window in dark and light theme, samples the
caption colour and checks minimize/maximize/close and resizing are still there. Writes titlebar/*.png and
titlebar/report.json, and prints the PNGs as base64 log lines ("TBIMG|name|chunk") so they
can be recovered from the job log. `--before` only takes the screenshot (for the unfixed build) and never fails.
"""

from __future__ import annotations

import base64
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

HOME = Path(os.environ.get("ORION_HOME") or Path(os.environ["RUNNER_TEMP"]) / "orion-titlebar")
OUT = Path("titlebar")
OUT.mkdir(exist_ok=True)
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
results: list[dict] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append({"name": name, "ok": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}", flush=True)


def orion_hwnd() -> int:
    return int(user32.FindWindowW(None, "ORION") or 0)


def rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def caption_shot(name: str) -> tuple[int, int, int]:
    """Screenshot the window and return the colour of the empty part of its caption."""
    hwnd = orion_hwnd()
    user32.SetForegroundWindow(hwnd)  # a lab runner, not Lluc's desktop: bring it up so the grab shows it
    time.sleep(1.5)
    left, top, right, bottom = rect(hwnd)
    img = ImageGrab.grab(bbox=(left, top, right, min(bottom, top + 420)), all_screens=True).convert("RGB")
    img.save(OUT / f"{name}.png")
    # Halfway between the title text and the minimize button, a few px into the caption.
    px = img.getpixel((int(img.width * 0.55), 14))
    print(f"{name}: caption pixel {px}, window {right - left}x{bottom - top}", flush=True)
    return px


def main() -> int:
    before = "--before" in sys.argv
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "settings.json").write_text(json.dumps({"user_name": "Lluc", "onboarded": True, "theme": "dark"}))
    env = {**os.environ, "ORION_HOME": str(HOME), "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY") or "dummy"}
    proc = subprocess.Popen([sys.executable, "-m", "orion"], env=env)
    try:
        for _ in range(90):
            if (HOME / "instance.json").exists() and orion_hwnd():
                break
            time.sleep(1)
        else:
            record("launch", False, "ORION window never appeared")
            return finish(before)
        time.sleep(8)  # UI load
        info = json.loads((HOME / "instance.json").read_text())
        c = httpx.Client(base_url=f"http://127.0.0.1:{info['port']}", headers={"x-orion-token": info["token"]}, timeout=30)

        dark_px = caption_shot("before-dark" if before else "after-dark")
        if before:
            record("before: screenshot taken", True, f"caption pixel {dark_px}")
            return finish(before)
        record("dark theme: caption is dark", max(dark_px) < 48, f"caption pixel {dark_px}")

        c.patch("/api/settings", json={"theme": "light"}).raise_for_status()
        time.sleep(1.5)
        light_px = caption_shot("after-light")
        record("light theme: caption is light", min(light_px) > 200, f"caption pixel {light_px}")
        c.patch("/api/settings", json={"theme": "dark"}).raise_for_status()
        time.sleep(1.5)
        back_px = caption_shot("after-dark-again")
        record("switching back to dark", max(back_px) < 48, f"caption pixel {back_px}")

        hwnd = orion_hwnd()
        style = user32.GetWindowLongW(hwnd, -16)
        need = {"caption": 0x00C00000, "system menu (close)": 0x00080000, "resize border": 0x00040000,
                "minimize": 0x00020000, "maximize": 0x00010000}
        missing = [k for k, bit in need.items() if not style & bit]
        record("window controls and resize border kept", not missing, f"missing: {missing or 'none'}")

        c.post("/api/window/maximize")
        time.sleep(1.5)
        maxed = bool(user32.IsZoomed(hwnd))
        caption_shot("after-dark-maximized")
        c.post("/api/window/minimize")
        time.sleep(1.5)
        mini = bool(user32.IsIconic(hwnd))
        c.post("/api/window/show")
        time.sleep(1.5)
        record("maximize / minimize / restore", maxed and mini and not user32.IsIconic(hwnd),
               f"maximized={maxed} minimized={mini}")
        return finish(before)
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except Exception:
            proc.kill()


def finish(before: bool) -> int:
    (OUT / ("report-before.json" if before else "report.json")).write_text(json.dumps(results, indent=2))
    for png in sorted(OUT.glob("*.png")):
        if png.stem.startswith("before") != before:
            continue
        data = base64.b64encode(png.read_bytes()).decode()
        for i in range(0, len(data), 3500):
            print(f"TBIMG|{png.stem}|{data[i:i + 3500]}")
    failed = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} passed", flush=True)
    return 0 if before or not failed else 1


if __name__ == "__main__":
    sys.exit(main())
