import ast
from pathlib import Path

import pytest

from orion.ghost import core, sprites
from orion.tasks.routing import GHOST, capabilities_for

ROOT = Path(__file__).resolve().parent.parent


def test_ghost_style_is_see_through_click_through_unfocusable():
    style = core.ghost_style(core.WS_EX_APPWINDOW | 0x100)
    for bit in (core.WS_EX_LAYERED, core.WS_EX_TRANSPARENT, core.WS_EX_NOACTIVATE, core.WS_EX_TOOLWINDOW):
        assert style & bit
    assert not style & core.WS_EX_APPWINDOW
    assert style & 0x100  # other bits kept


def test_opacity_is_clamped():
    assert core.clamp_opacity(None) == core.DEFAULT_OPACITY
    assert core.clamp_opacity(0) == core.MIN_OPACITY
    assert core.clamp_opacity(2) == core.MAX_OPACITY
    assert core.clamp_opacity("nope") == core.DEFAULT_OPACITY
    assert core.alpha_byte(0.5) == 128


def test_slots_stay_on_screen_and_cascade():
    work = core.Rect(0, 0, 1920, 1040)
    size = core.ghost_size((1200, 800), work)
    assert 560 <= size[0] <= 1920 and size[1] <= 1040 * 0.5 + 1
    a, b = core.slot_rect(0, size, work), core.slot_rect(1, size, work)
    for r in (a, b):
        assert r.left >= 0 and r.right <= 1920 and r.top >= core.LABEL_SPACE and r.bottom <= 1040
    assert b.left < a.left and b.top < a.top  # each later ghost steps inwards so every tag shows
    tl = core.slot_rect(0, size, work, "top-left")
    assert tl.left == core.MARGIN and tl.top == core.MARGIN + core.LABEL_SPACE


def test_glide_path_ends_on_target_and_is_curved():
    path = core.glide_path((0, 0), (300, 0), 20)
    assert path[-1] == (300, 0)
    assert any(y != 0 for _, y in path)
    assert 4 <= core.glide_steps((0, 0), (10, 0)) < core.glide_steps((0, 0), (2000, 0))


def test_keys_and_text():
    assert core.parse_keys("tab, tab enter") == [0x09, 0x09, 0x0D]
    with pytest.raises(ValueError):
        core.parse_keys("ctrl+s")
    with pytest.raises(ValueError):
        core.parse_keys("hyperspace")
    assert core.utf16_units("a\nb") == [ord("a"), ord("\r"), ord("b")]
    assert len(core.utf16_units("😀")) == 2  # surrogate pair
    assert core.make_lparam(5, 7) == (7 << 16) | 5
    assert core.key_lparam(0, True) & (1 << 31)


def test_posted_clicks_detection():
    assert core.posted_clicks_work(["Edit", "Notepad"])
    assert not core.posted_clicks_work(["Chrome_RenderWidgetHostHWND", "Chrome_WidgetWin_1"])
    assert not core.posted_clicks_work(["ApplicationFrameWindow"])


def test_text_box_classes():
    assert core.is_edit_class("Edit") and core.is_edit_class("RichEditD2DPT") and core.is_edit_class("RICHEDIT50W")
    assert not core.is_edit_class("Button") and not core.is_edit_class("")
    assert core.takes_replacesel("Edit") and not core.takes_replacesel("RichEditD2DPT")
    assert core.edit_text("a\nb\r\nc") == "a\r\nb\r\nc"


def test_scale_point_maps_picture_to_window():
    assert core.scale_point(50, 25, (100, 50), (1000, 500)) == (500, 250)
    assert core.scale_point(5000, -3, (100, 50), (1000, 500)) == (999, 0)


def test_sprites_render():
    cur = sprites.cursor([(-10, -10), (-5, -5)], ripple=0.5, t=1.0)
    assert cur.size == (sprites.CURSOR_BOX, sprites.CURSOR_BOX)
    assert cur.getpixel(sprites.HOT)[3] > 0  # the tip is drawn exactly on the hotspot
    fr = sprites.frame(400, 300, "Notepad", t=0.2)
    assert fr.size == (420, 346)
    assert fr.getpixel((210, 200))[3] == 0  # the middle stays clear so the ghost window shows through
    data = sprites.bgra_premultiplied(cur)
    assert len(data) == cur.size[0] * cur.size[1] * 4


def test_ghost_tools_route_without_desktop_gate():
    class T:
        source = "builtin"
        background = None

        def __init__(self, name, risk):
            self.name, self.risk = name, risk

    for name in GHOST:
        caps = capabilities_for(T(name, "confirm"))
        assert caps.route == "app_automation" and caps.desktop == "none" and not caps.needs_desktop


def test_ghost_code_never_takes_real_input():
    """The rule: ghost mode never moves the user's mouse or types on their keyboard."""
    banned = {"SendInput", "SetCursorPos", "mouse_event", "keybd_event", "click_input", "type_keys", "pyautogui"}
    for path in [*(ROOT / "orion" / "ghost").glob("*.py"), ROOT / "orion" / "tools" / "ghost.py"]:
        tree = ast.parse(path.read_text("utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
                {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        assert not names & banned, f"{path.name} uses {names & banned}"


def test_office_text_splitting():
    from orion.ghost import office

    assert office.office_kind("WINWORD") == "winword" and office.office_kind("notepad") == ""
    assert office.word_lines("a\r\nb\nc") == ["a", "b", "c"]
    assert office.excel_rows("a\tb\nc") == [["a", "b"], ["c"]]


def test_open_app_is_a_ghost_by_default_on_windows(monkeypatch):
    from orion.tasks import routing

    class T:
        name, source, background, risk = "open_app", "builtin", None, "safe"

    monkeypatch.setattr(routing, "IS_WIN", True)
    caps = routing.capabilities_for(T(), args={"name": "word"})
    assert caps.route == "app_automation" and not caps.needs_desktop
    caps = routing.capabilities_for(T(), args={"name": "spotify", "normal": True})
    assert caps.needs_desktop


def test_open_app_hands_windows_launches_to_ghost_mode(monkeypatch):
    from orion.tools import apps, ghost

    calls = []
    monkeypatch.setattr(apps, "IS_WIN", True)
    monkeypatch.setattr(ghost, "ghost_open", lambda name, ctx=None: calls.append(name) or "ghost")
    monkeypatch.setattr(apps, "launch", lambda target: calls.append(("normal", target)))
    assert apps.open_app("word") == "ghost"
    apps.open_app("settings")  # Settings is for the user: a normal window
    assert calls[0] == "word" and calls[1][0] == "normal"
