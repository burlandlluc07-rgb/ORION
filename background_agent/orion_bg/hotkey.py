"""Global hotkey (Windows): pressing it queues the text on the clipboard as a
task. It only listens (RegisterHotKey) and reads the clipboard; it never
sends keys, changes focus or opens a window.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

log = logging.getLogger("orion_bg.hotkey")

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
CF_UNICODETEXT = 13


def parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+alt+o' -> (modifier flags, virtual-key code)."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty hotkey")
    *mods, key = parts
    flags = MOD_NOREPEAT
    for m in mods:
        if m not in MODS:
            raise ValueError(f"Unknown modifier {m!r} in {combo!r}")
        flags |= MODS[m]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key == "space":
        vk = 0x20
    else:
        raise ValueError(f"Unsupported key {key!r} in {combo!r}")
    if flags == MOD_NOREPEAT:
        raise ValueError("A global hotkey needs at least one modifier")
    return flags, vk


def read_clipboard_text() -> str:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


class Hotkey:
    def __init__(self, combo: str, on_press: Callable[[], None]):
        self.combo = combo
        self.flags, self.vk = parse_combo(combo)
        self.on_press = on_press
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False

    def start(self) -> bool:
        if sys.platform != "win32":
            log.info("global hotkey is Windows only; skipped")
            return False
        self._thread = threading.Thread(target=self._loop, name="hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self.registered

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        self.registered = bool(user32.RegisterHotKey(None, 1, self.flags, self.vk))
        self._ready.set()
        if not self.registered:
            log.warning("could not register hotkey %s (another app may own it)", self.combo)
            return
        log.info("hotkey %s registered", self.combo)
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.on_press()
                    except Exception:
                        log.exception("hotkey handler failed")
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread_id and sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
