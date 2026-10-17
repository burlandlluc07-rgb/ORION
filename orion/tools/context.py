"""What you're looking at: the active window and the text selected in it, so "summarise this" or "translate the
selected text" just work.

A light background thread remembers the last window you used that isn't ORION itself (cheap: one Win32 call a
second), so asking from ORION's own window still refers to the document you were in. The selection is read on
demand through Windows UI Automation (works in Notepad, Word, browsers, most modern apps); nothing is copied, no
keys are pressed, and the clipboard is left alone.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time

from .platform import IS_WIN
from .registry import tool

log = logging.getLogger("orion.context")

UIA_TEXT_PATTERN = 10014
UIA_IS_TEXT_PATTERN_AVAILABLE = 30040
TREESCOPE_DESCENDANTS = 4
MAX_SELECTION = 6000

DEICTIC = re.compile(r"\b(this|these|that one|selected|selection|highlighted|on (?:my |the )?screen|current "
                     r"(?:window|page|document|tab|file)|what i'?m (?:looking at|reading|working on))\b", re.I)


class Foreground:
    """Remembers the most recent foreground window that belongs to another app."""

    def __init__(self) -> None:
        self.hwnd = 0
        self.title = ""
        self.app = ""
        self.at = 0.0
        self._own = {os.getpid()}
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        if not IS_WIN or self._started:
            return
        self._started = True
        threading.Thread(target=self._loop, name="foreground", daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                self.poll()
            except Exception as exc:  # pragma: no cover - Windows only
                log.debug("foreground poll failed: %s", exc)
            time.sleep(1.0)

    def poll(self) -> None:
        import ctypes
        from ctypes import wintypes

        import psutil

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in self._own or self._is_own(pid.value):
            return
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title:
            return
        try:
            app = psutil.Process(pid.value).name().rsplit(".", 1)[0]
        except Exception:
            app = ""
        if app.lower() in ("explorer", "searchhost", "shellexperiencehost", "startmenuexperiencehost") \
                and title in ("Program Manager", "Search", "Start"):
            return  # the desktop/taskbar/start menu isn't something you'd call "this"
        with self._lock:
            self.hwnd, self.title, self.app, self.at = hwnd, title, app, time.time()

    def _is_own(self, pid: int) -> bool:
        """ORION's window is drawn by WebView2 child processes of ORION: those count as "us" (apps ORION launched
        don't)."""
        import psutil

        try:
            proc = psutil.Process(pid)
            if proc.name().lower() != "msedgewebview2.exe":
                return False
            if any(parent.pid in self._own for parent in proc.parents()):
                self._own.add(pid)
                return True
        except Exception:
            pass
        return False


FOREGROUND = Foreground()


def _uia():
    import comtypes  # type: ignore

    try:
        comtypes.CoInitialize()  # tools run on worker threads
    except OSError:
        pass
    from pywinauto.uia_defines import IUIA  # type: ignore  (same UI Automation setup the window tools use)

    return IUIA().iuia


def _selection_from(element) -> str:
    from pywinauto.uia_defines import IUIA  # type: ignore

    pat = element.GetCurrentPattern(UIA_TEXT_PATTERN)
    if not pat:
        return ""
    tp = pat.QueryInterface(IUIA().UIA_dll.IUIAutomationTextPattern)
    ranges = tp.GetSelection()
    if not ranges:
        return ""
    parts = [ranges.GetElement(i).GetText(MAX_SELECTION) for i in range(min(ranges.Length, 5))]
    return "\n".join(p for p in parts if p).strip()


def read_selection(hwnd: int, errors: list | None = None) -> str:
    """Selected text in that window: its focused control first, then its text controls."""
    if not IS_WIN or not hwnd:
        return ""

    def note(where: str, exc: Exception) -> None:
        if errors is not None:
            errors.append(f"{where}: {type(exc).__name__}: {exc}")

    uia = _uia()
    try:
        focused = uia.GetFocusedElement()
        if focused is not None and focused.CurrentNativeWindowHandle and \
                _top_level(focused.CurrentNativeWindowHandle) == hwnd:
            text = _selection_from(focused)
            if text:
                return text
    except Exception as exc:
        note("focused", exc)
    try:
        root = uia.ElementFromHandle(hwnd)
        cond = uia.CreatePropertyCondition(UIA_IS_TEXT_PATTERN_AVAILABLE, True)
        found = root.FindAll(TREESCOPE_DESCENDANTS, cond)
        for i in range(min(found.Length if found else 0, 8)):
            try:
                text = _selection_from(found.GetElement(i))
                if text:
                    return text
            except Exception as exc:
                note(f"descendant {i}", exc)
    except Exception as exc:
        note("search", exc)
    try:  # pywinauto's wrappers, as a second route to the same UI Automation data
        from pywinauto import Desktop  # type: ignore

        win = Desktop(backend="uia").window(handle=hwnd)
        for ctl in win.descendants(control_type="Edit")[:4] + win.descendants(control_type="Document")[:4]:
            try:
                ranges = ctl.iface_text.GetSelection()
                parts = [ranges.GetElement(i).GetText(MAX_SELECTION) for i in range(min(ranges.Length, 5))]
                text = "\n".join(p for p in parts if p).strip()
                if text:
                    return text
            except Exception as exc:
                note("wrapper", exc)
    except Exception as exc:
        note("wrappers", exc)
    return ""


def _top_level(hwnd: int) -> int:
    import ctypes

    return ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd  # type: ignore[attr-defined]  # GA_ROOT


def _clipboard() -> str:
    try:
        import pyperclip

        return (pyperclip.paste() or "").strip()
    except Exception:
        return ""


def snapshot(selection: bool = True) -> dict:
    """{"app", "title", "selection", "clipboard", "seconds_ago"} for the window you were last using."""
    FOREGROUND.start()
    if IS_WIN:
        try:
            FOREGROUND.poll()  # in case it changed within the last second
        except Exception:
            pass
    out = {"app": FOREGROUND.app, "title": FOREGROUND.title, "selection": "", "clipboard": _clipboard()[:2000],
           "seconds_ago": round(time.time() - FOREGROUND.at) if FOREGROUND.at else None}
    if selection and FOREGROUND.hwnd:
        try:
            out["selection"] = read_selection(FOREGROUND.hwnd)[:MAX_SELECTION]
        except Exception as exc:
            log.debug("couldn't read the selection: %s", exc)
    return out


def describe(snap: dict) -> str:
    lines = []
    if snap.get("title"):
        lines.append(f"Active window: {snap['title']}" + (f" ({snap['app']})" if snap.get("app") else ""))
    if snap.get("selection"):
        lines.append(f"Selected text:\n\"\"\"\n{snap['selection']}\n\"\"\"")
    if snap.get("clipboard") and snap.get("clipboard") != snap.get("selection"):
        lines.append(f"Clipboard:\n\"\"\"\n{snap['clipboard'][:1500]}\n\"\"\"")
    return "\n".join(lines)


def context_for(text: str) -> str:
    """The "CURRENT CONTEXT" block for a request that points at something ("this", "the selected text"), else ""."""
    if not DEICTIC.search(text or ""):
        return ""
    try:
        desc = describe(snapshot())
    except Exception:
        return ""
    if not desc:
        return ""
    return ("CURRENT CONTEXT — what the user was just looking at (\"this\"/\"that\"/\"the selected text\" refer to "
            "it; prefer the selected text, then the window; say so if it looks unrelated):\n" + desc)


@tool(category="vision")
def current_context() -> str:
    """See what the user is looking at right now: the active window's title and app, the text they've selected in
    it, and the clipboard. Use it whenever they say "this", "that", "the selected text", "what I'm reading"."""
    desc = describe(snapshot())
    return desc or "Nothing to go on: no other window has been active and nothing is selected or copied."
