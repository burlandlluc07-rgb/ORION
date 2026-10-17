"""Keeps the daemon alive: starts it as a child process with no window and
restarts it if it exits with an error. A clean exit (Quit in the tray,
`orion-bg stop`) ends the supervisor too.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Callable

log = logging.getLogger("orion_bg.supervisor")

CREATE_NO_WINDOW = 0x08000000


def daemon_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "daemon"]
    return [sys.executable, "-m", "orion_bg", "daemon"]


def supervise(command: list[str] | None = None, max_backoff: float = 60.0,
              healthy_after: float = 60.0, max_restarts: int | None = None,
              spawn: Callable[[list[str]], int] | None = None, sleep=time.sleep) -> int:
    command = command or daemon_command()
    spawn = spawn or _spawn
    backoff = 2.0
    restarts = 0
    while True:
        started = time.monotonic()
        log.info("starting daemon: %s", " ".join(command))
        code = spawn(command)
        ran = time.monotonic() - started
        if code == 0:
            log.info("daemon exited cleanly; supervisor stopping")
            return 0
        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            log.error("daemon failed %d times; giving up", restarts)
            return code
        if ran >= healthy_after:
            backoff = 2.0
        log.warning("daemon exited with code %s after %.0f s; restarting in %.0f s", code, ran, backoff)
        sleep(backoff)
        backoff = min(max_backoff, backoff * 2)


def _spawn(command: list[str]) -> int:
    kwargs = {"stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        return subprocess.call(command, **kwargs)
    except KeyboardInterrupt:
        return 0
