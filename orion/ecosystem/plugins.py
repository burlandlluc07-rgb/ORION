"""Python plugins: drop a .py file into the data folder's ``plugins`` directory.

    from orion.tools.registry import tool

    @tool(category="home")
    def toggle_desk_lamp(on: bool) -> str:
        '''Turn the desk lamp on or off.

        Args:
            on: True to switch on.
        '''
        ...

Every function decorated with ``@tool`` becomes available to ORION (and shows
up under Ecosystem → Plugins, where it can be disabled). A broken plugin is
reported in the UI and never stops the app.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
from pathlib import Path

from ..tools.registry import Registry, Tool

log = logging.getLogger("orion.plugins")

TEMPLATE = '''"""Example ORION plugin. Rename the file, edit, then click Reload in Ecosystem → Plugins."""

from orion.tools.registry import tool


@tool(category="example")
def coin_flip() -> str:
    """Flip a coin."""
    import random

    return random.choice(["Heads", "Tails"])
'''


class PluginManager:
    def __init__(self, folder: Path, registry: Registry) -> None:
        self.folder = folder
        self.registry = registry
        self.folder.mkdir(parents=True, exist_ok=True)
        self.status: dict[str, dict] = {}
        example = self.folder / "_example_plugin.py"
        if not example.exists():
            example.write_text(TEMPLATE, "utf-8")

    def load_all(self) -> None:
        for name in list(self.status):
            self.registry.remove_source(f"plugin:{name}")
        self.status.clear()
        for path in sorted(self.folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            self._load(path)

    def _load(self, path: Path) -> None:
        name = path.stem
        mod_name = f"orion_plugin_{name}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            tools: list[Tool] = [
                obj.__orion_tool__ for obj in vars(module).values() if hasattr(obj, "__orion_tool__")
            ]
            for t in tools:
                t.source = f"plugin:{name}"
                self.registry.add(t)
            self.status[name] = {"name": name, "ok": True, "tools": [t.name for t in tools], "error": None,
                                 "path": str(path)}
            log.info("Plugin %s loaded (%d tools)", name, len(tools))
        except Exception as exc:
            self.status[name] = {"name": name, "ok": False, "tools": [], "path": str(path),
                                 "error": f"{exc}\n{traceback.format_exc(limit=3)}"}
            log.warning("Plugin %s failed: %s", name, exc)

    def list(self) -> list[dict]:
        return list(self.status.values())
