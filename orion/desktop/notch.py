"""The notch: a small black pill at the top centre of the screen that stays out of the way until you need ORION.

The page (ui/src/views/NotchView.tsx) decides the mode; this module sizes and places the native window for it and,
on Windows, clips the window to the pill's rounded shape with a window region. Webview transparency doesn't work on
Windows (the corners showed as a grey box), and a region also makes everything outside the pill click-through.
The region morphs between sizes with a small spring, so the shape animates natively.

Background-first: the pill is created with WS_EX_NOACTIVATE, so showing it, growing it for a status or an
approval never steals focus from the app you're in. It takes focus only when *you* open it (click or hotkey),
and gives it back as soon as it closes.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("orion.notch")

TITLE = "ORION Notch"

# Window size per mode (logical px). The pill inside is a little smaller, so it has room to grow on hover.
SIZES: dict[str, tuple[int, int]] = {
    "idle": (220, 44),
    "peek": (460, 72),
    "open": (700, 500),
}
# The visible pill per mode: width, height, bottom corner radius. Must match .notch-pill in ui/src/styles.css.
# "hover" is the idle window with a slightly bigger pill.
PILLS: dict[str, tuple[int, int, int]] = {
    "idle": (148, 30, 16),
    "hover": (200, 38, 19),
    "peek": (440, 58, 24),
    "open": (680, 480, 28),
}
MORPH_SECONDS = 0.34

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000

# SHQueryUserNotificationState: a full-screen game, video or presentation is in front.
QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE = 2, 3, 4


def size_for(mode: str) -> tuple[int, int]:
    return SIZES.get("idle" if mode == "hover" else mode, SIZES["idle"])


def pill_for(mode: str) -> tuple[int, int, int]:
    return PILLS.get(mode, PILLS["idle"])


def ease_out_back(t: float) -> float:
    """0→1 with a small overshoot, like the CSS spring on the pill."""
    c1 = 0.6
    return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2


def morph_frames(start: tuple[int, int, int], end: tuple[int, int, int], steps: int) -> list[tuple[int, int, int]]:
    """Pill sizes from `start` to `end` (inclusive of end), eased. Never below 1 px."""
    frames = []
    for i in range(1, steps + 1):
        k = ease_out_back(i / steps)
        frames.append(tuple(max(1, round(a + (b - a) * k)) for a, b in zip(start, end)))  # type: ignore[misc]
    frames[-1] = end
    return frames


def _scale(hwnd: int) -> float:
    try:
        return (ctypes.windll.user32.GetDpiForWindow(hwnd) or 96) / 96  # type: ignore[attr-defined]
    except Exception:
        return 1.0


def set_shape(hwnd: int, mode_or_pill: str | tuple[int, int, int], window: tuple[int, int] | None = None) -> bool:
    """Clip the window to the pill: centred, flush with the top, rounded only at the bottom (Windows only)."""
    if sys.platform != "win32" or not hwnd:
        return False
    pw, ph, r = pill_for(mode_or_pill) if isinstance(mode_or_pill, str) else mode_or_pill
    scale = _scale(hwnd)
    rect = wintypes_rect(hwnd)
    win_w = rect[2] - rect[0] if window is None else round(window[0] * scale)
    pw, ph, r = round(pw * scale), round(ph * scale), round(r * scale)
    x0 = (win_w - pw) // 2
    # Start the rounded rect above the window so only the bottom corners are round.
    rgn = ctypes.windll.gdi32.CreateRoundRectRgn(x0, -2 * r, x0 + pw + 1, ph + 1, 2 * r, 2 * r)  # type: ignore[attr-defined]
    return bool(ctypes.windll.user32.SetWindowRgn(ctypes.c_void_p(hwnd), rgn, True))  # type: ignore[attr-defined]


def wintypes_rect(hwnd: int) -> tuple[int, int, int, int]:
    from ctypes import wintypes

    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))  # type: ignore[attr-defined]
    return r.left, r.top, r.right, r.bottom


def morph(hwnd: int, start: str, end: str, alive=lambda: True) -> None:
    """Grow or shrink between modes: a bigger window first, then the region springs to the new pill, then the window
    settles at its new size. `alive()` going False stops a morph that a newer one replaced."""
    import time

    if sys.platform != "win32" or not hwnd:
        return
    (sw, sh), (ew, eh) = size_for(start), size_for(end)
    big = (max(sw, ew), max(sh, eh))
    if big != (sw, sh):
        apply(hwnd, end if (ew, eh) == big else start, size=big)
        set_shape(hwnd, start, window=big)
    steps = max(2, int(MORPH_SECONDS * 60))
    for frame in morph_frames(pill_for(start), pill_for(end), steps):
        if not alive():
            return
        set_shape(hwnd, frame, window=big)
        time.sleep(MORPH_SECONDS / steps)
    if alive():
        apply(hwnd, end)
        set_shape(hwnd, end, window=(ew, eh))


def place(mode: str, screen_x: int, screen_y: int, screen_w: int) -> tuple[int, int, int, int]:
    """(x, y, width, height) for the window: horizontally centred, flush with the top of the screen."""
    w, h = size_for(mode)
    return int(screen_x + (screen_w - w) / 2), int(screen_y), w, h


def apply(hwnd: int, mode: str, size: tuple[int, int] | None = None) -> bool:
    """Size and place the native window for `mode` on the primary screen without activating it (Windows only).

    pywebview's own resize/move/show use SetWindowPos/Activate calls that can pull focus, which is exactly what the
    notch must never do, so on Windows we do it here with SWP_NOACTIVATE. Returns False where that isn't possible.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    scale = _scale(hwnd)
    w, h = (round(v * scale) for v in (size or size_for(mode)))
    screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN: the primary screen, in physical pixels
    HWND_TOPMOST, SWP_NOACTIVATE, SWP_SHOWWINDOW = -1, 0x0010, 0x0040
    return bool(user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST), (screen_w - w) // 2, 0, w, h,
                                    SWP_NOACTIVATE | SWP_SHOWWINDOW))


def set_visible(hwnd: int, on: bool) -> bool:
    """Show without activating (SW_SHOWNOACTIVATE) or hide."""
    if sys.platform != "win32" or not hwnd:
        return False
    ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 4 if on else 0)  # type: ignore[attr-defined]
    return True


def set_activatable(hwnd: int, on: bool) -> None:
    """Let the pill take keyboard focus (on=True brings it to the front) or make it click-only again."""
    if sys.platform != "win32" or not hwnd:
        return
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = (style & ~WS_EX_NOACTIVATE) if on else (style | WS_EX_NOACTIVATE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    if on:
        user32.SetForegroundWindow(hwnd)


def fullscreen_app_in_front() -> bool:
    """True while a full-screen game, video or presentation is in front, so the pill can keep out of it."""
    if sys.platform != "win32":
        return False
    try:
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:  # type: ignore[attr-defined]
            return False
        return state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)
    except Exception:
        return False
