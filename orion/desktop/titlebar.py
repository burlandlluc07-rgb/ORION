"""Paints the native Windows title bar in ORION's own black/white/grey so it no longer shows as a white strip.

Uses DWM window attributes, so minimize/maximize/close, dragging, snapping and resizing stay native.
Windows 11 takes the exact caption colour; Windows 10 (1809+) only switches between its dark and light bar.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

log = logging.getLogger("orion.titlebar")

# Match ui/src/tokens.css: --bg-2 (sidebar) for the bar, --text for the title.
DARK = {"caption": "#0e0e0e", "text": "#ededed"}
LIGHT = {"caption": "#f3f3f2", "text": "#1a1a1a"}

DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 1809-1909
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def system_prefers_dark() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return True


def is_dark(theme: str | None) -> bool:
    """Same rule as useTheme in ui/src/App.tsx: dark unless "light", or "system" with Windows in light mode."""
    if theme == "light":
        return False
    if theme == "system":
        return system_prefers_dark()
    return True


def _colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def find_window(title: str) -> int:
    """This process's top-level window with exactly this title (0 if none)."""
    if sys.platform != "win32":
        return 0
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid, found = os.getpid(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    return int(found[0]) if found else 0


def style(hwnd: int, dark: bool) -> bool:
    """Colour the caption of `hwnd`. Returns True if Windows accepted at least the dark/light switch."""
    if sys.platform != "win32" or not hwnd:
        return False
    dwm = ctypes.windll.dwmapi
    hwnd_p = ctypes.c_void_p(hwnd)

    def put(attr: int, value: int) -> bool:
        v = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(hwnd_p, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0

    ok = put(DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark)) or put(DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, int(dark))
    colors = DARK if dark else LIGHT
    # Windows 11 (22000+) only; older builds reject these and keep the dark/light bar above.
    put(DWMWA_CAPTION_COLOR, _colorref(colors["caption"]))
    put(DWMWA_TEXT_COLOR, _colorref(colors["text"]))
    put(DWMWA_BORDER_COLOR, _colorref(colors["caption"]))
    # Windows 10 repaints the non-client area only when told to.
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
    ctypes.windll.user32.SetWindowPos(hwnd_p, None, 0, 0, 0, 0,
                                      SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    if not ok:
        log.info("Windows refused the dark title bar attribute")
    return ok
