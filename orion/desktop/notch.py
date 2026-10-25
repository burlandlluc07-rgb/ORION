"""The notch: a small black pill at the top centre of the screen that stays out of the way until you need ORION.

The page (ui/src/views/NotchView.tsx) decides the mode and animates the pill; this module only sizes and places
the native window for each mode, and switches whether clicking it may take keyboard focus.

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

# Window size per mode (logical px). The pill inside is a little smaller, so it has room to grow on hover and
# the rounded corners sit on a transparent background.
SIZES: dict[str, tuple[int, int]] = {
    "idle": (220, 44),
    "peek": (460, 72),
    "open": (700, 500),
}

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000

# SHQueryUserNotificationState: a full-screen game, video or presentation is in front.
QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE = 2, 3, 4


def size_for(mode: str) -> tuple[int, int]:
    return SIZES.get(mode, SIZES["idle"])


def place(mode: str, screen_x: int, screen_y: int, screen_w: int) -> tuple[int, int, int, int]:
    """(x, y, width, height) for the window: horizontally centred, flush with the top of the screen."""
    w, h = size_for(mode)
    return int(screen_x + (screen_w - w) / 2), int(screen_y), w, h


def apply(hwnd: int, mode: str) -> bool:
    """Size and place the native window for `mode` on the primary screen without activating it (Windows only).

    pywebview's own resize/move/show use SetWindowPos/Activate calls that can pull focus, which is exactly what the
    notch must never do, so on Windows we do it here with SWP_NOACTIVATE. Returns False where that isn't possible.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    try:
        scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
    except Exception:
        scale = 1.0
    w, h = (round(v * scale) for v in size_for(mode))
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
