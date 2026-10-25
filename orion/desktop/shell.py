"""Native desktop shell: main window, Quick Bar, tray icon, hotkeys, autostart."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any

from ..config import Config

log = logging.getLogger("orion.shell")


def icon_path(ext: str = "png") -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / f"orion.{ext}"


class DesktopShell:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.server: Any = None
        self.url = ""
        self.window: Any = None
        self.quick: Any = None
        self.notch: Any = None
        self._notch_mode = "idle"
        self._notch_hidden = False
        self.tray: Any = None
        self.hotkeys: Any = None
        self._quitting = False

    @staticmethod
    def has_webview() -> bool:
        try:
            import webview  # noqa: F401

            return True
        except Exception:
            return False

    def attach(self, server, url: str) -> None:
        self.server = server
        self.url = url
        server.engine.notifier = self.notify

    # ── Windows ────────────────────────────────────────────────────────────
    def run(self, start_hidden: bool = False) -> None:
        import webview

        width, height, x, y = self._initial_geometry()
        self.window = webview.create_window(
            "ORION", self.url, width=width, height=height, x=x, y=y, min_size=(760, 520), background_color="#000000",
            hidden=start_hidden, text_select=True,
        )
        if self.config.settings.notch:
            # The notch replaces the separate Quick Bar window: same hotkey, one small window instead of two.
            from . import notch

            x, y, w, h = self._notch_geometry("idle")
            self.notch = webview.create_window(
                notch.TITLE, self.url + "#/notch", width=w, height=h, x=x, y=y, frameless=True, on_top=True,
                transparent=True, focus=False, shadow=False, resizable=False, easy_drag=False,
                background_color="#000000",
            )
            self.notch.events.shown += lambda: threading.Thread(target=self._watch_fullscreen, name="notch", daemon=True).start()
        else:
            self.quick = webview.create_window(
                "ORION Quick", self.url + "#/quick", width=720, height=440, frameless=True, on_top=True,
                hidden=True, background_color="#000000", easy_drag=True, resizable=False,
            )
        self.window.events.closing += self._on_closing
        self.window.events.shown += self.apply_titlebar
        self._start_tray()
        self._start_hotkeys()
        storage = self.config.root / "webview"
        try:
            webview.start(gui=None, private_mode=False, storage_path=str(storage), debug="--debug" in sys.argv)
        except Exception:
            # e.g. the Microsoft Edge WebView2 runtime is missing: keep ORION usable in the browser.
            log.exception("Native window failed; falling back to the default browser")
            import webbrowser

            self.window = self.quick = self.notch = None
            webbrowser.open(self.url)
            self.notify("ORION", "Opened in your browser. Install the Microsoft Edge WebView2 Runtime for the app window.")
            import time

            while not self._quitting and self.server.is_alive():
                time.sleep(0.5)
        self._cleanup()

    @staticmethod
    def _initial_geometry() -> tuple[int, int, int | None, int | None]:
        """Fit and centre the window on the primary screen (a fixed 1320 px window was cut off on a 1024×768 VM)."""
        try:
            import webview

            scr = webview.screens[0]
            w, h = min(1320, int(scr.width * 0.9)), min(860, int(scr.height * 0.88))
            return w, h, int(getattr(scr, "x", 0) + (scr.width - w) / 2), int(getattr(scr, "y", 0) + (scr.height - h) / 2)
        except Exception:
            return 1200, 780, None, None

    def _on_closing(self) -> bool:
        if self._quitting or not self.config.settings.close_to_tray or self.tray is None:
            self._quitting = True
            for extra in (self.quick, self.notch):
                if extra:
                    try:
                        extra.destroy()
                    except Exception:
                        pass
            return True
        self.window.hide()
        return False

    def apply_titlebar(self) -> None:
        """Match the native title bar to the app theme instead of Windows' default white bar."""
        if not self.window or sys.platform != "win32":
            return
        try:
            from . import titlebar

            titlebar.style(titlebar.find_window("ORION"), titlebar.is_dark(self.config.settings.theme))
        except Exception:
            log.exception("Couldn't theme the title bar")

    def show_main(self) -> None:
        if self.window:
            self.window.show()
            self.window.restore()
            self.apply_titlebar()  # also picks up a Windows light/dark switch while ORION was hidden

    def toggle_quick(self) -> None:
        if self.notch:
            self.notch.evaluate_js("window.__orionNotch && window.__orionNotch('toggle')")
            return
        if not self.quick:
            return
        if getattr(self, "_quick_visible", False):
            self.quick.hide()
            self._quick_visible = False
            return
        try:
            import webview

            scr = webview.screens[0]
            self.quick.move(int(scr.x + (scr.width - 720) / 2), int(scr.y + scr.height * 0.18))
        except Exception:
            pass
        self.quick.show()
        self.quick.evaluate_js("window.__orionQuickFocus && window.__orionQuickFocus()")
        self._quick_visible = True

    def window_action(self, action: str) -> None:
        if action == "show":
            self.show_main()
        elif action == "hide":
            self.window and self.window.hide()
        elif action == "minimize":
            self.window and self.window.minimize()
        elif action == "maximize":
            if self.window:
                self.window.show()
                self.window.maximize()
        elif action == "quick":
            self.toggle_quick()
        elif action == "hide-quick":
            if self.quick:
                self.quick.hide()
                self._quick_visible = False
        elif action == "open-main-from-quick":
            if self.quick:
                self.quick.hide()
                self._quick_visible = False
            self.show_main()
        elif action.startswith("notch-"):
            self.notch_action(action[len("notch-"):])
        elif action == "quit":
            threading.Thread(target=self.quit, daemon=True).start()

    # ── Notch ──────────────────────────────────────────────────────────────
    def _notch_geometry(self, mode: str) -> tuple[int, int, int, int]:
        from . import notch

        try:
            import webview

            scr = webview.screens[0]
            return notch.place(mode, getattr(scr, "x", 0), getattr(scr, "y", 0), scr.width)
        except Exception:
            w, h = notch.size_for(mode)
            return 0, 0, w, h

    def notch_action(self, what: str) -> None:
        """idle | peek | open | focus | blur | open-main. The page picks the mode, this sizes the window for it."""
        if not self.notch:
            return
        from . import notch, titlebar

        if what == "open-main":
            self.notch.evaluate_js("window.__orionNotch && window.__orionNotch('close')")
            self.show_main()
            return
        if what in ("focus", "blur"):
            notch.set_activatable(titlebar.find_window(notch.TITLE), what == "focus")
            return
        if what not in notch.SIZES:
            return
        self._notch_mode = what
        try:
            if not notch.apply(titlebar.find_window(notch.TITLE), what):
                x, y, w, h = self._notch_geometry(what)
                self.notch.resize(w, h)
                self.notch.move(x, y)
        except Exception:
            log.exception("Couldn't resize the notch")

    def _watch_fullscreen(self) -> None:
        """Hide the pill while a full-screen game, film or presentation is in front; bring it back after."""
        import time

        from . import notch, titlebar

        hwnd = titlebar.find_window(notch.TITLE)
        notch.apply(hwnd, self._notch_mode)  # also pins it flush to the top on high-DPI screens
        while not self._quitting and self.notch and hwnd:
            hide = notch.fullscreen_app_in_front() and self._notch_mode == "idle"
            if hide != self._notch_hidden and notch.set_visible(hwnd, not hide):
                self._notch_hidden = hide
            time.sleep(1.5)

    def quit(self) -> None:
        self._quitting = True
        if self.window:
            self.window.destroy()
        else:
            self._cleanup()

    def _cleanup(self) -> None:
        if self.hotkeys:
            self.hotkeys.stop()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass

    # ── Tray ───────────────────────────────────────────────────────────────
    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image
        except Exception as exc:
            log.info("Tray unavailable: %s", exc)
            return
        try:
            image = Image.open(icon_path("png"))
        except Exception:
            image = Image.new("RGBA", (64, 64), (76, 201, 255, 255))

        def voice(_icon=None, _item=None):
            eng = self.server.engine
            if eng.voice:
                self.server.call(eng.voice.toggle)

        menu = pystray.Menu(
            pystray.MenuItem("Open ORION", lambda *_: self.show_main(), default=True),
            pystray.MenuItem("Ask ORION", lambda *_: self.toggle_quick()),
            pystray.MenuItem("Talk to ORION", voice),
            pystray.MenuItem("Stop all tasks", lambda *_: self.server.engine.stop_everything()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: self.quit()),
        )
        self.tray = pystray.Icon("ORION", image, "ORION", menu)
        threading.Thread(target=self.tray.run, name="tray", daemon=True).start()

    def run_tray_blocking(self) -> None:
        """Browser mode: keep the process alive via the tray (or until Ctrl+C)."""
        self._start_tray()
        self._start_hotkeys()
        import time

        while not self._quitting and self.server.is_alive():
            time.sleep(0.5)

    def notify(self, title: str, message: str) -> None:
        if self.tray is not None:
            try:
                self.tray.notify(message[:250], title[:60])
            except Exception:
                pass

    # ── Hotkeys ────────────────────────────────────────────────────────────
    def _start_hotkeys(self) -> None:
        from .hotkeys import Hotkeys

        s = self.config.settings

        def voice():
            eng = self.server.engine
            if eng.voice:
                self.server.call(eng.voice.toggle)

        self.hotkeys = Hotkeys({s.hotkey_quickbar: self.toggle_quick, s.hotkey_voice: voice})
        self.hotkeys.start()

    # ── Autostart ──────────────────────────────────────────────────────────
    @staticmethod
    def set_autostart(enabled: bool) -> None:
        if sys.platform != "win32":
            return
        import winreg

        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --minimized'
        else:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            cmd = f'"{pyw if pyw.exists() else sys.executable}" -m orion --minimized'
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                             winreg.KEY_SET_VALUE)
        with key:
            if enabled:
                winreg.SetValueEx(key, "ORION", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "ORION")
                except FileNotFoundError:
                    pass
