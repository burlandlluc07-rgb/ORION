"""System-wide hotkeys.

On Windows we use the native RegisterHotKey API: the shortcut is captured
before other apps see it (so Alt+Space doesn't also open a window menu) and
it costs nothing while idle. Elsewhere we fall back to pynput.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

log = logging.getLogger("orion.hotkeys")

MODS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008, "cmd": 0x0008}
VKS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def parse(combo: str) -> tuple[int, int]:
    """'<ctrl>+<alt>+j' → (modifiers, virtual key)."""
    mods, vk = 0, 0
    for raw in combo.lower().split("+"):
        key = raw.strip().strip("<>")
        if key in MODS:
            mods |= MODS[key]
        elif key in VKS:
            vk = VKS[key]
        elif len(key) == 1:
            vk = ord(key.upper())
    if not vk:
        raise ValueError(f"Unsupported hotkey: {combo}")
    return mods, vk


class Hotkeys:
    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        self.bindings = {k: v for k, v in bindings.items() if k}
        self._thread: threading.Thread | None = None
        self._listener = None
        self._tid = 0

    def start(self) -> None:
        if sys.platform == "win32":
            self._thread = threading.Thread(target=self._win_loop, name="hotkeys", daemon=True)
            self._thread.start()
        else:
            try:
                from pynput import keyboard

                self._listener = keyboard.GlobalHotKeys({k: v for k, v in self.bindings.items()})
                self._listener.start()
            except Exception as exc:
                log.info("Global hotkeys unavailable: %s", exc)

    def _win_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()  # type: ignore[attr-defined]
        actions = {}
        for i, (combo, fn) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse(combo)
            except ValueError as exc:
                log.warning("%s", exc)
                continue
            if user32.RegisterHotKey(None, i, mods | 0x4000, vk):  # MOD_NOREPEAT
                actions[i] = fn
            else:
                log.warning("Hotkey %s is already used by another program", combo)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in actions:  # WM_HOTKEY
                try:
                    actions[msg.wParam]()
                except Exception:
                    log.exception("Hotkey handler failed")
        for i in actions:
            user32.UnregisterHotKey(None, i)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        if sys.platform == "win32" and self._tid:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # type: ignore[attr-defined]  WM_QUIT
