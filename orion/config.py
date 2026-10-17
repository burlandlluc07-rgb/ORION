"""Paths, settings and secrets.

Everything user-specific lives in one data directory (``%APPDATA%/ORION`` on
Windows, ``~/.local/share/ORION`` on Linux, overridable with ``ORION_HOME``),
so the installed program folder stays read-only like a real application.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir
from pydantic import BaseModel, Field

log = logging.getLogger("orion.config")

KEYRING_SERVICE = "ORION"
KEYRING_USER = "gemini_api_key"


def data_dir() -> Path:
    root = os.environ.get("ORION_HOME")
    path = Path(root) if root else Path(user_data_dir("ORION", appauthor=False, roaming=True))
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_dir() -> Path:
    """Folder holding bundled read-only resources (works under PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


class ModelLimits(BaseModel):
    """Free-tier budget for one model. Conservative defaults; editable in Settings."""

    rpm: int = 10
    rpd: int = 250


class VoiceSettings(BaseModel):
    mode: str = "live"  # "live" (Gemini native audio) | "classic" (record → Gemini → edge-tts) | "off"
    live_voice: str = "Charon"  # Gemini Live prebuilt voice
    tts_voice: str = "en-GB-RyanNeural"  # edge-tts voice used in classic mode
    tts_rate: str = "+8%"
    wake_word: bool = False
    wake_phrases: list[str] = Field(default_factory=lambda: ["Hey Jarvis", "Hey ORION"])  # any words you like
    wake_sensitivity: float = 0.5  # 0 strict … 1 relaxed
    wake_language: str = ""  # speech language, e.g. "en-GB" or "es-ES" ("" = Windows display language)
    wake_engine: str = "windows"  # "windows" (any phrase, built in) | "openwakeword" (pre-trained models)
    wake_word_model: str = "hey_jarvis"  # openwakeword model when that engine is chosen
    input_device: int | None = None
    output_device: int | None = None
    speak_replies: bool = True
    instant_commands: bool = True  # common commands run offline the moment they're heard (Windows)
    follow_up: bool = True  # after a reply, keep listening briefly so you can carry on without the wake word
    follow_up_seconds: float = 6.0
    barge_in: bool = True  # talk over ORION to interrupt it (classic mode; Live always allows it)
    chime: bool = True  # a soft tone when ORION starts listening


class Settings(BaseModel):
    user_name: str = ""
    assistant_name: str = "ORION"
    persona: str = "jarvis"  # jarvis | concise | friendly
    theme: str = "dark"
    accent: str = "#4cc9ff"

    # Model routing. "-latest" aliases are maintained by Google, so they keep
    # working when individual versions are retired.
    model_fast: str = "gemini-flash-lite-latest"
    model_smart: str = "gemini-flash-latest"
    model_live: str = "auto"  # "auto" → discover a model that supports bidiGenerateContent
    model_limits: dict[str, ModelLimits] = Field(
        default_factory=lambda: {
            "gemini-flash-lite-latest": ModelLimits(rpm=15, rpd=500),
            "gemini-flash-latest": ModelLimits(rpm=5, rpd=20),
        }
    )
    extra_models: bool = True  # when quota runs out, also use other free Flash models (each has its own quota)
    thinking_budget: int = 0  # 0 = off (fastest). Raise for harder reasoning.

    fast_path: bool = True  # answer simple commands locally with zero API calls
    approval_mode: str = "ask"  # "ask" | "auto_safe" (auto-approve low risk) | "trust" (never ask)
    allowed_roots: list[str] = Field(default_factory=list)  # extra folders file tools may touch
    # Steps that use your desktop (mouse, keys, focus, or opening a window) when ORION decides on them itself:
    # "ask" = ask every time (default) | "allow" = don't ask. Your own instant commands and phone buttons never ask.
    desktop_actions: str = "ask"
    # The background agent: a task queue that works on files in its own folders, shell commands with no
    # window and web search, never the desktop. Its tasks wait for your OK on risky steps and survive restarts.
    background_agent: bool = True
    browser_mode: str = "background"  # "background" = ORION's browser runs hidden | "visible" = in a window

    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    hotkey_quickbar: str = "<alt>+<space>"
    hotkey_voice: str = "<ctrl>+<alt>+j"
    start_minimized: bool = False
    launch_on_startup: bool = False
    close_to_tray: bool = True

    vault_path: str = ""  # Obsidian vault for ORION's memory ("" = Documents/ORION Brain)
    memory_review_day: int = 6  # weekly short-term review: 0 = Monday … 6 = Sunday
    memory_review_hour: int = 20
    auto_memory: bool = True  # learn durable facts from conversations automatically
    proactive: bool = True  # watch battery/CPU/disk/downloads/calendar/network and speak up
    proactive_voice: bool = True
    proactive_downloads: bool = True
    quiet_start: str = "23:00"
    quiet_end: str = "07:00"

    check_updates: bool = True
    update_repo: str = "burlandlluc07-rgb/ORION"  # GitHub "owner/repo" whose Releases ORION checks

    remote_enabled: bool = False
    remote_port: int = 8790
    remote_access: str = "lan"  # lan | tailscale | cloudflare

    history_turns: int = 12
    onboarded: bool = False


class Config:
    """Thread-safe settings holder persisted as JSON."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir()
        self.path = self.root / "settings.json"
        self._lock = threading.RLock()
        self.settings = self._load()

    def _load(self) -> Settings:
        if self.path.exists():
            try:
                return Settings.model_validate(json.loads(self.path.read_text("utf-8")))
            except Exception as exc:  # corrupt file: keep a copy, start fresh
                log.warning("settings.json unreadable (%s); backing it up", exc)
                self.path.replace(self.path.with_suffix(".bad.json"))
        return Settings()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self.settings.model_dump_json(indent=2), "utf-8")
            tmp.replace(self.path)

    def update(self, patch: dict[str, Any]) -> Settings:
        with self._lock:
            merged = _deep_merge(self.settings.model_dump(), patch)
            self.settings = Settings.model_validate(merged)
            self.save()
            return self.settings

    # ── API key ────────────────────────────────────────────────────────────
    def get_api_key(self) -> str:
        env = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if env:
            return env.strip()
        try:
            import keyring

            value = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if value:
                return value
        except Exception:
            pass
        fallback = self.root / ".key"
        return fallback.read_text("utf-8").strip() if fallback.exists() else ""

    def set_api_key(self, key: str) -> str:
        """Store the key in the OS credential vault; fall back to a private file."""
        key = key.strip()
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            (self.root / ".key").unlink(missing_ok=True)
            return "keyring"
        except Exception:
            fallback = self.root / ".key"
            fallback.write_text(key, "utf-8")
            try:
                os.chmod(fallback, 0o600)
            except OSError:
                pass
            return "file"


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
