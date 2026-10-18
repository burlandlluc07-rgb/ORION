"""Screen vision and keyboard/mouse control."""

from __future__ import annotations

import io
import time

from .platform import IS_WIN, home
from .registry import ToolContext, ToolError, tool


def capture(monitor: int = 1, max_side: int = 1600) -> tuple[bytes, tuple[int, int]]:
    """Grab a monitor as JPEG bytes, downscaled to keep vision requests small and fast."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        mons = sct.monitors
        idx = monitor if 0 < monitor < len(mons) else 1
        shot = sct.grab(mons[idx])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80, optimize=True)
    return buf.getvalue(), img.size


@tool(category="vision")
async def look_at_screen(question: str = "Describe what's on the screen.", monitor: int = 1,
                         ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Look at the user's screen and answer a question about it (read errors, describe a window, find a button…).

    Args:
        question: What to look for or answer about the screen.
        monitor: Monitor number (1 = primary).
    """
    import asyncio

    jpeg, size = await asyncio.to_thread(capture, monitor)
    if ctx is None or ctx.engine is None:
        raise ToolError("Vision is unavailable.")
    answer = await ctx.engine.brain.vision(question, jpeg, "image/jpeg")
    return f"[screen {size[0]}x{size[1]}] {answer}"


@tool(category="vision")
def take_screenshot(monitor: int = 1) -> str:
    """Save a screenshot to Pictures/ORION and return its path.

    Args:
        monitor: Monitor number (1 = primary).
    """
    import mss
    import mss.tools

    folder = home() / "Pictures" / "ORION"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("screenshot_%Y-%m-%d_%H-%M-%S.png")
    with mss.mss() as sct:
        mons = sct.monitors
        shot = sct.grab(mons[monitor if 0 < monitor < len(mons) else 1])
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    return f"Screenshot saved: {path}"


def _gui():
    try:
        import pyautogui
    except Exception as exc:  # no display
        raise ToolError(f"Keyboard/mouse control unavailable: {exc}") from exc
    pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort
    return pyautogui


@tool(risk="confirm", category="input", summary=lambda a: f"Type “{str(a.get('text'))[:40]}”")
def type_text(text: str, press_enter: bool = False) -> str:
    """Type text into whatever window currently has focus (as if on the keyboard).

    Args:
        text: Text to type.
        press_enter: Press Enter afterwards.
    """
    g = _gui()
    if text.isascii():
        g.write(text, interval=0.008)
    else:  # unicode: paste via clipboard
        import pyperclip

        old = pyperclip.paste()
        pyperclip.copy(text)
        g.hotkey("ctrl", "v")
        time.sleep(0.15)
        pyperclip.copy(old)
    if press_enter:
        g.press("enter")
    return "Typed."


@tool(risk="confirm", category="input", summary=lambda a: f"Press {a.get('keys')}")
def press_keys(keys: str, times: int = 1) -> str:
    """Press a key or shortcut, e.g. "enter", "ctrl+s", "alt+tab", "win+d", "ctrl+shift+esc".

    Args:
        keys: Keys joined with "+".
        times: Repeat count.
    """
    from ..tasks.routing import media_key

    if (command := media_key(keys)) is not None and IS_WIN:
        from .system import _app_command

        for _ in range(max(1, min(times, 50))):  # a shell message, not a key press: the focused window gets nothing
            _app_command(command)
        return f"Pressed {keys} (sent to Windows directly, no key press)."
    g = _gui()
    parts = [k.strip().lower().replace("windows", "win").replace("control", "ctrl") for k in keys.split("+")]
    for _ in range(max(1, min(times, 50))):
        if len(parts) == 1:
            g.press(parts[0])
        else:
            g.hotkey(*parts)
        time.sleep(0.05)
    return f"Pressed {keys}."


@tool(risk="confirm", category="input", summary=lambda a: f"Click at ({a.get('x')}, {a.get('y')})")
def mouse_click(x: float, y: float, button: str = "left", double: bool = False, relative: bool = False) -> str:
    """Click the mouse at screen coordinates.

    Args:
        x: Horizontal pixel position (or 0-1 fraction of the screen width when relative is true).
        y: Vertical pixel position (or 0-1 fraction of the screen height when relative is true).
        button: left, right or middle.
        double: Double-click.
        relative: Treat x/y as fractions of the primary screen (used by the phone's tap-to-click).
    """
    g = _gui()
    if relative:
        w, h = g.size()
        x, y = max(0.0, min(1.0, float(x))) * (w - 1), max(0.0, min(1.0, float(y))) * (h - 1)
    x, y = int(round(float(x))), int(round(float(y)))
    g.click(x=x, y=y, button=button, clicks=2 if double else 1, interval=0.08)
    return f"Clicked at {x},{y}."


@tool(category="input")
def scroll(amount: int = -5) -> str:
    """Scroll the window under the mouse. Negative scrolls down.

    Args:
        amount: Scroll steps; negative = down, positive = up.
    """
    _gui().scroll(int(amount) * 100)
    return "Scrolled."

