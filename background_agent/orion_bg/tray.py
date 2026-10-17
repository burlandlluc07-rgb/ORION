"""Tray icon (pystray): status, pause/resume, open history, quit.

Opening the history page is the only thing here that shows a window, and it
only happens when the user clicks it.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Any, Callable

log = logging.getLogger("orion_bg.tray")


def available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


def _icon_image(color: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=color)
    draw.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))
    return img


class Tray:
    def __init__(self, agent: Any, ui_url: str, stop: threading.Event, on_quit: Callable[[], None]):
        self.agent = agent
        self.ui_url = ui_url
        self.stop_event = stop
        self.on_quit = on_quit
        self.icon = None

    def _status_text(self) -> str:
        st = self.agent.status()
        text = f"{len(st['running'])} running, {st['counts']['queued']} queued"
        if st["pending_approvals"]:
            text += f", {st['pending_approvals']} need OK"
        return ("Paused: " if st["paused"] else "") + text

    def run(self) -> None:
        """Blocks until Quit is clicked or the stop event is set."""
        import pystray

        def toggle_pause(icon, item):
            self.agent.resume() if self.agent.paused else self.agent.pause()
            self._refresh()

        def open_ui(icon, item):
            webbrowser.open(self.ui_url)

        def quit_(icon, item):
            self.on_quit()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: self._status_text(), None, enabled=False),
            pystray.MenuItem("Pause new tasks", toggle_pause, checked=lambda item: self.agent.paused),
            pystray.MenuItem("Open task history", open_ui, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit background agent", quit_),
        )
        self.icon = pystray.Icon("orion_bg", _icon_image("#2856d6"), "Orion background agent", menu)
        threading.Thread(target=self._watch, name="tray-watch", daemon=True).start()
        log.info("tray icon shown")
        self.icon.run()

    def _refresh(self) -> None:
        if not self.icon:
            return
        try:
            paused = self.agent.paused
            waiting = self.agent.status()["pending_approvals"]
            color = "#8a8a8a" if paused else ("#d18b00" if waiting else "#2856d6")
            self.icon.icon = _icon_image(color)
            self.icon.title = "Orion: " + self._status_text()
            self.icon.update_menu()
        except Exception:
            log.debug("tray refresh failed", exc_info=True)

    def _watch(self) -> None:
        while not self.stop_event.wait(3):
            self._refresh()
        if self.icon:
            self.icon.stop()
