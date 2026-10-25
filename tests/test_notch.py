from types import SimpleNamespace

from orion.config import Settings
from orion.desktop import notch
from orion.desktop.shell import DesktopShell


class FakeWindow:
    def __init__(self):
        self.calls = []

    def resize(self, w, h):
        self.calls.append(("resize", w, h))

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def evaluate_js(self, code):
        self.calls.append(("js", code))

    def show(self):
        self.calls.append(("show",))

    def restore(self):
        self.calls.append(("restore",))


def make_shell(**settings):
    shell = DesktopShell(SimpleNamespace(settings=Settings(**settings), root=None))
    shell.notch = FakeWindow()
    return shell


def test_notch_is_on_by_default():
    assert Settings().notch is True


def test_sizes_grow_from_idle_to_open():
    (iw, ih), (pw, ph), (ow, oh) = (notch.size_for(m) for m in ("idle", "peek", "open"))
    assert iw < pw < ow and ih < ph < oh
    assert notch.size_for("nonsense") == notch.size_for("idle")


def test_place_centres_flush_with_top():
    x, y, w, h = notch.place("idle", 0, 0, 1920)
    assert (w, h) == notch.size_for("idle")
    assert y == 0 and x == (1920 - w) // 2
    x2, *_ = notch.place("open", 1920, 0, 2560)  # second monitor to the right
    assert x2 == 1920 + (2560 - notch.size_for("open")[0]) // 2


def test_native_helpers_are_noops_off_windows():
    assert notch.apply(0, "open") is False
    assert notch.set_visible(0, True) is False
    notch.set_activatable(0, True)  # must not raise
    assert notch.fullscreen_app_in_front() is False


def test_mode_actions_resize_the_window():
    shell = make_shell()
    shell.window_action("notch-peek")
    assert shell._notch_mode == "peek"
    w, h = notch.size_for("peek")
    assert ("resize", w, h) in shell.notch.calls


def test_unknown_notch_action_is_ignored():
    shell = make_shell()
    shell.window_action("notch-explode")
    assert shell._notch_mode == "idle" and shell.notch.calls == []


def test_hotkey_toggles_the_notch_instead_of_the_quick_bar():
    shell = make_shell()
    shell.toggle_quick()
    assert shell.notch.calls == [("js", "window.__orionNotch && window.__orionNotch('toggle')")]


def test_open_main_closes_the_notch_and_shows_orion():
    shell = make_shell()
    shell.window = FakeWindow()
    shell.window_action("notch-open-main")
    assert ("js", "window.__orionNotch && window.__orionNotch('close')") in shell.notch.calls
    assert ("show",) in shell.window.calls


def test_actions_are_safe_without_a_notch():
    shell = make_shell()
    shell.notch = None
    shell.window_action("notch-open")
    shell.window_action("notch-focus")


def test_pills_fit_their_windows():
    for mode in ("idle", "hover", "peek", "open"):
        pw, ph, r = notch.pill_for(mode)
        ww, wh = notch.size_for(mode)
        assert pw <= ww and ph <= wh and 0 < r <= ph
    assert notch.size_for("hover") == notch.size_for("idle")


def test_morph_frames_end_exactly_and_overshoot_a_little():
    frames = notch.morph_frames(notch.pill_for("idle"), notch.pill_for("open"), 20)
    assert frames[-1] == notch.pill_for("open")
    assert max(f[0] for f in frames) > notch.pill_for("open")[0]  # spring overshoot
    assert max(f[0] for f in frames) < notch.pill_for("open")[0] * 1.15
    shrink = notch.morph_frames(notch.pill_for("open"), notch.pill_for("idle"), 20)
    assert shrink[-1] == notch.pill_for("idle") and min(f[1] for f in shrink) >= 1


def test_hover_is_a_shape_only_mode():
    shell = make_shell()
    shell.window_action("notch-hover")
    assert shell._notch_mode == "hover"
    assert ("resize", *notch.size_for("idle")) in shell.notch.calls


def test_shape_helpers_are_noops_off_windows():
    assert notch.set_shape(0, "idle") is False
    notch.morph(0, "idle", "open")  # must not raise or sleep
