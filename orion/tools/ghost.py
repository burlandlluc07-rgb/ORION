"""Ghost mode tools: ORION uses desktop apps in his own see-through windows with his own cursor.

None of these tools move the user's mouse, type on their keyboard, or change which window they're using.
When an app won't work that way, the tool says so and ORION asks instead (see orion/ghost/__init__.py).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ..ghost import core
from .platform import IS_WIN
from .registry import ToolContext, ToolError, tool

KIT = "ghost"


def _desk():
    if not IS_WIN:
        raise ToolError("Ghost mode needs Windows.")
    from ..ghost.win32 import DESK

    return DESK


def _say(ctx: ToolContext | None, text: str) -> None:
    eng = getattr(ctx, "engine", None)
    if eng is not None and getattr(eng, "thoughts", None) is not None:
        eng.thoughts.add("ghost", text)
    if eng is not None:
        eng.bus.emit("ghost.changed")


def _guard(fn):
    """Turn ghost-mode refusals into normal tool errors the model can read."""
    from ..ghost.win32 import GhostError

    try:
        return fn()
    except GhostError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def resolve_app(name: str) -> tuple[str, str, list[str]]:
    """(target to launch, display name, names to recognise its window's program by)."""
    from .apps import INDEX, WIN_BUILTINS

    q = name.strip().lower()
    if q in WIN_BUILTINS:
        target = WIN_BUILTINS[q]
        return target, name, [q, Path(target).stem.lower()]
    hit = INDEX.find(q)
    if hit:
        display, target = hit
        keys = [q, display.lower(), Path(target).stem.lower()]
        return target, display.title(), [k for k in keys if k]
    exe = shutil.which(q) or shutil.which(q.replace(" ", ""))
    if exe:
        return exe, name, [q, Path(exe).stem.lower()]
    raise ToolError(f"Couldn't find an installed app called “{name}”.")


def _control(hwnd: int, control: str):
    """A control inside one ghost window, by name or automation id (UI Automation)."""
    from pywinauto import Desktop

    win = Desktop(backend="uia").window(handle=hwnd)
    target = control.strip().lower()
    best = None
    for c in win.descendants():
        try:
            name = (c.window_text() or "").strip().lower()
            aid = (c.element_info.automation_id or "").lower()
        except Exception:
            continue
        if target in (name, aid):
            return c
        if best is None and target and target in name:
            best = c
    if best is None:
        raise ToolError(f"No control “{control}” in that ghost window. Use ghost_look to see what's there.")
    return best


@tool(category="ghost", kit=KIT, summary=lambda a: f"Open {a.get('app')} as a ghost window")
def ghost_open(app: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Open a desktop app for ORION's own work as a ghost window: see-through, click-through and never focused,
    full screen. The user keeps working undisturbed. Word and Excel open as ORION's own copy with a blank document
    and take ghost_type straight into it.

    Args:
        app: The app's name, e.g. "notepad", "excel", "calculator".
    """
    target, display, keys = resolve_app(app)
    desk = _desk()
    eng = getattr(ctx, "engine", None)
    if eng is not None:
        s = eng.config.settings
        desk.configure(opacity=core.glass_to_opacity(getattr(s, "ghost_glass", core.DEFAULT_GLASS)), corner=getattr(s, "ghost_layout", None),
                       cursor=getattr(s, "ghost_cursor", None), frame=getattr(s, "ghost_frame", None))
        desk.on_event = lambda _kind, text: _say(ctx, text)
    from ..ghost import office

    kind = next((office.office_kind(k) for k in keys if office.office_kind(k)), "")
    if kind:
        g = _guard(lambda: _open_office(desk, kind, display))
    else:
        g = _guard(lambda: desk.open(target, display, keys))
    return (f"{display} is open as a ghost window titled “{g.title}”. Use ghost_look to see it, then ghost_click, "
            f"ghost_type and ghost_press to work it.")


@tool(category="ghost", kit=KIT, summary=lambda a: f"Look at ghost window {a.get('window') or ''}".strip())
async def ghost_look(question: str = "Describe this window and where its buttons and fields are.", window: str = "",
                     ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """See one of ORION's ghost windows (captured directly, so it works even behind other windows) and answer a
    question about it. Coordinates in the answer are in the picture's pixels, ready for ghost_click.

    Args:
        question: What to find or answer, e.g. "where is the Save button?".
        window: Part of the ghost window's title (default: the newest ghost).
    """
    desk = _desk()
    g = _guard(lambda: desk.find(window))
    jpeg, size = await asyncio.to_thread(lambda: _guard(lambda: desk.look(g)))
    if ctx is None or ctx.engine is None:
        raise ToolError("Vision is unavailable.")
    prompt = (f"{question}\nThe picture is {size[0]}x{size[1]} pixels. When you mention something to click, give "
              "its centre as (x, y) in those pixels.")
    answer = await ctx.engine.brain.vision(prompt, jpeg, "image/jpeg")
    _say(ctx, f"Looked at {g.title}")
    return f"[ghost “{g.title}” {size[0]}x{size[1]}] {answer}"


@tool(risk="confirm", category="ghost", kit=KIT,
      summary=lambda a: f"Ghost-click {a.get('control') or 'at ' + str(a.get('x')) + ',' + str(a.get('y'))}")
def ghost_click(window: str = "", control: str = "", x: float = -1, y: float = -1, button: str = "left",
                double: bool = False, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Click inside a ghost window with ORION's ghost cursor. The user's own mouse never moves.

    Prefer `control` (a button/menu/field name); otherwise give x, y from ghost_look's picture.

    Args:
        window: Part of the ghost window's title (default: the newest ghost).
        control: The control's name or automation id.
        x: Horizontal position in ghost_look's picture.
        y: Vertical position in ghost_look's picture.
        button: "left" or "right".
        double: Double-click.
    """
    desk = _desk()
    g = _guard(lambda: desk.find(window))
    if control:
        c = _control(g.hwnd, control)
        try:
            desk.point_at(c.rectangle())
        except Exception:
            pass
        for method in ("invoke", "select", "toggle", "expand"):
            fn = getattr(c, method, None)
            if fn:
                try:
                    fn()
                    _say(ctx, f"Clicked “{control}” in {g.title}")
                    return f"Activated “{c.window_text()}” in {g.title}."
                except Exception:
                    continue
        from .apps import _background_click

        if _background_click(c):
            _say(ctx, f"Clicked “{control}” in {g.title}")
            return f"Activated “{c.window_text()}” in {g.title}."
        raise ToolError(f"“{control}” doesn't respond to background clicks. Ask the user before trying another way.")
    if x < 0 or y < 0:
        raise ToolError("Give a control name, or x and y from ghost_look.")
    out = _guard(lambda: desk.click(g, x, y, "right" if button == "right" else "left", double))
    _say(ctx, out)
    return out


@tool(risk="confirm", category="ghost", kit=KIT, summary=lambda a: f"Ghost-type into {a.get('window') or 'ghost window'}")
def ghost_type(text: str, window: str = "", control: str = "", ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Type text into a ghost window, never with the user's keyboard. With `control`, the field's text is set
    directly; without it, the text goes to the ghost's own focused field (click it first with ghost_click).

    Args:
        text: What to type. Use \\n for new lines.
        window: Part of the ghost window's title (default: the newest ghost).
        control: The field's name or automation id.
    """
    desk = _desk()
    g = _guard(lambda: desk.find(window))
    if control:
        from .apps import _background_set_text, _text_is, _win32_set_text

        c = _control(g.hwnd, control)
        try:
            desk.point_at(c.rectangle())
        except Exception:
            pass
        if _win32_set_text(c, text) or _background_set_text(c, text):
            _say(ctx, f"Typed into “{control}” in {g.title}")
            return f"Set “{control}” in {g.title}."
        try:
            c.iface_value.SetValue(text)
            if _text_is(c, text):
                return f"Set “{control}” in {g.title}."
        except Exception:
            pass
        raise ToolError(f"“{control}” won't take text in the background. Ask the user before trying another way.")
    out = _guard(lambda: _office_or_messages(desk, g, text))
    _say(ctx, out)
    return out


def _open_office(desk, kind: str, display: str):
    """Word / Excel: ORION's own copy through Office automation (a blank document, never your open files), made a
    ghost as soon as its window exists."""
    from ..ghost import office
    from ..ghost.win32 import user32

    locked = bool(user32.LockSetForegroundWindow(1))  # it can't jump in front of you while it starts
    try:
        try:
            hwnd = office.open_office(kind)
        except Exception as exc:  # noqa: BLE001 - COM errors come in many shapes
            raise ToolError(f"Couldn't start {display} through Office automation ({exc}).") from exc
        return desk.adopt(hwnd, display)
    finally:
        if locked:
            user32.LockSetForegroundWindow(2)


def _office_or_messages(desk, g, text: str) -> str:
    """Word and Excel take text through Office automation; everything else through the ghost's own messages."""
    from ..ghost import office
    from ..ghost.win32 import _exe

    kind = office.office_kind(Path(_exe(g.pid)).stem)
    if kind:
        desk.busy(g, doing=f"ORION is typing in {g.app or 'Office'}")  # new windows Word opens become ghosts too
        try:
            out = office.type_into(kind, g.hwnd, text)
        except Exception as exc:  # noqa: BLE001 - COM errors come in many shapes
            raise ToolError(f"Office wouldn't take the text in the background ({exc}). Ask the user before trying "
                            "another way.") from exc
        g.last_action = f"typed {len(text)} characters"
        return out
    return desk.type(g, text)


@tool(risk="confirm", category="ghost", kit=KIT, summary=lambda a: f"Ghost-press {a.get('keys')}")
def ghost_press(keys: str, window: str = "", ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Press keys inside a ghost window: enter, tab, escape, backspace, delete, arrows, home, end, page up/down,
    f1–f12, separated by spaces. Key combinations (Ctrl+S) aren't possible without the user's keyboard: use the
    app's menu by name with ghost_click instead.

    Args:
        keys: e.g. "enter" or "tab tab enter".
        window: Part of the ghost window's title (default: the newest ghost).
    """
    desk = _desk()
    g = _guard(lambda: desk.find(window))
    return _guard(lambda: desk.press(g, keys))


@tool(category="ghost", kit=KIT)
def ghost_windows() -> list[dict]:
    """List ORION's open ghost windows."""
    if not IS_WIN:
        return []
    from ..ghost.win32 import DESK

    return DESK.list()


@tool(risk="confirm", category="ghost", kit=KIT, summary=lambda a: f"Close ghost window {a.get('window') or ''}".strip())
def ghost_close(window: str = "", ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Close a ghost window when ORION's job in it is done (the app may ask to save first; look before closing).

    Args:
        window: Part of the ghost window's title (default: the newest ghost).
    """
    desk = _desk()
    g = _guard(lambda: desk.find(window))
    from ..ghost import office

    try:
        mine = office.close_office(g.hwnd)  # ORION's own Word/Excel: quit it without saving
    except Exception:  # noqa: BLE001
        mine = False
    out = desk.close(g) if not mine else f"Closed {g.title}."
    if mine:
        desk._run(lambda: desk._forget(g.hwnd))
    _say(ctx, out)
    return out

