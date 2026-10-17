"""The package must never take over the desktop: no mouse, keyboard, focus
or window automation anywhere in the code."""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "orion" / "background"

FORBIDDEN = [
    r"\bpyautogui\b", r"\bpynput\b", r"\bpywinauto\b", r"\bkeyboard\.[a-z_]+\(", r"\bmouse\.[a-z_]+\(",
    r"SendInput", r"keybd_event", r"mouse_event", r"SetForegroundWindow", r"SetFocus\b",
    r"SetWindowPos", r"ShowWindow\(", r"os\.startfile", r"SetCursorPos", r"BringWindowToTop",
]


def test_no_desktop_takeover_calls():
    hits = []
    for path in PKG.glob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pat in FORBIDDEN:
                if re.search(pat, line):
                    hits.append(f"{path.name}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_child_processes_have_no_window():
    tools = (PKG / "tools.py").read_text()
    assert "CREATE_NO_WINDOW" in tools and "SW_HIDE" in tools and "stdin" in tools
