"""ORION's brain map: its own code as a graph, for Memory → View brain.

``assets/brain_map.json`` is built from ORION's source by ``scripts/build_brain_map.py`` (graphify, offline). It
ships with the app; nothing here parses code at runtime. This module adds what only the running engine knows:
which point on the map each tool is, and which point lights up for each kind of event (thinking, listening,
saving a memory…), so the UI can show the brain working live.
"""

from __future__ import annotations

import inspect
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("orion.brainmap")
MAP_FILE = Path(__file__).resolve().parent / "assets" / "brain_map.json"

# What lights up for each kind of event: (file, label on the map). The UI fires these from the event stream.
ANCHORS = {
    "think": ("orion/brain/agent.py", "Agent"),
    "gemini": ("orion/brain/gemini.py", "GeminiBrain"),
    "backup": ("orion/brain/backup.py", "BackupBrain"),
    "voice": ("orion/voice/manager.py", "VoiceManager"),
    "listen": ("orion/voice/audio.py", "Microphone"),
    "wake": ("orion/voice/wake.py", "SpeechService"),
    "speak": ("orion/voice/tts.py", "TTS"),
    "live": ("orion/voice/live.py", "LiveSession"),
    "memory": ("orion/memory/vault.py", "Vault"),
    "learn": ("orion/memory/learner.py", "Learner"),
    "alert": ("orion/proactive.py", "Proactive"),
    "mission": ("orion/tasks/missions.py", "Missions"),
    "routine": ("orion/tasks/scheduler.py", "Scheduler"),
    "task": ("orion/tasks/tracker.py", "TaskTracker"),
    "approval": ("orion/tasks/tracker.py", "ApprovalBroker"),
    "connector": ("orion/ecosystem/connectors.py", "ConnectorManager"),
    "plugin": ("orion/ecosystem/plugins.py", "PluginManager"),
    "engine": ("orion/engine.py", "Engine"),
    "events": ("orion/events.py", "EventBus"),
    "api": ("orion/server/api.py", "create_app()"),
    "interface": ("ui/src/App.tsx", "App()"),
}


@lru_cache(maxsize=1)
def load() -> dict | None:
    try:
        return json.loads(MAP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Brain map unavailable: %s", e)
        return None


def _index(data: dict) -> dict[tuple[str, str], int]:
    files = data["files"]
    out: dict[tuple[str, str], int] = {}
    for i, node in enumerate(data["nodes"]):
        out.setdefault((files[node[1]], node[0]), i)
    return out


def _source(fn: Any) -> tuple[str, str] | None:
    """(module as a source path, function name) for a tool's Python function.

    Goes by module name, not file, because the installed app ships compiled code without the .py files.
    """
    fn = inspect.unwrap(getattr(fn, "__func__", fn))
    module, name = getattr(fn, "__module__", None), getattr(fn, "__name__", None)
    if not module or not name or not module.startswith("orion."):
        return None
    return module.replace(".", "/"), name


def tool_nodes(data: dict, tools: list) -> dict[str, int]:
    """Tool name → map node, for tools whose code is on the map (MCP and plugin tools aren't)."""
    index = _index(data)
    out: dict[str, int] = {}
    for t in tools:
        src = _source(t.fn)
        if not src:
            continue
        base, name = src
        for path in (f"{base}.py", f"{base}/__init__.py"):
            node = index.get((path, f"{name}()"), index.get((path, f".{name}()")))
            if node is not None:
                out[t.name] = node
                break
    return out


def payload(engine: Any) -> dict:
    data = load()
    if data is None:
        return {"available": False}
    index = _index(data)
    anchors = {key: index[spot] for key, spot in ANCHORS.items() if spot in index}
    tools = engine.registry.all(include_disabled=True)
    return {
        "available": True,
        **data,
        "anchors": anchors,
        "tools": tool_nodes(data, tools),
        # Tools without their own spot light up the part of the brain that runs them.
        "tool_sources": {t.name: t.source.split(":")[0] for t in tools if t.source != "core"},
    }
