"""Pure ghost-mode logic: window styles, corner layout, cursor motion, key maps, app quirks."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Extended window styles.
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020  # clicks fall through to whatever is underneath
WS_EX_TOOLWINDOW = 0x00000080  # no taskbar button, no Alt+Tab
WS_EX_LAYERED = 0x00080000  # lets the window be see-through
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000  # never becomes the active window
GHOST_BITS = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE

DEFAULT_GLASS = 30  # % see-through (Lluc, 2026-11-01: "make it 30% glass")
DEFAULT_OPACITY = 1 - DEFAULT_GLASS / 100
MIN_OPACITY, MAX_OPACITY = 0.25, 0.85
MARGIN = 16
FOLLOW_WINDOWS_S = 25  # after ORION acts in a ghost app, new windows of that app become ghosts too
LAYOUTS = ("full", "natural")  # full screen (default) / where the app put itself; or one of CORNERS
ACTIVITY_S = 8  # how long the "ORION is …" pill stays up after his last action in a ghost
CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
LABEL_SPACE = 30  # room above each ghost for its "ORION · ghost" tag

# Window classes whose content ignores posted mouse messages: Chromium/Electron render widgets and Store apps.
# Their buttons usually still answer UI Automation, so ORION uses controls by name there, or asks.
NO_POSTED_CLICKS = ("chrome_widgetwin", "chrome_renderwidgethosthwnd", "intermediate d3d window",
                    "applicationframewindow", "windows.ui.core.corewindow", "mozillawindowclass")

# Keys ORION can press inside a ghost window with window messages (no modifier keys: their state is global, so
# Ctrl+S in the background would have to hold *your* Ctrl key, which is exactly what ghost mode never does).
VK: dict[str, int] = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B, "backspace": 0x08, "delete": 0x2E,
    "del": 0x2E, "space": 0x20, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D, **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
# Keys that also produce a character message, like a real key press does.
KEY_CHARS = {0x0D: "\r", 0x09: "\t", 0x08: "\b", 0x20: " ", 0x1B: "\x1b"}


# Text boxes ORION can type into without focus. Plain "Edit" boxes (Notepad, most dialogs) also take whole text
# with EM_REPLACESEL, which Windows carries across processes for this system class only.
TEXT_BOX_CLASSES = ("edit", "richedit", "scintilla")


def is_edit_class(cls: str) -> bool:
    return (cls or "").lower().startswith(TEXT_BOX_CLASSES)


def takes_replacesel(cls: str) -> bool:
    return (cls or "").lower() == "edit"


def edit_text(text: str) -> str:
    """Line breaks as a Windows text box stores them."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def full_rect(work: Rect) -> Rect:
    """A ghost that fills the screen's work area, like a maximised window (taskbar left visible)."""
    return Rect(work.left, work.top, work.right, work.bottom)


def pill_pos(rect: tuple[int, int, int, int], size: tuple[int, int], margin: int = 14) -> tuple[int, int]:
    """Where the activity pill sits: centred near the top of the ghost window."""
    l, t, r, _b = rect
    return l + (r - l - size[0]) // 2, t + margin


def ghost_style(style: int) -> int:
    """Extended style that turns a normal top-level window into a ghost."""
    return (style | GHOST_BITS) & ~WS_EX_APPWINDOW


def glass_to_opacity(glass) -> float:
    """'30% glass' (30% see-through) → window opacity 0.7."""
    try:
        return clamp_opacity(1 - float(glass) / 100)
    except (TypeError, ValueError):
        return DEFAULT_OPACITY


def clamp_opacity(value: float | None) -> float:
    try:
        v = float(value if value is not None else DEFAULT_OPACITY)
    except (TypeError, ValueError):
        v = DEFAULT_OPACITY
    return min(MAX_OPACITY, max(MIN_OPACITY, v))


def alpha_byte(opacity: float) -> int:
    return round(clamp_opacity(opacity) * 255)


def posted_clicks_work(class_names: list[str]) -> bool:
    """False when the target window (or its parents) is a kind that drops background clicks."""
    low = [c.lower() for c in class_names if c]
    return not any(any(c.startswith(bad) for bad in NO_POSTED_CLICKS) for c in low)


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


def ghost_size(original: tuple[int, int], work: Rect, scale: float = 0.4) -> tuple[int, int]:
    """Shrink a window for the ghost corner, keeping its shape, never below a usable 560×380."""
    ow, oh = max(1, original[0]), max(1, original[1])
    w = max(560, int(work.width * scale))
    h = int(w * oh / ow)
    max_h = int(work.height * 0.5)
    if h > max_h:
        h = max_h
        w = max(560, int(h * ow / oh))
    return min(w, work.width - 2 * MARGIN), max(380, min(h, work.height - 2 * MARGIN - LABEL_SPACE))


def slot_rect(index: int, size: tuple[int, int], work: Rect, corner: str = "bottom-right") -> Rect:
    """Where the index-th ghost sits: tucked into a corner, later ghosts cascading inwards so each tag shows."""
    w, h = size
    step = LABEL_SPACE + 8
    off = (index % 6) * step
    right = corner.endswith("right")
    bottom = corner.startswith("bottom")
    x = work.right - MARGIN - w - off if right else work.left + MARGIN + off
    y = work.bottom - MARGIN - h - off if bottom else work.top + MARGIN + LABEL_SPACE + off
    x = max(work.left, min(x, work.right - w))
    y = max(work.top + LABEL_SPACE, min(y, work.bottom - h))
    return Rect(x, y, x + w, y + h)


def ease_in_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 0.5 - math.cos(math.pi * t) / 2


def glide_path(start: tuple[float, float], end: tuple[float, float], steps: int, bend: float = 0.18) -> list[tuple[int, int]]:
    """A gently curved, eased path for the ghost cursor, so it reads as a hand moving, not a teleport."""
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    # Control point off to one side of the straight line.
    cx, cy = sx + dx / 2 - dy * bend, sy + dy / 2 + dx * bend
    out = []
    for i in range(1, max(1, steps) + 1):
        t = ease_in_out(i / max(1, steps))
        x = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * ex
        y = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * ey
        out.append((round(x), round(y)))
    return out


def glide_steps(start: tuple[float, float], end: tuple[float, float], fps: int = 60) -> int:
    """Longer moves take a little longer, between 0.18 and 0.6 s."""
    d = math.hypot(end[0] - start[0], end[1] - start[1])
    return max(4, int(fps * min(0.6, 0.18 + d / 2400)))


def make_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def key_lparam(vk_scan: int, up: bool) -> int:
    """lParam for WM_KEYDOWN / WM_KEYUP: repeat 1, the scan code, and the transition bits for a release."""
    lp = 1 | ((vk_scan & 0xFF) << 16)
    if up:
        lp |= (1 << 30) | (1 << 31)
    return lp


def parse_keys(keys: str) -> list[int]:
    """'enter', 'tab tab', 'down, enter' → virtual-key codes. Modifier combos are refused (see VK)."""
    out = []
    for raw in keys.replace(",", " ").split():
        k = raw.strip().lower()
        if "+" in k and k != "+":
            raise ValueError("Ghost windows can't take key combinations like Ctrl+S without using your keyboard. "
                             "Use a menu or button by name instead.")
        if k not in VK:
            raise ValueError(f"Unknown key “{raw}”. Ghost keys: {', '.join(sorted(set(VK)))}.")
        out.append(VK[k])
    if not out:
        raise ValueError("No keys given.")
    return out


def utf16_units(text: str) -> list[int]:
    """WM_CHAR takes UTF-16 code units: emoji and other astral characters need their surrogate pair."""
    data = text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def scale_point(x: float, y: float, shot: tuple[int, int], window: tuple[int, int]) -> tuple[int, int]:
    """A point on the (downscaled) picture ORION looked at → a point in the window, in window pixels."""
    sw, sh = max(1, shot[0]), max(1, shot[1])
    px, py = round(x * window[0] / sw), round(y * window[1] / sh)
    return min(max(px, 0), max(0, window[0] - 1)), min(max(py, 0), max(0, window[1] - 1))


@dataclass
class Ghost:
    """One window ORION is working in."""
    hwnd: int
    title: str
    pid: int = 0
    app: str = ""
    original_style: int = 0
    original_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    opened: float = 0.0
    last_action: str = ""
    shot_size: tuple[int, int] = (0, 0)
    flags: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.hwnd, "title": self.title, "app": self.app, "opened": self.opened,
                "last_action": self.last_action, "rect": list(self.rect)}
