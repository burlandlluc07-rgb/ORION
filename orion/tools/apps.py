"""Launching, closing and driving desktop applications.

App discovery builds an index of Start Menu shortcuts and Store apps once in
the background, so "open spotify" resolves in milliseconds afterwards.
Window control uses Windows UI Automation (pywinauto) when available.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal

import psutil

from .platform import IS_LINUX, IS_MAC, IS_WIN, open_target, powershell
from .registry import ToolContext, ToolError, tool

# Friendly aliases → canonical index names or direct commands.
ALIASES: dict[str, str] = {
    "chrome": "google chrome", "google": "google chrome", "edge": "microsoft edge", "firefox": "firefox",
    "vscode": "visual studio code", "vs code": "visual studio code", "code": "visual studio code",
    "word": "word", "excel": "excel", "powerpoint": "powerpoint", "outlook": "outlook",
    "teams": "microsoft teams", "discord": "discord", "spotify": "spotify", "steam": "steam",
    "whatsapp": "whatsapp", "telegram": "telegram", "obs": "obs studio", "notion": "notion",
}
WIN_BUILTINS: dict[str, str] = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe", "paint": "mspaint.exe",
    "file explorer": "explorer.exe", "explorer": "explorer.exe", "files": "explorer.exe",
    "task manager": "taskmgr.exe", "command prompt": "cmd.exe", "cmd": "cmd.exe",
    "powershell": "powershell.exe", "terminal": "wt.exe", "control panel": "control.exe",
    "settings": "ms-settings:", "snipping tool": "snippingtool.exe", "registry editor": "regedit.exe",
    "camera": "microsoft.windows.camera:", "clock": "ms-clock:", "store": "ms-windows-store:",
}
SETTINGS_PAGES: dict[str, str] = {
    "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi", "network": "ms-settings:network",
    "display": "ms-settings:display", "sound": "ms-settings:sound", "updates": "ms-settings:windowsupdate",
    "apps": "ms-settings:appsfeatures", "battery": "ms-settings:batterysaver", "storage": "ms-settings:storagesense",
    "privacy": "ms-settings:privacy", "personalization": "ms-settings:personalization",
    "notifications": "ms-settings:notifications", "power": "ms-settings:powersleep",
}


class AppIndex:
    def __init__(self) -> None:
        self.apps: dict[str, str] = {}  # lowercase display name → launch target
        self.ready = threading.Event()
        self._lock = threading.Lock()

    def build_async(self) -> None:
        threading.Thread(target=self.build, name="app-index", daemon=True).start()

    def build(self) -> None:
        found: dict[str, str] = {}
        try:
            if IS_WIN:
                found.update(self._win_shortcuts())
                found.update(self._win_store_apps())
            elif IS_LINUX:
                found.update(self._linux_desktop_files())
            elif IS_MAC:
                for root in ("/Applications", str(Path.home() / "Applications")):
                    for p in Path(root).glob("*.app"):
                        found[p.stem.lower()] = str(p)
        finally:
            with self._lock:
                self.apps = found
            self.ready.set()

    @staticmethod
    def _win_shortcuts() -> dict[str, str]:
        roots = [
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
            Path.home() / "Desktop",
        ]
        out: dict[str, str] = {}
        skip = re.compile(r"uninstall|readme|help|documentation|website|release notes", re.I)
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if p.suffix.lower() in (".lnk", ".url", ".appref-ms") and not skip.search(p.stem):
                    out.setdefault(p.stem.lower(), str(p))
        return out

    @staticmethod
    def _win_store_apps() -> dict[str, str]:
        try:
            raw = powershell("Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }", timeout=25)
        except Exception:
            return {}
        out = {}
        for line in raw.splitlines():
            if "|" in line:
                name, appid = line.rsplit("|", 1)
                out.setdefault(name.strip().lower(), f"shell:AppsFolder\\{appid.strip()}")
        return out

    @staticmethod
    def _linux_desktop_files() -> dict[str, str]:
        out = {}
        for root in ("/usr/share/applications", str(Path.home() / ".local/share/applications")):
            for p in Path(root).glob("*.desktop"):
                try:
                    m = re.search(r"^Name=(.+)$", p.read_text(errors="ignore"), re.M)
                except OSError:
                    continue
                if m:
                    out.setdefault(m.group(1).strip().lower(), p.stem)
        return out

    def find(self, query: str) -> tuple[str, str] | None:
        q = query.strip().lower()
        q = ALIASES.get(q, q)
        self.ready.wait(timeout=8)
        with self._lock:
            apps = dict(self.apps)
        if q in apps:
            return q, apps[q]
        starts = [n for n in apps if n.startswith(q)]
        if starts:
            best = min(starts, key=len)
            return best, apps[best]
        contains = [n for n in apps if q in n]
        if contains:
            best = min(contains, key=len)
            return best, apps[best]
        close = difflib.get_close_matches(q, list(apps), n=1, cutoff=0.72)
        return (close[0], apps[close[0]]) if close else None

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self.apps)


INDEX = AppIndex()


def launch(target: str) -> None:
    if IS_WIN:
        if target.startswith("shell:AppsFolder"):
            subprocess.Popen(["explorer.exe", target])
        else:
            os.startfile(target)  # type: ignore[attr-defined]
    elif IS_LINUX and not os.path.sep in target and shutil.which("gtk-launch"):
        subprocess.Popen(["gtk-launch", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif IS_MAC:
        subprocess.Popen(["open", target])
    else:
        open_target(target)


@tool(category="apps", summary=lambda a: f"Open {a.get('name')}")
def open_app(name: str) -> str:
    """Open (launch) an installed application by name, e.g. "spotify", "chrome", "notepad", "settings".

    Args:
        name: The app's name as the user said it.
    """
    q = name.strip().lower()
    if IS_WIN:
        if q in WIN_BUILTINS:
            launch(WIN_BUILTINS[q])
            return f"Opened {name}."
        for key, uri in SETTINGS_PAGES.items():
            if q in (f"{key} settings", f"settings {key}"):
                launch(uri)
                return f"Opened {key} settings."
    hit = INDEX.find(q)
    if hit:
        display, target = hit
        launch(target)
        return f"Opened {display.title()}."
    exe = shutil.which(q) or shutil.which(q.replace(" ", ""))
    if exe:
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Started {name}."
    raise ToolError(f"Couldn't find an installed app called “{name}”. Try the exact name from the Start menu.")


@tool(category="apps")
def list_installed_apps(filter: str = "") -> list[str]:
    """List installed applications ORION can open.

    Args:
        filter: Optional text the app name must contain.
    """
    INDEX.ready.wait(timeout=8)
    names = [n for n in INDEX.names() if filter.lower() in n]
    return names[:80]


@tool(risk="confirm", category="apps", summary=lambda a: f"Close {a.get('name')}")
def close_app(name: str) -> str:
    """Close a running application gracefully (like clicking X).

    Args:
        name: App or process name, e.g. "spotify" or "chrome".
    """
    target = name.lower().removesuffix(".exe").replace(" ", "")
    hits = []
    for p in psutil.process_iter(["name"]):
        pname = (p.info["name"] or "").lower().removesuffix(".exe").replace(" ", "")
        if pname == target or (len(target) > 3 and target in pname):
            hits.append(p)
    if not hits:
        raise ToolError(f"{name} doesn't seem to be running.")
    for p in hits:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(hits, timeout=4)
    return f"Closed {name}."


@tool(category="apps", summary=lambda a: f"Open settings: {a.get('page')}")
def open_settings_page(page: Literal[tuple(SETTINGS_PAGES)]) -> str:  # type: ignore[valid-type]
    """Open a specific Windows Settings page.

    Args:
        page: Which page to open.
    """
    if not IS_WIN:
        raise ToolError("Settings pages are only available on Windows.")
    launch(SETTINGS_PAGES[page])
    return f"Opened {page} settings."


# ── Windows (UI Automation) ─────────────────────────────────────────────────
def _windows() -> list:
    if IS_WIN:
        from pywinauto import Desktop  # type: ignore

        return [w for w in Desktop(backend="uia").windows() if w.window_text().strip() and w.is_visible()]
    raise ToolError("Window control is only available on Windows.")


def _find_window(title: str):
    wins = _windows()
    t = title.lower()
    exact = [w for w in wins if w.window_text().lower() == t]
    part = exact or [w for w in wins if t in w.window_text().lower()]
    if not part:
        raise ToolError(f"No open window matching “{title}”. Use list_windows first.")
    return part[0]


@tool(category="apps")
def list_windows() -> list[str]:
    """List the titles of visible open windows."""
    return [w.window_text() for w in _windows()][:60]


SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE, WM_CLOSE = 4, 7, 0x0010


def _user32():
    import ctypes

    return ctypes.windll.user32  # type: ignore[attr-defined]


def _quiet_window_action(hwnd: int, action: str) -> None:
    """Minimize, restore or close a window with messages that don't activate it, so focus stays where it is."""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    if action == "close":
        if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            raise ToolError("Windows refused to pass the close request to that window.")
        return
    if action == "minimize":
        user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        return
    if user32.IsZoomed(hwnd):  # restore a maximized window to its normal size via its placement, not activating it

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                        ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                        ("rcNormalPosition", wintypes.RECT)]

        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            wp.showCmd = SW_SHOWNOACTIVATE
            user32.SetWindowPlacement(hwnd, ctypes.byref(wp))


def _window_state(hwnd: int) -> str:
    user32 = _user32()
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
        return "closed"
    return "minimized" if user32.IsIconic(hwnd) else "maximized" if user32.IsZoomed(hwnd) else "normal"


def _wait_state(hwnd: int, want: set[str], timeout: float = 3.0) -> str:
    deadline = time.monotonic() + timeout
    state = _window_state(hwnd)
    while state not in want and time.monotonic() < deadline:
        time.sleep(0.1)
        state = _window_state(hwnd)
    return state


@tool(category="apps", summary=lambda a: f"{a.get('action', 'focus').title()} window “{a.get('title')}”")
def window_action(title: str, action: Literal["focus", "minimize", "maximize", "restore", "close"] = "focus") -> str:
    """Focus, minimize, maximize, restore or close a window by (part of) its title.

    Minimize, restore and close happen in the background: the user's focus stays where it is. Focus and maximize
    bring the window to the front.

    Args:
        title: Part of the window title.
        action: What to do with it.
    """
    w = _find_window(title)
    name = w.window_text()
    hwnd = int(getattr(w, "handle", 0) or 0)
    if action in ("minimize", "restore", "close") and hwnd:
        _quiet_window_action(hwnd, action)
        state = _wait_state(hwnd, {"closed"} if action == "close" else {"minimized"} if action == "minimize"
                            else {"normal"})
        if action == "close":
            if state != "closed":
                return f"Asked “{name}” to close, but it is still open: it may be asking to save changes."
            return f"Closed “{name}”."
        if action == "minimize" and state != "minimized":
            raise ToolError(f"“{name}” didn't minimize (it is {state}).")
        if action == "restore" and state != "normal":
            raise ToolError(f"“{name}” didn't restore (it is {state}).")
        return f"{action.title()}d “{name}” without moving your focus."
    if action == "focus":
        if w.is_minimized():
            w.restore()
        w.set_focus()
        if hwnd and _user32().GetForegroundWindow() != hwnd:
            raise ToolError(f"Windows didn't let “{name}” come to the front.")
    else:
        getattr(w, action)()
        if hwnd and action == "maximize" and _wait_state(hwnd, {"maximized"}) != "maximized":
            raise ToolError(f"“{name}” didn't maximize.")
    return f"{action.title()}d “{name}”." if action != "close" else f"Closed “{name}”."


@tool(category="apps")
def inspect_window(title: str, max_controls: int = 60) -> list[dict]:
    """List the interactive controls (buttons, fields, tabs, menus) inside a window so you can operate it.

    Args:
        title: Part of the window title.
        max_controls: Limit on returned controls.
    """
    w = _find_window(title)
    keep = {"Button", "Edit", "TabItem", "MenuItem", "ListItem", "CheckBox", "RadioButton", "ComboBox",
            "Hyperlink", "TreeItem", "Document", "SplitButton"}
    out = []
    for c in w.descendants():
        try:
            ct = c.element_info.control_type
            name = c.window_text().strip()
            if ct in keep and (name or ct == "Edit"):
                out.append({"type": ct, "name": name[:80], "id": c.element_info.automation_id or ""})
        except Exception:
            continue
        if len(out) >= max_controls:
            break
    return out


def _find_control(title: str, control: str):
    w = _find_window(title)
    target = control.lower()
    best = None
    for c in w.descendants():
        try:
            name = c.window_text().strip().lower()
            aid = (c.element_info.automation_id or "").lower()
        except Exception:
            continue
        if name == target or aid == target:
            return c
        if best is None and target in name:
            best = c
    if best is None:
        raise ToolError(f"No control “{control}” in that window. Use inspect_window to see what's there.")
    return best


BM_CLICK, WM_SETTEXT = 0x00F5, 0x000C


def _background_click(c) -> bool:
    """Last tries before a real mouse click, none of which move the mouse or the focus: the control's accessible
    default action, then (for a classic Windows button) the click message Windows sends a button itself."""
    try:
        c.iface_legacy_iaccessible.DoDefaultAction()
        return True
    except Exception:
        pass
    hwnd = int(getattr(c, "handle", 0) or 0)
    if IS_WIN and hwnd and (c.class_name() or "").lower() == "button":
        try:
            _user32().SendMessageTimeoutW(hwnd, BM_CLICK, 0, 0, 0x2, 3000, None)
            return True
        except Exception:
            return False
    return False


def _background_set_text(c, text: str) -> bool:
    """Last tries before real typing: the accessible value, then (for a classic Windows edit box) a set-text
    message. Each is read back, so a field that ignores it falls through to the next."""
    def took() -> bool:
        try:
            return (c.window_text() or "") == text or c.legacy_properties().get("Value", "") == text
        except Exception:
            return False

    try:
        c.iface_legacy_iaccessible.SetValue(text)
        if took():
            return True
    except Exception:
        pass
    hwnd = int(getattr(c, "handle", 0) or 0)
    if IS_WIN and hwnd:
        try:
            import ctypes

            _user32().SendMessageTimeoutW(hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(text), 0x2, 3000, None)
            return took()
        except Exception:
            return False
    return False


@tool(risk="confirm", category="apps", summary=lambda a: f"Click “{a.get('control')}” in {a.get('title')}")
def click_control(title: str, control: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Click/activate a button, tab, menu item or list item inside a window (UI Automation, no mouse needed).

    Args:
        title: Part of the window title.
        control: The control's name or automation id (from inspect_window).
    """
    c = _find_control(title, control)
    for method in ("invoke", "select", "toggle", "expand"):  # UI Automation patterns: no mouse, no focus change
        fn = getattr(c, method, None)
        if fn:
            try:
                fn()
                return f"Activated “{c.window_text()}”."
            except Exception:
                continue
    if _background_click(c):
        return f"Activated “{c.window_text()}”."
    if ctx is not None and ctx.request_desktop("this control only responds to a real mouse click"):
        c.click_input()  # the user allowed this one foreground click
        return f"Clicked “{c.window_text()}” with the mouse (you allowed it)."
    raise ToolError("That control can't be activated in the background, and a real mouse click wasn't allowed.")


@tool(risk="confirm", category="apps", summary=lambda a: f"Type into “{a.get('control')}” in {a.get('title')}")
def set_control_text(title: str, control: str, text: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Replace the text of an input field inside a window.

    Args:
        title: Part of the window title.
        control: The field's name or automation id.
        text: New text.
    """
    c = _find_control(title, control)
    try:
        c.set_edit_text(text)  # UI Automation value pattern: no keys, no focus change
        return "Text set."
    except Exception:
        pass
    if _background_set_text(c, text):
        return "Text set."
    if ctx is not None and ctx.request_desktop("this field only accepts real typing"):
        c.set_focus()
        c.type_keys("^a{BACKSPACE}" + text, with_spaces=True)
        return "Text typed with the keyboard (you allowed it)."
    raise ToolError("That field can't be filled in the background, and real typing wasn't allowed.")
