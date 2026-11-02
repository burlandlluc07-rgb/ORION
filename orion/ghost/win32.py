"""Windows plumbing for ghost mode: ghost windows, the ghost cursor and the focus guard.

Everything here runs without touching the user's input:

* no SendInput, SetCursorPos, mouse_event or keybd_event anywhere in this file;
* windows are moved with SWP_NOACTIVATE, shown with SW_SHOWNOACTIVATE, and made WS_EX_NOACTIVATE;
* clicks and keys are *posted* to the target window's own message queue, or done through UI Automation;
* pictures come from PrintWindow, which draws a window into ORION's own bitmap whether or not it's in front.

A single UI thread owns ORION's overlay windows (the cursor and one frame per ghost), the WinEvent hooks and
the animation timer. Other threads hand it work through ``_run``.
"""

from __future__ import annotations

import ctypes
import io
import logging
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from . import core, sprites
from .core import Ghost, Rect

log = logging.getLogger("orion.ghost")

IS_WIN = sys.platform == "win32"

GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x40, 0x20
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
LWA_ALPHA = 0x2
ULW_ALPHA = 0x2
WM_TIMER, WM_APP, WM_CLOSE, WM_QUIT = 0x0113, 0x8000, 0x0010, 0x0012
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0202, 0x0203
EM_REPLACESEL, SMTO_ABORTIFHUNG, WM_NULL = 0x00C2, 0x0002, 0x0000
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_GETTEXT = 0x0204, 0x0205, 0x0100, 0x0101, 0x0102, 0x000D
MK_LBUTTON, MK_RBUTTON = 0x1, 0x2
EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_SHOW = 0x0003, 0x8002
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0, 0x2
CWP_SKIPINVISIBLE, CWP_SKIPDISABLED = 0x1, 0x2
GA_ROOT = 2
PW_RENDERFULLCONTENT = 0x2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
WDA_EXCLUDEFROMCAPTURE = 0x11
SEE_MASK_NOCLOSEPROCESS, SEE_MASK_FLAG_NO_UI = 0x40, 0x400
SPI_GETWORKAREA = 0x0030
FPS = 60

WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
                                  wintypes.DWORD, wintypes.DWORD) if IS_WIN else None
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM) if IS_WIN else None
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM) \
    if IS_WIN else None


class GhostError(RuntimeError):
    """Something ghost mode can't do without interrupting the user. The message says what to do instead."""


# ── ctypes declarations ─────────────────────────────────────────────────────
if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:  # pragma: no cover
        dwmapi = None

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte), ("SourceConstantAlpha", ctypes.c_ubyte),
                    ("AlphaFormat", ctypes.c_byte)]

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

    def _sig(fn, res, *args):
        fn.restype = res
        fn.argtypes = list(args)

    H, P = wintypes.HWND, ctypes.c_void_p
    _sig(user32.GetWindowLongW, wintypes.LONG, H, ctypes.c_int)
    _sig(user32.SetWindowLongW, wintypes.LONG, H, ctypes.c_int, wintypes.LONG)
    _sig(user32.SetLayeredWindowAttributes, wintypes.BOOL, H, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD)
    _sig(user32.SetWindowPos, wintypes.BOOL, H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    _sig(user32.ShowWindow, wintypes.BOOL, H, ctypes.c_int)
    _sig(user32.IsWindow, wintypes.BOOL, H)
    _sig(user32.IsWindowVisible, wintypes.BOOL, H)
    _sig(user32.IsIconic, wintypes.BOOL, H)
    _sig(user32.IsZoomed, wintypes.BOOL, H)
    _sig(user32.GetWindowRect, wintypes.BOOL, H, ctypes.POINTER(wintypes.RECT))
    _sig(user32.GetForegroundWindow, H)
    _sig(user32.SetForegroundWindow, wintypes.BOOL, H)
    _sig(user32.GetWindowThreadProcessId, wintypes.DWORD, H, ctypes.POINTER(wintypes.DWORD))
    _sig(user32.AttachThreadInput, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    _sig(user32.GetAncestor, H, H, wintypes.UINT)
    _sig(user32.GetWindowTextLengthW, ctypes.c_int, H)
    _sig(user32.GetWindowTextW, ctypes.c_int, H, wintypes.LPWSTR, ctypes.c_int)
    _sig(user32.GetClassNameW, ctypes.c_int, H, wintypes.LPWSTR, ctypes.c_int)
    _sig(user32.GetParent, H, H)
    _sig(user32.GetWindow, H, H, wintypes.UINT)
    _sig(user32.PostMessageW, wintypes.BOOL, H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _sig(user32.SendMessageTimeoutW, wintypes.LPARAM, H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
         wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    _sig(user32.ScreenToClient, wintypes.BOOL, H, ctypes.POINTER(wintypes.POINT))
    _sig(user32.ChildWindowFromPointEx, H, H, wintypes.POINT, wintypes.UINT)
    _sig(user32.GetGUIThreadInfo, wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
    _sig(user32.PrintWindow, wintypes.BOOL, H, wintypes.HDC, wintypes.UINT)
    _sig(user32.GetDC, wintypes.HDC, H)
    _sig(user32.ReleaseDC, ctypes.c_int, H, wintypes.HDC)
    _sig(user32.UpdateLayeredWindow, wintypes.BOOL, H, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
         ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
         ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
    _sig(user32.CreateWindowExW, H, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
         ctypes.c_int, ctypes.c_int, ctypes.c_int, H, wintypes.HMENU, wintypes.HINSTANCE, P)
    _sig(user32.DestroyWindow, wintypes.BOOL, H)
    _sig(user32.DefWindowProcW, ctypes.c_ssize_t, H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _sig(user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
    _sig(user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), H, wintypes.UINT, wintypes.UINT)
    _sig(user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
    _sig(user32.DispatchMessageW, ctypes.c_ssize_t, ctypes.POINTER(wintypes.MSG))
    _sig(user32.PostThreadMessageW, wintypes.BOOL, wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _sig(user32.SetTimer, ctypes.c_size_t, H, ctypes.c_size_t, wintypes.UINT, P)
    _sig(user32.SetWinEventHook, wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC,
         wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
    _sig(user32.SystemParametersInfoW, wintypes.BOOL, wintypes.UINT, wintypes.UINT, P, wintypes.UINT)
    _sig(user32.EnumWindows, wintypes.BOOL, ENUMPROC, wintypes.LPARAM)
    _sig(user32.EnumChildWindows, wintypes.BOOL, H, ENUMPROC, wintypes.LPARAM)
    _sig(user32.IsChild, wintypes.BOOL, H, H)
    _sig(user32.IsHungAppWindow, wintypes.BOOL, H)
    _sig(user32.LockSetForegroundWindow, wintypes.BOOL, wintypes.UINT)
    _sig(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
    _sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
    _sig(gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int)
    _sig(gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
         ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    _sig(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
    _sig(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
    _sig(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
    _sig(gdi32.GetDIBits, ctypes.c_int, wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, P,
         ctypes.POINTER(BITMAPINFO), wintypes.UINT)
    _sig(kernel32.GetCurrentThreadId, wintypes.DWORD)
    _sig(kernel32.GetProcessId, wintypes.DWORD, wintypes.HANDLE)
    _sig(kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
    _sig(kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _sig(kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
         ctypes.POINTER(wintypes.DWORD))
    _sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
    _sig(shell32.ShellExecuteExW, wintypes.BOOL, ctypes.POINTER(SHELLEXECUTEINFOW))
    try:
        _sig(user32.SetWindowDisplayAffinity, wintypes.BOOL, H, wintypes.DWORD)
    except AttributeError:  # pragma: no cover
        pass


# ── Small helpers ───────────────────────────────────────────────────────────
def _text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _rect(hwnd: int) -> Rect:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Rect(r.left, r.top, r.right, r.bottom)


def _frame_rect(hwnd: int) -> Rect:
    """The visible window edge, without the invisible resize shadow that GetWindowRect includes."""
    if dwmapi is not None:
        r = wintypes.RECT()
        if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r),
                                        ctypes.sizeof(r)) == 0:
            return Rect(r.left, r.top, r.right, r.bottom)
    return _rect(hwnd)


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _thread(hwnd: int) -> int:
    return user32.GetWindowThreadProcessId(hwnd, None)


def _exe(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        kernel32.CloseHandle(h)


def _work_area() -> Rect:
    r = wintypes.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return Rect(r.left, r.top, r.right, r.bottom)


def _top_windows() -> list[int]:
    out: list[int] = []
    cb = ENUMPROC(lambda h, _l: out.append(h or 0) or True)
    user32.EnumWindows(cb, 0)
    return [h for h in out if h]


def _is_app_window(hwnd: int) -> bool:
    """A real, visible main window (not a tooltip, owned dialog or ORION's own overlay)."""
    if not (hwnd and user32.IsWindowVisible(hwnd)) or user32.GetAncestor(hwnd, GA_ROOT) != hwnd:
        return False
    if user32.GetWindow(hwnd, 4):  # GW_OWNER: owned popups ride along with their owner
        return False
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    if ex & core.WS_EX_TOOLWINDOW or _pid(hwnd) == os.getpid():
        return False
    r = _rect(hwnd)
    return r.width >= 200 and r.height >= 120 and bool(_text(hwnd) or user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION)


def _children(hwnd: int) -> list[int]:
    out: list[int] = []
    cb = ENUMPROC(lambda h, _l: out.append(h or 0) or True)
    user32.EnumChildWindows(hwnd, cb, 0)
    return [h for h in out if h]


def _ancestry_classes(hwnd: int) -> list[str]:
    out = []
    h = hwnd
    for _ in range(12):
        if not h:
            break
        out.append(_class(h))
        h = user32.GetParent(h)
    return out


def give_focus_back(prev: int) -> bool:
    """Hand the foreground back to the user's window after something grabbed it. This is undoing an interruption,
    not causing one: it never touches the mouse or keyboard."""
    if not prev or not user32.IsWindow(prev):
        return False
    fg = user32.GetForegroundWindow()
    me, them = kernel32.GetCurrentThreadId(), _thread(fg) if fg else 0
    attached = bool(them and them != me and user32.AttachThreadInput(me, them, True))
    try:
        return bool(user32.SetForegroundWindow(prev))
    finally:
        if attached:
            user32.AttachThreadInput(me, them, False)


def responsive(hwnd: int, ms: int = 2500) -> bool:
    """True when the window's thread answers a no-op message in time. PrintWindow would otherwise wait forever on
    an app that is busy or hung."""
    if user32.IsHungAppWindow(hwnd):
        return False
    res = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, ms, ctypes.byref(res)))


def capture(hwnd: int, max_side: int = 1600) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    """A picture of one window via PrintWindow (works behind other windows and needs no focus).

    Returns (jpeg, picture size, window size); the picture is the visible frame, scaled to fit ``max_side``."""
    from PIL import Image

    if not responsive(hwnd):
        raise GhostError(f"{_text(hwnd) or 'That window'} isn't answering right now (it may be busy or showing a "
                         "message). Try again in a moment.")
    outer, frame = _rect(hwnd), _frame_rect(hwnd)
    w, h = outer.width, outer.height
    if w <= 0 or h <= 0:
        raise GhostError("That window has no size (is it minimised?).")
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            raise GhostError("Windows couldn't draw that window for ORION.")
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, screen)
    crop = (frame.left - outer.left, frame.top - outer.top, frame.right - outer.left, frame.bottom - outer.top)
    if 0 <= crop[0] < crop[2] <= w and 0 <= crop[1] < crop[3] <= h:
        img = img.crop(crop)
    size = img.size
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue(), img.size, size


def deepest_child(root: int, sx: int, sy: int) -> tuple[int, int, int]:
    """The child window under a screen point, found by walking down from ``root`` (WindowFromPoint can't be used:
    a ghost is click-through, so Windows would hand back whatever is underneath it). Returns (hwnd, x, y) in that
    window's client coordinates."""
    h = root
    for _ in range(32):
        pt = wintypes.POINT(sx, sy)
        user32.ScreenToClient(h, ctypes.byref(pt))
        child = user32.ChildWindowFromPointEx(h, pt, CWP_SKIPINVISIBLE | CWP_SKIPDISABLED)
        if not child or child == h:
            break
        h = child
    pt = wintypes.POINT(sx, sy)
    user32.ScreenToClient(h, ctypes.byref(pt))
    return h, pt.x, pt.y


# ── The ghost desk: one UI thread for overlays, hooks and animation ─────────
class GhostDesk:
    def __init__(self) -> None:
        self.ghosts: dict[int, Ghost] = {}
        self.opacity = core.DEFAULT_OPACITY
        self.corner = "full"  # full screen like a maximised window; "natural" = where the app put itself; a corner
        self.show_frame = False  # no outline: a ghost is just see-through glass
        self.show_cursor = True
        self.on_event: Callable[[str, str], None] = lambda kind, text: None  # → ORION's thoughts / activity
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._hooks: list = []
        self._procs: list = []
        self._frames: dict[int, int] = {}  # ghost hwnd → its frame overlay hwnd
        self._pills: dict[int, int] = {}  # ghost hwnd → its "ORION is …" activity pill overlay
        self._pill_keys: dict[int, tuple] = {}
        self._frame_keys: dict[int, tuple] = {}
        self._cursor = 0
        self._cur = (0.0, 0.0)
        self._path: list[tuple[int, int]] = []
        self._trail: list[tuple[int, int]] = []
        self._arrived = threading.Event()
        self._ripple = -1.0
        self._last_move = 0.0
        self._cursor_alpha = 0
        self._user_fg = 0
        self._pending: dict | None = None
        self._t0 = time.monotonic()
        self._tick = 0
        self._hide_from_capture = os.environ.get("ORION_GHOST_SHOW_IN_CAPTURE") != "1"

    # ---- thread plumbing ----
    def _ensure(self) -> None:
        if not IS_WIN:
            raise GhostError("Ghost mode needs Windows.")
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._main, name="ghost-desk", daemon=True)
            self._thread.start()
        if not self._ready.wait(5):
            raise GhostError("Ghost mode's overlay didn't start.")

    def _run(self, fn: Callable, wait: bool = True, timeout: float = 10.0):
        """Run ``fn`` on the UI thread (the only thread that may touch ORION's overlay windows)."""
        self._ensure()
        if threading.get_ident() == getattr(self._thread, "ident", None):
            return fn()
        done = threading.Event()
        box: dict = {}

        def job():
            try:
                box["v"] = fn()
            except BaseException as exc:  # handed back to the caller
                box["e"] = exc
            finally:
                done.set()

        self._jobs.put(job)
        user32.PostThreadMessageW(self._tid, WM_APP, 0, 0)
        if not wait:
            return None
        if not done.wait(timeout):
            raise GhostError("Ghost mode timed out.")
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def _main(self) -> None:
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: real pixels everywhere
        except Exception:
            pass
        self._tid = kernel32.GetCurrentThreadId()
        hinst = kernel32.GetModuleHandleW(None)
        proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        self._procs.append(proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.lpszClassName = "OrionGhostOverlay"
        user32.RegisterClassExW(ctypes.byref(wc))
        self._hinst = hinst
        for ev in (EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_SHOW):
            cb = WINEVENTPROC(self._on_winevent)
            self._procs.append(cb)
            self._hooks.append(user32.SetWinEventHook(ev, ev, None, cb, 0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS))
        self._user_fg = user32.GetForegroundWindow() or 0
        user32.SetTimer(None, 0, 1000 // FPS, None)
        msg = wintypes.MSG()
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP:
                self._drain()
                continue
            if msg.message == WM_TIMER and not msg.hWnd:
                self._drain()
                self._frame_tick()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _drain(self) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            job()

    def _overlay(self, hide_from_capture: bool = True) -> int:
        ex = core.WS_EX_LAYERED | core.WS_EX_TRANSPARENT | core.WS_EX_TOPMOST | core.WS_EX_TOOLWINDOW | core.WS_EX_NOACTIVATE
        h = user32.CreateWindowExW(ex, "OrionGhostOverlay", "ORION ghost", WS_POPUP, 0, 0, 1, 1, None, None,
                                   self._hinst, None)
        if h and hide_from_capture and self._hide_from_capture:
            try:  # keep ORION's own overlays out of screenshots, including the ones he takes himself
                user32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE)
            except Exception:
                pass
        return h or 0

    @staticmethod
    def _blit(hwnd: int, img, x: int, y: int, alpha: int = 255) -> None:
        w, h = img.size
        data = sprites.bgra_premultiplied(img)
        screen = user32.GetDC(0)
        mem = gdi32.CreateCompatibleDC(screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, data, len(data))
            old = gdi32.SelectObject(mem, bmp)
            blend = BLENDFUNCTION(0, 0, max(0, min(255, alpha)), 1)
            user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wintypes.POINT(x, y)), ctypes.byref(wintypes.SIZE(w, h)),
                                       mem, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
            gdi32.SelectObject(mem, old)
        finally:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(0, screen)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

    # ---- focus guard + catching a launched app's window ----
    def _on_winevent(self, _hook, event, hwnd, id_object, id_child, _thread, _time) -> None:
        try:
            if not hwnd or id_object != 0 or id_child != 0:
                return
            root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
            if event == EVENT_SYSTEM_FOREGROUND:
                if root in self.ghosts:
                    # A ghost grabbed the foreground (some apps call SetForegroundWindow on themselves): undo it.
                    self._make_unfocusable(root)
                    give_focus_back(self._user_fg)
                    self.on_event("ghost", f"{self.ghosts[root].title} tried to take focus; handed it straight back")
                elif _pid(root) != os.getpid():
                    self._user_fg = root
            elif event == EVENT_OBJECT_SHOW and root not in self.ghosts:
                if self._pending and self._matches_pending(root):
                    self._adopt(root)
                elif (parent := self._spawned_by_ghost(root)) is not None:
                    self._adopt(root, app=parent.app)  # the app's next window: Word's document, a dialog, …
        except Exception:  # a hook must never throw into Windows
            log.debug("ghost hook failed", exc_info=True)

    def _spawned_by_ghost(self, hwnd: int) -> Ghost | None:
        """A new window from a program ORION is working in, shortly after he acted in it. One process can host your
        windows too (Windows 11 Notepad), so only windows that appear while ORION is busy there are taken."""
        if not _is_app_window(hwnd):
            return None
        pid, now = _pid(hwnd), time.monotonic()
        for g in self.ghosts.values():
            if g.pid == pid and now < g.flags.get("busy_until", 0):
                return g
        return None

    def busy(self, g: Ghost, seconds: float = core.FOLLOW_WINDOWS_S, doing: str = "") -> None:
        """ORION is working in ``g``: follow its new windows for a while, and show ``doing`` on it."""
        now = time.monotonic()
        g.flags["busy_until"] = now + seconds
        if doing:
            g.flags["doing"] = doing
            g.flags["doing_until"] = now + core.ACTIVITY_S

    def adopt(self, hwnd: int, app: str = "") -> Ghost:
        """Make an existing window (e.g. the Word window ORION's own Word opened) a ghost."""
        return self._run(lambda: self._adopt(hwnd, app))

    def _matches_pending(self, hwnd: int) -> bool:
        p = self._pending
        if not p or hwnd in p["before"] or not _is_app_window(hwnd):
            return False
        pid = _pid(hwnd)
        if p["pid"] and pid == p["pid"]:
            return True
        # Launchers hand over to another process (Store apps, Notepad on Windows 11): accept a brand-new window
        # only when its program's name matches what ORION opened, so a window *you* open meanwhile is left alone.
        exe = Path(_exe(pid)).stem.lower()
        return bool(exe) and any(k and (k in exe or exe in k) for k in p["keys"])

    def _make_unfocusable(self, hwnd: int) -> None:
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        want = core.ghost_style(ex)
        if want != ex:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(want).value)

    def _adopt(self, hwnd: int, app: str = "") -> Ghost:
        """Turn a window into a ghost: see-through, click-through, unfocusable, out of the taskbar. It stays where and
        how big the app made it, like a window you opened yourself, unless a corner is set."""
        if hwnd in self.ghosts:
            return self.ghosts[hwnd]
        was_fg = user32.GetForegroundWindow() == hwnd
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        g = Ghost(hwnd=hwnd, title=_text(hwnd) or "app", pid=_pid(hwnd),
                  app=app or (self._pending["app"] if self._pending else ""),
                  original_style=ex, original_rect=_rect(hwnd).as_tuple(), opened=time.time())
        self.ghosts[hwnd] = g
        self.busy(g, doing=f"ORION is opening {g.app or g.title}")
        if self._pending:
            self._pending["hwnd"] = hwnd
            self._pending["done"].set()
        from ..tools.apps import _quiet_window_action

        if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
            _quiet_window_action(hwnd, "restore")
        user32.ShowWindow(hwnd, SW_HIDE)  # the taskbar only notices the new style on the next show
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(core.ghost_style(ex)).value)
        user32.SetLayeredWindowAttributes(hwnd, 0, core.alpha_byte(self.opacity), LWA_ALPHA)
        if self.corner == "full":
            full = core.full_rect(_work_area())
            user32.SetWindowPos(hwnd, HWND_TOPMOST, full.left, full.top, full.width, full.height,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)
        elif self.corner in core.CORNERS:
            r = _rect(hwnd)
            work = _work_area()
            size = core.ghost_size((r.width, r.height), work)
            slot = core.slot_rect(len(self.ghosts) - 1, size, work, self.corner)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, slot.left, slot.top, slot.width, slot.height,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)
        else:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        g.rect = _rect(hwnd).as_tuple()
        if was_fg:
            give_focus_back(self._user_fg)
        if self.show_frame:
            self._frames[hwnd] = self._overlay()
        self.on_event("ghost", f"Opened {g.title} as a ghost window")
        return g

    def _frame_tick(self) -> None:
        self._tick += 1
        t = time.monotonic() - self._t0
        self._cursor_step(t)
        if self._tick % 4:  # frames refresh ~15 times a second
            return
        for hwnd in list(self.ghosts):
            g = self.ghosts[hwnd]
            fr = self._frames.get(hwnd)
            if not user32.IsWindow(hwnd):
                self._forget(hwnd)
                continue
            r = _frame_rect(hwnd)
            g.rect = r.as_tuple()
            g.title = _text(hwnd) or g.title
            self._pill_tick(g, r, t)
            if not fr:
                continue
            if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(fr, SW_HIDE)
                continue
            pad, label = 10, 26
            key = (r.as_tuple(), g.title, int(t * 4) % 60)  # the glow breathes at 4 fps: cheap, still alive
            if self._frame_keys.get(hwnd) == key:
                continue
            self._frame_keys[hwnd] = key
            img = sprites.frame(r.width, r.height, g.title, t=t, pad=pad, label_h=label)
            self._blit(fr, img, r.left - pad, r.top - pad - label)
            user32.SetWindowPos(fr, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _pill_tick(self, g: Ghost, r: Rect, t: float) -> None:
        """Show what ORION is doing on the ghost while he works in it; hide it a few seconds after he stops."""
        hwnd = g.hwnd
        show = (time.monotonic() < g.flags.get("doing_until", 0) and not user32.IsIconic(hwnd)
                and user32.IsWindowVisible(hwnd))
        pill = self._pills.get(hwnd)
        if not show:
            if pill:
                user32.ShowWindow(pill, SW_HIDE)
            return
        if not pill:
            pill = self._pills[hwnd] = self._overlay(hide_from_capture=False)  # you can screenshot it too
        text = g.flags.get("doing") or f"ORION is working in {g.title}"
        key = (r.as_tuple(), text, int(t * 6) % 60)
        if self._pill_keys.get(hwnd) == key:
            return
        self._pill_keys[hwnd] = key
        img = sprites.activity(text[:70], t)
        x, y = core.pill_pos(r.as_tuple(), img.size)
        self._blit(pill, img, x, y)
        user32.SetWindowPos(pill, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _forget(self, hwnd: int) -> None:
        pill = self._pills.pop(hwnd, 0)
        self._pill_keys.pop(hwnd, None)
        if pill:
            user32.DestroyWindow(pill)
        self.ghosts.pop(hwnd, None)
        fr = self._frames.pop(hwnd, 0)
        self._frame_keys.pop(hwnd, None)
        if fr:
            user32.DestroyWindow(fr)

    # ---- ghost cursor ----
    def _cursor_step(self, t: float) -> None:
        if not self.show_cursor:
            return
        moving = bool(self._path)
        if moving:
            self._cur = self._path.pop(0)
            self._trail = (self._trail + [self._cur])[-12:]
            self._last_move = time.monotonic()
            if not self._path:
                self._arrived.set()
        elif self._trail:
            self._trail = self._trail[1:]
        if self._ripple >= 0:
            self._ripple += 1 / (FPS * 0.45)
            if self._ripple > 1:
                self._ripple = -1.0
        idle = time.monotonic() - self._last_move
        target = 255 if idle < 2.5 or self._ripple >= 0 else max(0, 255 - int((idle - 2.5) * 300))
        if not self._cursor:
            if not moving:
                return
            self._cursor = self._overlay()
        if target == 0 and self._cursor_alpha == 0:
            return
        self._cursor_alpha = target
        x, y = self._cur
        rel = [(px - x, py - y) for px, py in self._trail[:-1]]
        img = sprites.cursor(rel, self._ripple, t)
        self._blit(self._cursor, img, int(x) - sprites.HOT[0], int(y) - sprites.HOT[1], target)
        if target == 0:
            user32.ShowWindow(self._cursor, SW_HIDE)

    def glide(self, x: int, y: int, click: bool = False) -> None:
        """Move ORION's ghost cursor to a screen point (and ripple for a click). Waits until it gets there.
        The real pointer is never moved."""
        if not self.show_cursor or not IS_WIN:
            return
        self._ensure()

        def start():
            if not self._cursor_alpha and not self._path:
                # Appear near the target rather than sweeping across the whole screen.
                self._cur = (x - 120, y + 80)
            self._arrived.clear()
            self._path = core.glide_path(self._cur, (x, y), core.glide_steps(self._cur, (x, y), FPS))

        self._run(start)
        self._arrived.wait(1.5)
        if click:
            self._run(lambda: setattr(self, "_ripple", 0.0))
            time.sleep(0.12)

    # ---- opening apps as ghosts ----
    def open(self, target: str, app: str, keys: list[str], timeout: float = 20.0) -> Ghost:
        self._ensure()
        done = threading.Event()

        def arm():
            self._pending = {"pid": 0, "before": set(_top_windows()), "keys": [k.lower() for k in keys if k],
                             "app": app, "done": done, "hwnd": 0}

        self._run(arm)
        locked = False
        try:
            # If ORION itself is in front (you just typed to him), lock the foreground so the new app can't
            # jump in front of you while it starts. Released as soon as the window is a ghost.
            locked = bool(user32.LockSetForegroundWindow(1))
            sei = SHELLEXECUTEINFOW()
            sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
            sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
            sei.lpVerb = "open"
            sei.lpFile = target
            sei.nShow = SW_SHOWNOACTIVATE
            if not shell32.ShellExecuteExW(ctypes.byref(sei)):
                raise GhostError(f"Windows couldn't start {app}.")
            if sei.hProcess:
                pid = kernel32.GetProcessId(sei.hProcess)
                kernel32.CloseHandle(sei.hProcess)
                self._run(lambda: self._pending and self._pending.__setitem__("pid", pid))
            deadline = time.monotonic() + timeout
            while not done.wait(0.25):
                if time.monotonic() > deadline:
                    raise GhostError(f"{app} started but no window appeared within {int(timeout)} s.")

                def sweep():  # in case the show event was missed
                    for h in _top_windows():
                        if self._pending and h not in self.ghosts and self._matches_pending(h):
                            self._adopt(h)
                            return

                self._run(sweep)
            return self.ghosts[self._pending["hwnd"]] if self._pending else self._latest()
        finally:
            if locked:
                user32.LockSetForegroundWindow(2)
            self._run(lambda: setattr(self, "_pending", None))

    def _latest(self) -> Ghost:
        return max(self.ghosts.values(), key=lambda g: g.opened)

    # ---- finding and using ghosts ----
    def find(self, window: str | int | None) -> Ghost:
        live = {h: g for h, g in self.ghosts.items() if IS_WIN and user32.IsWindow(h)}
        if not live:
            raise GhostError("ORION has no ghost windows open. Use ghost_open first.")
        if window in (None, ""):
            return max(live.values(), key=lambda g: g.opened)
        if isinstance(window, int) or str(window).isdigit():
            g = live.get(int(window))
            if g:
                return g
        q = str(window).lower()
        for g in sorted(live.values(), key=lambda g: -g.opened):
            if q in g.title.lower() or q in g.app.lower():
                return g
        raise GhostError(f"No ghost window matches “{window}”. Open ones: " + ", ".join(g.title for g in live.values()))

    def look(self, g: Ghost) -> tuple[bytes, tuple[int, int]]:
        self.busy(g, doing=f"ORION is reading {g.title}")
        jpeg, shot, win = capture(g.hwnd)
        g.shot_size = shot
        g.flags["win_size"] = win
        return jpeg, shot

    def click(self, g: Ghost, x: float, y: float, button: str = "left", double: bool = False) -> str:
        frame = _frame_rect(g.hwnd)
        win = g.flags.get("win_size") or (frame.width, frame.height)
        px, py = core.scale_point(x, y, g.shot_size or win, win)
        sx, sy = frame.left + px, frame.top + py
        target, cx, cy = deepest_child(g.hwnd, sx, sy)
        if not core.posted_clicks_work(_ancestry_classes(target)):
            raise GhostError(f"{g.title} ignores background clicks on its content. Use ghost_click with a control's "
                             "name instead, or ask the user before doing it any other way.")
        self.glide(sx, sy, click=True)
        lp = core.make_lparam(cx, cy)
        down, up, flag = (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON) if button == "right" else \
            (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON)
        user32.PostMessageW(target, WM_MOUSEMOVE, 0, lp)
        user32.PostMessageW(target, down, flag, lp)
        user32.PostMessageW(target, up, 0, lp)
        if double:
            user32.PostMessageW(target, WM_LBUTTONDBLCLK, MK_LBUTTON, lp)
            user32.PostMessageW(target, WM_LBUTTONUP, 0, lp)
        g.flags["last_target"] = target
        self.busy(g, doing=f"ORION is clicking in {g.title}")
        g.last_action = f"clicked at {px},{py}"
        return f"Clicked {g.title} at ({px}, {py}) with the ghost cursor."

    def point_at(self, rect) -> None:
        """Glide the ghost cursor to the middle of a control's rectangle (a UI Automation rect)."""
        try:
            self.glide((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2, click=True)
        except Exception:
            pass

    def focus_target(self, g: Ghost) -> int:
        """The control that has keyboard focus *inside* the ghost's own thread. Every thread keeps its own focus,
        so this is not your focus: typing there never reaches the window you're using. A ghost is never activated,
        so its thread often has no focus at all; then the control ORION last clicked, or the window's text box."""
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        inside = lambda h: bool(h) and h != g.hwnd and bool(user32.IsChild(g.hwnd, h))
        if user32.GetGUIThreadInfo(_thread(g.hwnd), ctypes.byref(info)) and inside(info.hwndFocus):
            return info.hwndFocus
        last = g.flags.get("last_target")
        if inside(last) and core.is_edit_class(_class(last)):
            return last
        for h in _children(g.hwnd):
            if core.is_edit_class(_class(h)) and user32.IsWindowVisible(h):
                return h
        return last if inside(last) else g.hwnd

    def type(self, g: Ghost, text: str) -> str:
        target = self.focus_target(g)
        cls = _class(target)
        g.flags["type_target"] = cls
        if core.takes_replacesel(cls):
            # Standard text boxes take text straight in with EM_REPLACESEL, at their own caret: no keys at all.
            buf = ctypes.create_unicode_buffer(core.edit_text(text))
            res = ctypes.c_size_t()
            ok = user32.SendMessageTimeoutW(target, EM_REPLACESEL, 1, ctypes.cast(buf, ctypes.c_void_p).value,
                                            SMTO_ABORTIFHUNG, 3000, ctypes.byref(res))
            if not ok:
                raise GhostError(f"{g.title} didn't answer in time, so nothing was typed.")
        else:
            if not core.posted_clicks_work(_ancestry_classes(target)):
                raise GhostError(f"{g.title} doesn't take background typing. Use ghost_type with a field's name instead.")
            for unit in core.utf16_units(text):
                user32.PostMessageW(target, WM_CHAR, unit, 1)
        self.busy(g, doing=f"ORION is typing in {g.title}")
        g.last_action = f"typed {len(text)} characters"
        return f"Typed {len(text)} characters into {g.title}."

    def press(self, g: Ghost, keys: str) -> str:
        codes = core.parse_keys(keys)
        target = self.focus_target(g)
        edit = core.takes_replacesel(_class(target))
        for vk in codes:
            if edit and vk == core.VK["enter"]:
                self.type(g, "\n")
                continue
            user32.PostMessageW(target, WM_KEYDOWN, vk, core.key_lparam(0, False))
            if vk in core.KEY_CHARS:
                user32.PostMessageW(target, WM_CHAR, ord(core.KEY_CHARS[vk]), core.key_lparam(0, False))
            user32.PostMessageW(target, WM_KEYUP, vk, core.key_lparam(0, True))
        self.busy(g, doing=f"ORION is pressing {keys} in {g.title}")
        g.last_action = f"pressed {keys}"
        return f"Pressed {keys} in {g.title}."

    def take_over(self, g: Ghost) -> str:
        """Hand a ghost back to the user as a normal window. Only ever called from the user's own button."""
        def undo():
            hwnd = g.hwnd
            self._forget(hwnd)
            user32.ShowWindow(hwnd, SW_HIDE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(g.original_style).value)
            if g.original_style & core.WS_EX_LAYERED:  # it was layered before ORION: just make it solid again
                user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
            l, t, r, b = g.original_rect
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, l, t, r - l, b - t, SWP_SHOWWINDOW | SWP_FRAMECHANGED)
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)  # you pressed Take over, so it comes to you
        self._run(undo)
        return f"{g.title} is yours now."

    def close(self, g: Ghost) -> str:
        user32.PostMessageW(g.hwnd, WM_CLOSE, 0, 0)
        self._run(lambda: self._forget(g.hwnd))
        return f"Closed {g.title}."

    def list(self) -> list[dict]:
        return [g.to_dict() for g in self.ghosts.values() if IS_WIN and user32.IsWindow(g.hwnd)]

    def configure(self, opacity: float | None = None, corner: str | None = None, cursor: bool | None = None,
                  frame: bool | None = None) -> None:
        if opacity is not None:
            self.opacity = core.clamp_opacity(opacity)
            if IS_WIN and self.ghosts:
                self._run(lambda: [user32.SetLayeredWindowAttributes(h, 0, core.alpha_byte(self.opacity), LWA_ALPHA)
                                   for h in self.ghosts])
        if corner in (*core.CORNERS, *core.LAYOUTS):
            self.corner = corner
        if frame is not None:
            self.show_frame = bool(frame)
        if cursor is not None:
            self.show_cursor = bool(cursor)


DESK = GhostDesk()


def window_text(hwnd: int) -> str:
    """Read a control's text back (WM_GETTEXT), for checks."""
    buf = ctypes.create_unicode_buffer(4096)
    user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, 4096, ctypes.cast(buf, ctypes.c_void_p).value, 0x2, 2000, None)
    return buf.value


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y
