"""Quiet results: Windows toast notifications.

A toast appears in the corner and in the Action Center; it never takes focus
or moves the mouse. Without winotify (or off Windows) it only logs.
"""

from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger("orion.background.notify")


class Notifier:
    def __init__(self, cfg: dict, ui_url: str | None = None):
        self.cfg = cfg["notify"]
        self.ui_url = ui_url
        self.sent: list[tuple[str, str]] = []  # kept for tests and the tray tooltip
        self._lock = threading.Lock()
        self._backend = None
        if self.cfg.get("toast") and sys.platform == "win32":
            try:
                from winotify import Notification  # type: ignore

                self._backend = Notification
            except ImportError:
                log.warning("winotify is not installed; toasts are logged only")

    def __call__(self, title: str, message: str) -> None:
        self.send(title, message)

    def send(self, title: str, message: str) -> None:
        message = message if len(message) <= 250 else message[:247] + "..."
        with self._lock:
            self.sent.append((title, message))
            del self.sent[:-50]
        log.info("notify: %s | %s", title, message)
        if self._backend is None:
            return
        try:
            toast = self._backend(app_id=self.cfg.get("app_id", "Orion"), title=title, msg=message)
            if self.ui_url:
                toast.add_actions(label="Open history", launch=self.ui_url)
            toast.show()
        except Exception:  # a failed toast must never fail a task
            log.exception("toast failed")
