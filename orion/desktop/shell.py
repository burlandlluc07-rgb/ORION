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

        width, height = self._initial_size()
        self.window = webview.create_window(
            "ORION", self.url, width=width, height=height, min_size=(760, 520), background_color="#000000",
            hidden=start_hidden, text_select=True,
        )
        self.quick = webview.create_window(
            "ORION Quick", self.url + "#/quick", width=720, height=440, frameless=True, on_top=True,
            hidden=True, background_color="#000000", easy_drag=True, resizable=False,
        )
        self.window.events.closing += self._on_closing
        self._start_tray()
        self._start_hotkeys()
        storage = self.config.root / "webview"
        try:
            webview.start(gui=None, private_mode=False, storage_path=str(storage), debug="--debug" in sys.argv)
        except Exception:
            # e.g. the Microsoft Edge WebView2 runtime is missing: keep ORION usable in the browser.
            log.exception("Native window failed; falling back to the default browser")
            import webbrowser

            self.window = self.quick = None
            webbrowser.open(self.url)
            self.notify("ORION", "Opened in your browser. Install the Microsoft Edge WebView2 Runtime for the app window.")
            import time

            while not self._quitting and self.server.is_alive():
                time.sleep(0.5)
        self._cleanup()

    @staticmethod
    def _initial_size() -> tuple[int, int]:
        """Fit the window to the screen (seen on a 1024×768 VM: a fixed 1320 px window was cut off)."""
        try:
            import webview

            scr = webview.screens[0]
            return min(1320, int(scr.width * 0.9)), min(860, int(scr.height * 0.88))
        except Exception:
            return 1200, 780

    def _on_closing(self) -> bool:
        if self._quitting or not self.config.settings.close_to_tray or self.tray is None:
            self._quitting = True
            if self.quick:
                try:
                    self.quick.destroy()
                except Exception:
                    pass
            return True
        self.window.hide()
        return False

    def show_main(self) -> None:
        if self.window:
            self.window.show()
            self.window.restore()

    def toggle_quick(self) -> None:
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
        elif action == "quit":
            threading.Thread(target=self.quit, daemon=True).start()

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
            pystray.MenuItem("Quick command", lambda *_: self.toggle_quick()),
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
