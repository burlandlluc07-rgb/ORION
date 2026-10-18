"""Background routes for actions that used to take the desktop: media keys, window moves, and the last tries
before a real click or real typing. Plus a guard that every core tool which can touch the desktop is classified."""

import asyncio
import inspect
import re

import pytest

from orion.tasks import routing
from orion.tasks.routing import capabilities_for
from orion.tools import apps, screen, system
from orion.tools.registry import ToolContext


@pytest.fixture
def windows(monkeypatch):
    for mod in (routing, screen, apps):
        monkeypatch.setattr(mod, "IS_WIN", True)


def test_router_takes_background_routes_per_call(engine, windows):
    reg = engine.registry
    for keys in ("volumeup", "Volume Down", "playpause", "nexttrack", "mute"):
        caps = capabilities_for(reg.get("press_keys"), args={"keys": keys})
        assert caps.desktop == "none" and caps.route == "local", keys
    for keys in ("ctrl+s", "alt+tab", "enter", "win+volumeup"):
        assert capabilities_for(reg.get("press_keys"), args={"keys": keys}).desktop == "input", keys
    for action in ("minimize", "restore", "close"):
        caps = capabilities_for(reg.get("window_action"), args={"title": "x", "action": action})
        assert caps.route == "app_automation" and caps.desktop == "visible", action
    for action in ("focus", "maximize"):
        assert capabilities_for(reg.get("window_action"), args={"title": "x", "action": action}).desktop == "input"
    assert capabilities_for(reg.get("window_action")).desktop == "input"  # no args: focus is the default


def test_media_keys_need_windows_for_the_background_route(engine, monkeypatch):
    monkeypatch.setattr(routing, "IS_WIN", False)  # elsewhere they are real key presses
    assert capabilities_for(engine.registry.get("press_keys"), args={"keys": "volumeup"}).desktop == "input"


async def test_media_key_runs_without_a_desktop_prompt_or_a_key_press(engine, windows, monkeypatch, config):
    config.settings.approval_mode = "trust"
    config.settings.desktop_actions = "ask"  # the desktop gate is on, and still isn't needed
    sent = []
    monkeypatch.setattr(system, "_app_command", sent.append)
    monkeypatch.setattr(screen, "_gui", lambda: pytest.fail("no key may be injected"))
    asked = []
    engine.bus.subscribe()
    real_request = engine.approvals.request

    async def request(*a, **kw):
        asked.append(a)
        return await real_request(*a, **kw)

    monkeypatch.setattr(engine.approvals, "request", request)
    task = engine.tracker.start("t", None)
    ok, res = await asyncio.wait_for(engine.agent.run_tool("press_keys", {"keys": "volumeup", "times": 2}, task,
                                                           None), 5)
    assert ok and "no key press" in res
    assert sent == ["volume_up", "volume_up"] and not asked
    assert task.steps[0]["lifecycle"]["capabilities"]["desktop"] == "none"


async def test_other_keys_still_ask_when_the_desktop_gate_is_on(engine, windows, monkeypatch, config):
    config.settings.approval_mode = "trust"
    config.settings.desktop_actions = "ask"
    monkeypatch.setattr(screen, "_gui", lambda: pytest.fail("declined: no key may be pressed"))
    asked = []

    async def request(name, label, risk, args, task, desktop=False):
        asked.append(desktop)
        return False

    monkeypatch.setattr(engine.approvals, "request", request)
    task = engine.tracker.start("t", None)
    ok, _ = await engine.agent.run_tool("press_keys", {"keys": "alt+tab"}, task, None)
    assert not ok and asked == [True]


class FakeWindow:
    handle = 42

    def __init__(self):
        self.calls = []

    def window_text(self):
        return "Notes.txt - Notepad"

    def __getattr__(self, name):  # set_focus, restore, minimize, maximize, close, is_minimized
        def call(*a):
            self.calls.append(name)
            return False

        return call


@pytest.mark.parametrize(("action", "end"), [("minimize", "minimized"), ("restore", "normal"), ("close", "closed")])
def test_window_moves_use_messages_that_keep_focus(monkeypatch, action, end):
    win = FakeWindow()
    sent = []
    monkeypatch.setattr(apps, "_find_window", lambda title: win)
    monkeypatch.setattr(apps, "_quiet_window_action", lambda hwnd, a: sent.append((hwnd, a)))
    monkeypatch.setattr(apps, "_wait_state", lambda hwnd, want, timeout=3.0: end)
    out = apps.window_action("notes", action)
    assert sent == [(42, action)] and win.calls == []  # no set_focus, no pywinauto minimize/restore/close
    assert "Notes.txt" in out


def test_window_move_that_did_not_happen_is_an_error(monkeypatch):
    monkeypatch.setattr(apps, "_find_window", lambda title: FakeWindow())
    monkeypatch.setattr(apps, "_quiet_window_action", lambda hwnd, a: None)
    monkeypatch.setattr(apps, "_wait_state", lambda hwnd, want, timeout=3.0: "normal")
    with pytest.raises(apps.ToolError, match="didn't minimize"):
        apps.window_action("notes", "minimize")
    assert "still open" in apps.window_action("notes", "close")  # e.g. it is asking to save: said, not hidden


class Legacy:
    def __init__(self, owner):
        self.owner = owner

    def DoDefaultAction(self):  # noqa: N802 - the UI Automation name
        self.owner.events.append("default_action")

    def SetValue(self, text):  # noqa: N802
        self.owner.value = text


class StubbornControl:
    """A control with no invoke/select/toggle/expand and no value pattern, like many custom-drawn apps."""

    handle = 0

    def __init__(self):
        self.events = []
        self.value = ""
        self.iface_legacy_iaccessible = Legacy(self)

    def window_text(self):
        return self.value or "Save"

    def legacy_properties(self):
        return {"Value": self.value}

    def class_name(self):
        return "Custom"

    def _fail(self, *a, **kw):
        raise RuntimeError("pattern not supported")

    invoke = select = toggle = expand = set_edit_text = _fail

    def click_input(self):
        self.events.append("mouse")

    def set_focus(self):
        self.events.append("focus")

    def type_keys(self, *a, **kw):
        self.events.append("keys")


def no_desktop_ctx(asked):
    return ToolContext(engine=None, escalate=lambda reason: asked.append(reason) or False)


def test_click_uses_the_accessible_default_action_before_the_mouse(monkeypatch):
    c = StubbornControl()
    monkeypatch.setattr(apps, "_find_control", lambda title, control: c)
    asked = []
    assert apps.click_control("app", "Save", ctx=no_desktop_ctx(asked)).startswith("Activated")
    assert c.events == ["default_action"] and not asked


def test_text_uses_the_accessible_value_before_typing(monkeypatch):
    c = StubbornControl()
    monkeypatch.setattr(apps, "_find_control", lambda title, control: c)
    asked = []
    assert apps.set_control_text("app", "Name", "Lluc", ctx=no_desktop_ctx(asked)) == "Text set."
    assert c.value == "Lluc" and c.events == [] and not asked


def test_real_mouse_is_still_the_last_resort_and_asks(monkeypatch):
    c = StubbornControl()
    c.iface_legacy_iaccessible = None  # nothing in the background works
    monkeypatch.setattr(apps, "_find_control", lambda title, control: c)
    reasons = []
    with pytest.raises(apps.ToolError, match="background"):
        apps.click_control("app", "Save", ctx=no_desktop_ctx(reasons))
    assert reasons and c.events == []


# Calls that move the mouse, send keys, take focus or open a visible window.
DESKTOP_CALLS = re.compile(r"open_target|pyautogui|_gui\(|set_focus|click_input|type_keys|send_keys|startfile|"
                           r"SetForegroundWindow|\blaunch\(|bring_to_front|press_send_in_whatsapp|webbrowser|"
                           r"SetWindowPos|ShowWindow|LockWorkStation|_find_window|_find_control")
# Tools whose desktop calls are known to be background ones, with the reason.
KNOWN_BACKGROUND = {
    "media_control": "WM_APPCOMMAND to the shell on Windows; pyautogui only off Windows",
    "change_volume": "the volume API, else WM_APPCOMMAND to the shell",
    "mute": "the volume API, else WM_APPCOMMAND to the shell",
}


def test_every_core_tool_that_can_touch_the_desktop_is_classified(engine):
    """The router classifies by name, so a new tool that isn't listed would run as background. This catches it."""
    listed = routing.INPUT | routing.VISIBLE | routing.BROWSER | routing.APP_AUTOMATION | set(KNOWN_BACKGROUND)
    missing = []
    for t in engine.registry.all(include_disabled=True):
        if t.source.startswith(("mcp:", "plugin:")):
            continue
        try:
            src = inspect.getsource(t.fn)
        except (OSError, TypeError):
            continue
        if DESKTOP_CALLS.search(src) and t.name not in listed:
            missing.append(t.name)
    assert not missing, f"classify these in orion/tasks/routing.py: {missing}"


def test_classic_edit_box_gets_a_set_text_message_before_ui_automation(monkeypatch, windows):
    """The lab showed UI Automation's value pattern focuses a classic edit box, so the message goes first."""
    c = StubbornControl()
    c.handle = 7
    c.class_name = lambda: "Edit"
    c.set_edit_text = lambda text: pytest.fail("UI Automation would take the focus")
    sent = []

    class User32:
        def SendMessageTimeoutW(self, hwnd, msg, wparam, lparam, flags, timeout, result):  # noqa: N802
            sent.append((hwnd, msg))
            c.value = lparam.value

    monkeypatch.setattr(apps, "_user32", User32)
    monkeypatch.setattr(apps, "_find_control", lambda title, control: c)
    asked = []
    assert apps.set_control_text("app", "Text Editor", "hi", ctx=no_desktop_ctx(asked)) == "Text set."
    assert sent == [(7, apps.WM_SETTEXT)] and c.value == "hi" and c.events == [] and not asked
