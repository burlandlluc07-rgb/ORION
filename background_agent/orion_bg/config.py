"""One TOML config file. API keys are never stored here: only the name of
the environment variable that holds one."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "daemon": {
        "host": "127.0.0.1",
        "port": 0,  # 0 = pick a free port; clients read it from daemon.json
        "workers": 2,
    },
    "tasks": {
        "timeout_seconds": 900,
        "max_steps": 40,
        "dummy_seconds": 2.0,
    },
    "safety": {
        # The agent may only read or write inside these folders.
        "allowed_paths": ["~/Documents/Orion"],
        # Commands that run without asking. Anything else needs approval.
        # A command matches when it equals an entry or starts with entry + " ",
        # and contains no chaining or redirection characters.
        "safe_commands": [
            "dir", "echo", "type", "where", "whoami", "hostname", "ver",
            "python --version", "py --version", "pip list", "pip show",
            "git status", "git log", "git diff", "git branch",
        ],
        "shell_timeout_seconds": 120,
        "max_read_chars": 100_000,
    },
    "agent": {
        "provider": "gemini",  # "gemini" (default, same SDK and key as ORION) or "claude"
        "web_search": True,
        "system_prompt": "",
    },
    "gemini": {
        "model": "gemini-flash-latest",  # ORION's "smart" model
        # The key is read from this variable, then GOOGLE_API_KEY, then the key
        # ORION saved in the Windows credential vault.
        "api_key_env": "GEMINI_API_KEY",
        "use_orion_keyring": True,
        "max_output_tokens": 8192,
        "thinking_budget": "",  # empty = model default; 0 = off
        "retries": 3,
    },
    "claude": {
        "model": "claude-opus-5-5",
        "effort": "high",
        "max_tokens": 16000,
        "api_key_env": "ANTHROPIC_API_KEY",
        "web_search_max_uses": 5,
        # Server-side refusal fallback ("default" routing); turn off if your
        # account or proxy rejects the beta.
        "fallbacks": True,
    },
    "notify": {
        "toast": True,
        "app_id": "Orion",
        "on_complete": True,
        "on_failure": True,
        "on_approval": True,
    },
    "hotkey": {
        # Pressing the hotkey queues the text on the clipboard as a task.
        "enabled": True,
        "combo": "ctrl+alt+o",
    },
    "scheduler": {
        "enabled": True,
    },
    "tray": {
        "enabled": True,
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 2_000_000,
        "backup_count": 5,
    },
}

TEMPLATE = """\
# ORION background agent config.
# The Gemini API key is read from GEMINI_API_KEY (or GOOGLE_API_KEY), else from
# the key ORION saved in the Windows credential vault. Never put a key in this file.

[daemon]
host = "127.0.0.1"
port = 0          # 0 = random free port, published in daemon.json
workers = 2

[tasks]
timeout_seconds = 900
max_steps = 40

[safety]
allowed_paths = ["~/Documents/Orion"]
shell_timeout_seconds = 120

[agent]
provider = "gemini"
web_search = true

[gemini]
model = "gemini-flash-latest"
api_key_env = "GEMINI_API_KEY"

[notify]
toast = true

[hotkey]
enabled = true
combo = "ctrl+alt+o"

[scheduler]
enabled = true

[tray]
enabled = true

[logging]
level = "INFO"
"""


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config(dict):
    """Nested dict with the defaults filled in."""

    def section(self, name: str) -> dict:
        return self[name]

    def allowed_roots(self) -> list[Path]:
        roots = []
        for raw in self["safety"]["allowed_paths"]:
            expanded = os.path.expandvars(os.path.expanduser(raw))
            roots.append(Path(expanded).resolve())
        return roots


def load(path: Path | None = None, overrides: dict | None = None) -> Config:
    path = path or paths.config_path()
    data: dict = {}
    if path.exists():
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    merged = _merge(DEFAULTS, data)
    if overrides:
        merged = _merge(merged, overrides)
    return Config(merged)


def write_template(path: Path | None = None) -> Path:
    path = path or paths.config_path()
    if not path.exists():
        path.write_text(TEMPLATE, encoding="utf-8")
    return path
