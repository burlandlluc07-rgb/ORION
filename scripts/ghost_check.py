"""Windows check: ghost mode never touches the user's mouse, keyboard focus or foreground window.

A plain Notepad stands in for the user's own work (in front, with focus). ORION then opens a second Notepad as a
ghost, clicks and types into it with his ghost cursor, reads it with PrintWindow, survives the ghost trying to
grab focus, and finally hands it over. After every step the real cursor position and the foreground window must
be exactly what they were. Writes ghost/*.png and ghost/report.json.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["ORION_GHOST_SHOW_IN_CAPTURE"] = "1"  # so the screenshots show the cursor and frames

from PIL import Image, ImageGrab  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orion.ghost import core  # noqa: E402
from orion.ghost.win32 import DESK, capture, cursor_pos, give_focus_back, window_text  # noqa: E402

OUT = Path("ghost")
OUT.mkdir(exist_ok=True)
user32 = ctypes.windll.user32
results: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}", flush=True)


def shot(name: str) -> None:
    ImageGrab.grab(all_screens=True).save(OUT / f"{name}.png")


def child(hwnd: int, cls: str) -> int:
    return int(user32.FindWindowExW(hwnd, None, cls, None) or 0)


def untouched(step: str, user_win: int, pos: tuple[int, int]) -> None:
    fg, now = int(user32.GetForegroundWindow() or 0), cursor_pos()
    record(f"{step}: your window keeps focus", fg == user_win, f"foreground={fg} yours={user_win}")
    record(f"{step}: your mouse didn't move", now == pos, f"{now} vs {pos}")


def _rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def main() -> int:
    # The user's own work: a normal Notepad in front, with text in it.
    before = set()
    user = subprocess.Popen(["notepad.exe"])
    user_win = 0
    for _ in range(100):
        time.sleep(0.1)
        hwnd = int(user32.FindWindowW("Notepad", None) or 0)
        if hwnd:
            user_win = hwnd
            break
    if not user_win:
        record("user's Notepad opened", False, "no window")
        return 1
    before.add(user_win)
    user32.ShowWindow(user_win, 3)  # maximised, like real work
    user32.SetForegroundWindow(user_win)
    time.sleep(0.6)
    user32.SendMessageW(child(user_win, "Edit"), 0x000C, 0, ctypes.c_wchar_p("Lluc is typing his own notes here."))
    user32.SetCursorPos(400, 400)  # the lab itself parks the mouse somewhere known (this is the user, not ORION)
    time.sleep(0.3)
    pos = cursor_pos()
    record("lab ready", int(user32.GetForegroundWindow() or 0) == user_win, f"user window {user_win}")

    # 1. ORION opens his own Notepad as a ghost.
    t0 = time.monotonic()
    g = DESK.open("notepad.exe", "Notepad", ["notepad"])
    record("ghost window appears", bool(g and user32.IsWindow(g.hwnd)), f"{g.title} in {time.monotonic() - t0:.1f}s")
    time.sleep(0.8)
    ex = user32.GetWindowLongW(g.hwnd, -20) & 0xFFFFFFFF
    for bit, name in ((core.WS_EX_LAYERED, "see-through"), (core.WS_EX_TRANSPARENT, "click-through"),
                      (core.WS_EX_NOACTIVATE, "never focused"), (core.WS_EX_TOOLWINDOW, "not in the taskbar")):
        record(f"ghost is {name}", bool(ex & bit), hex(ex))
    alpha = ctypes.c_ubyte()
    flags = ctypes.c_uint()
    user32.GetLayeredWindowAttributes(g.hwnd, None, ctypes.byref(alpha), ctypes.byref(flags))
    record("ghost is about half see-through", 100 <= alpha.value <= 160, f"alpha {alpha.value}")
    record("ghost is a different window from yours", g.hwnd != user_win, str(g.hwnd))
    now = _rect_of(g.hwnd)
    o = g.original_rect
    record("ghost keeps the size and place the app chose", abs((now[2] - now[0]) - (o[2] - o[0])) <= 40 and
           abs(now[0] - o[0]) <= 40, f"{now} vs {o}")
    untouched("after opening", user_win, pos)
    shot("1-ghost-opened")

    # 2. He looks at it without bringing it forward.
    jpeg, size = DESK.look(g)
    (OUT / "2-ghost-capture.jpg").write_bytes(jpeg)
    img = Image.open(OUT / "2-ghost-capture.jpg").convert("L")
    record("ORION can see the ghost window (PrintWindow)", img.getextrema()[1] > 60, f"{size} extrema={img.getextrema()}")

    # 3. Click into the text area with the ghost cursor, then type.
    DESK.click(g, size[0] // 2, size[1] // 2)
    time.sleep(0.05)
    shot("3-ghost-cursor-click")
    untouched("after a ghost click", user_win, pos)
    DESK.type(g, "Hello from ORION's ghost.")
    DESK.press(g, "enter")
    DESK.type(g, "Second line, typed in the background.")
    time.sleep(0.6)
    ghost_edit = child(g.hwnd, "Edit") or child(g.hwnd, "RichEditD2DPT")
    text = window_text(ghost_edit) if ghost_edit else ""
    record("text reached the ghost", "Hello from ORION's ghost." in text and "Second line" in text, f"{text[:80]!r} via {g.flags.get('type_target')}")
    user_text = window_text(child(user_win, "Edit"))
    record("your Notepad wasn't typed into", user_text == "Lluc is typing his own notes here.", repr(user_text[:60]))
    untouched("after ghost typing", user_win, pos)
    shot("4-ghost-typed")

    # 4. The ghost tries to grab the foreground (some apps do this to themselves): ORION hands it back.
    user32.SetForegroundWindow(g.hwnd)
    time.sleep(0.8)
    fg = int(user32.GetForegroundWindow() or 0)
    record("a ghost that grabs focus gives it straight back", fg == user_win, f"foreground={fg}")
    if fg != user_win:
        give_focus_back(user_win)

    # 5. The user's clicks go through the ghost to their own work underneath.
    r = core.Rect(*g.rect)
    hit = int(user32.WindowFromPoint(ctypes.wintypes.POINT((r.left + r.right) // 2, (r.top + r.bottom) // 2)) or 0)
    root = int(user32.GetAncestor(hit, 2) or 0)
    record("clicks pass through the ghost", root != g.hwnd, f"window under the ghost: {root}")

    # 6. Take over: it becomes a normal window, yours.
    DESK.take_over(g)
    time.sleep(0.8)
    ex2 = user32.GetWindowLongW(g.hwnd, -20) & 0xFFFFFFFF
    record("take over makes it a normal window", not ex2 & (core.WS_EX_TRANSPARENT | core.WS_EX_NOACTIVATE | core.WS_EX_TOOLWINDOW),
           hex(ex2))
    shot("5-taken-over")

    # 7. ORION's ordinary "open app" tool opens a ghost too, unless asked for a normal window.
    from orion.tools.apps import open_app

    before = set(DESK.ghosts)
    yours = int(user32.GetForegroundWindow() or 0)  # the Notepad just taken over is the user's window now
    out = open_app("notepad")
    time.sleep(0.8)
    new = [h for h in DESK.ghosts if h not in before]
    ex3 = user32.GetWindowLongW(new[0], -20) & 0xFFFFFFFF if new else 0
    record("open_app opens a ghost by default", bool(new) and bool(ex3 & core.WS_EX_TRANSPARENT), out[:80])
    untouched("after open_app", yours, pos)
    for h in new:
        user32.PostMessageW(h, 0x0010, 0, 0)

    record("your mouse never moved, start to finish", cursor_pos() == pos, f"{cursor_pos()} vs {pos}")
    for h in (g.hwnd, user_win):
        user32.PostMessageW(h, 0x0010, 0, 0)
    time.sleep(0.5)
    for p in ("notepad.exe",):
        subprocess.run(["taskkill", "/f", "/im", p], capture_output=True)
    user.wait(timeout=5)
    (OUT / "report.json").write_text(json.dumps(results, indent=2))
    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
