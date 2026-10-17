"""Where the background agent keeps its files.

Windows: %APPDATA%\\Orion (logs in %APPDATA%\\Orion\\logs, data in
%APPDATA%\\Orion\\background). Elsewhere: ~/.orion. ORION_HOME overrides both,
which the tests use to get a throwaway folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    override = os.environ.get("ORION_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "Orion"
    else:
        base = Path.home() / ".orion"
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_dir() -> Path:
    path = app_dir() / "background"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = app_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_dir() / "background-agent.toml"


def tasks_db() -> Path:
    return data_dir() / "tasks.db"


def schedules_db() -> Path:
    return data_dir() / "schedules.db"


def runtime_file() -> Path:
    """Port, token and pid of the running daemon, read by clients."""
    return data_dir() / "daemon.json"
