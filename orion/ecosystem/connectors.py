"""Connectors: Model Context Protocol (MCP) servers.

MCP is the open standard Claude, Cowork and most AI tools use for
integrations, so ORION can plug into the whole existing ecosystem —
GitHub, Notion, Spotify, Home Assistant, databases, browsers — without
writing custom code. Each enabled connector's tools are exposed to Gemini
as ``<connector>__<tool>``.

Configuration lives in ``connectors.json`` in the data folder::

    {"github": {"transport": "stdio", "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "…"}, "enabled": true}}

``"read_only": true`` on a connector only registers the tools the server marks read-only
(``readOnlyHint``); every other tool is listed as blocked and can't be called. Use it to try a new
account connector (for example Google Workspace) before allowing it to change anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from ..tools.registry import Registry, Tool, ToolError

log = logging.getLogger("orion.connectors")

# One-click catalogue shown in Ecosystem → Connectors. "needs" lists env vars the user must fill in.
CATALOG: list[dict] = [
    {"id": "filesystem", "name": "Filesystem", "description": "Advanced file operations inside folders you choose.",
     "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{HOME}"],
     "needs": [], "requires": "Node.js"},
    {"id": "fetch", "name": "Fetch", "description": "Read any web page as clean Markdown.",
     "transport": "stdio", "command": "uvx", "args": ["mcp-server-fetch"], "needs": [], "requires": "uv"},
    {"id": "github", "name": "GitHub", "description": "Issues, pull requests, repos and code search.",
     "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "needs": ["GITHUB_PERSONAL_ACCESS_TOKEN"], "requires": "Node.js"},
    {"id": "playwright", "name": "Browser (Playwright)", "description": "Let ORION drive a real browser: click, fill forms, read pages.",
     "transport": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@latest"], "needs": [], "requires": "Node.js"},
    {"id": "memory", "name": "Knowledge graph", "description": "Structured long-term memory of people, projects and relations.",
     "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"], "needs": [],
     "requires": "Node.js"},
    {"id": "notion", "name": "Notion", "description": "Search and edit your Notion pages and databases.",
     "transport": "stdio", "command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
     "needs": ["NOTION_TOKEN"], "requires": "Node.js"},
    {"id": "home-assistant", "name": "Home Assistant", "description": "Control lights, switches and scenes in your smart home.",
     "transport": "http", "url": "http://homeassistant.local:8123/mcp_server/sse", "needs": ["HA_TOKEN"],
     "requires": "Home Assistant MCP integration"},
    {"id": "custom", "name": "Custom server", "description": "Any MCP server: command + args, or an HTTP URL.",
     "transport": "stdio", "command": "", "args": [], "needs": [], "requires": ""},
]

_SAFE = re.compile(r"[^a-zA-Z0-9_]")


def tool_name(server: str, name: str) -> str:
    full = f"{_SAFE.sub('_', server)}__{_SAFE.sub('_', name)}"
    return full[:64]


def clean_schema(schema: Any) -> dict:
    """Strip JSON-Schema keys Gemini rejects; keep the rest intact."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    drop = {"$schema", "$id", "additionalProperties", "$defs", "definitions", "$ref", "examples", "const"}
    out = {k: clean_schema(v) if isinstance(v, dict) else v for k, v in schema.items() if k not in drop}
    if "properties" in out and isinstance(out["properties"], dict):
        out["properties"] = {k: clean_schema(v) for k, v in out["properties"].items()}
    if "items" in out and isinstance(out["items"], dict):
        out["items"] = clean_schema(out["items"])
    if isinstance(out.get("type"), list):  # ["string","null"] → "string"
        types_ = [t for t in out["type"] if t != "null"]
        out["type"] = types_[0] if types_ else "string"
    out.setdefault("type", "object")
    return out


def _result_text(res: Any) -> str:
    parts = []
    for c in getattr(res, "content", None) or []:
        if getattr(c, "type", "") == "text":
            parts.append(c.text)
        elif getattr(c, "type", "") == "image":
            parts.append("[image returned]")
        elif getattr(c, "type", "") == "resource":
            parts.append(str(getattr(c.resource, "text", "[resource]")))
    if not parts and getattr(res, "structured_content", None):
        parts.append(json.dumps(res.structured_content)[:8000])
    text = "\n".join(parts) or "(no output)"
    if getattr(res, "is_error", False):
        raise ToolError(text[:2000])
    return text


class Connection:
    def __init__(self, name: str, cfg: dict) -> None:
        self.name = name
        self.cfg = cfg
        self.session: Any = None
        self.state = "stopped"  # stopped | starting | ready | error
        self.error: str | None = None
        self.tools: list[str] = []
        self.blocked: list[str] = []  # tools not registered because the connector is read-only
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _open_transport(self, stack: contextlib.AsyncExitStack):
        cfg = self.cfg
        if cfg.get("transport", "stdio") == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            cmd = cfg.get("command", "")
            exe = shutil.which(cmd) or cmd
            if not exe:
                raise RuntimeError("No command configured.")
            if not shutil.which(cmd) and not Path(cmd).exists():
                raise RuntimeError(f"'{cmd}' not found. Install it first (e.g. Node.js for npx, uv for uvx).")
            env = {**os.environ, **{k: str(v) for k, v in (cfg.get("env") or {}).items()}}
            args = [a.replace("{HOME}", str(Path.home())) for a in cfg.get("args", [])]
            streams = await stack.enter_async_context(stdio_client(StdioServerParameters(command=exe, args=args, env=env)))
        else:
            import mcp.client.streamable_http as sh

            headers = {k: str(v) for k, v in (cfg.get("headers") or {}).items()}
            for key, val in (cfg.get("env") or {}).items():
                if key.endswith("TOKEN") and "Authorization" not in headers:
                    headers["Authorization"] = f"Bearer {val}"
            if hasattr(sh, "streamable_http_client"):
                import httpx

                client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=60))
                try:
                    streams = await stack.enter_async_context(sh.streamable_http_client(cfg["url"], http_client=client))
                except TypeError:
                    streams = await stack.enter_async_context(sh.streamable_http_client(cfg["url"]))
            else:
                streams = await stack.enter_async_context(sh.streamablehttp_client(cfg["url"], headers=headers))
        return streams[0], streams[1]

    async def run(self, registry: Registry, on_change) -> None:
        from mcp import ClientSession

        self.state, self.error = "starting", None
        on_change()
        try:
            async with contextlib.AsyncExitStack() as stack:
                read, write = await asyncio.wait_for(self._open_transport(stack), timeout=60)
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=60)
                listed = await session.list_tools()
                self.session = session
                self.tools, self.blocked = [], []
                for t in listed.tools:
                    self._register(registry, t)
                self.state = "ready"
                on_change()
                log.info("Connector %s ready with %d tools", self.name, len(self.tools))
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # includes ExceptionGroup from anyio task groups
            self.state = "error"
            self.error = _flatten(exc)
            log.warning("Connector %s failed: %s", self.name, self.error)
        finally:
            registry.remove_source(f"mcp:{self.name}")
            self.session = None
            if self.state != "error":
                self.state = "stopped"
            on_change()

    def _register(self, registry: Registry, t: Any) -> None:
        name = tool_name(self.name, t.name)
        ann = getattr(t, "annotations", None)
        read_only = bool(getattr(ann, "readOnlyHint", False) or getattr(ann, "read_only_hint", False))
        destructive = bool(getattr(ann, "destructiveHint", False) or getattr(ann, "destructive_hint", False))
        remote_name = t.name
        if self.cfg.get("read_only") and not read_only:
            self.blocked.append(name)  # read-only connector: anything that might change data is never offered
            return
        schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {}

        async def call(**kwargs: Any) -> str:
            if self.session is None:
                raise ToolError(f"Connector {self.name} is not connected.")
            res = await asyncio.wait_for(self.session.call_tool(remote_name, kwargs), timeout=120)
            return _result_text(res)

        registry.add(Tool(
            name=name, description=f"[{self.name}] {(t.description or t.name)[:900]}", parameters=clean_schema(schema),
            fn=call, risk="safe" if read_only else ("danger" if destructive else "confirm"),
            category="connector", source=f"mcp:{self.name}",
        ))
        self.tools.append(name)

    def start(self, registry: Registry, on_change) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run(registry, on_change), name=f"mcp-{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()

    def info(self) -> dict:
        safe_cfg = {k: v for k, v in self.cfg.items() if k != "env"}
        safe_cfg["env_keys"] = sorted((self.cfg.get("env") or {}).keys())
        return {"name": self.name, "state": self.state, "error": self.error, "tools": self.tools,
                "blocked": self.blocked, "read_only": bool(self.cfg.get("read_only")), "config": safe_cfg}


def _flatten(exc: BaseException) -> str:
    subs = getattr(exc, "exceptions", None)
    if subs:
        return "; ".join(_flatten(e) for e in subs)
    return f"{type(exc).__name__}: {exc}"[:400]


class ConnectorManager:
    def __init__(self, path: Path, registry: Registry, on_change) -> None:
        self.path = path
        self.registry = registry
        self.on_change = on_change
        self.conns: dict[str, Connection] = {}

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        except ValueError:
            log.warning("connectors.json is invalid JSON")
            return {}

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2), "utf-8")

    async def start_all(self) -> None:
        for name, cfg in self._read().items():
            if cfg.get("enabled", True):
                await self.start(name, cfg)

    async def start(self, name: str, cfg: dict) -> None:
        if name in self.conns:
            await self.conns[name].stop()
        conn = Connection(name, cfg)
        self.conns[name] = conn
        conn.start(self.registry, self.on_change)

    async def upsert(self, name: str, cfg: dict) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", name):
            raise ValueError("Connector names use lowercase letters, digits, - and _.")
        data = self._read()
        old_env = (data.get(name) or {}).get("env") or {}
        # Blank env values in an update keep the stored secret.
        cfg["env"] = {k: (v if v else old_env.get(k, "")) for k, v in (cfg.get("env") or {}).items()}
        data[name] = cfg
        self._write(data)
        if cfg.get("enabled", True):
            await self.start(name, cfg)
        elif name in self.conns:
            await self.conns.pop(name).stop()
        self.on_change()

    async def remove(self, name: str) -> None:
        data = self._read()
        data.pop(name, None)
        self._write(data)
        if name in self.conns:
            await self.conns.pop(name).stop()
        self.on_change()

    async def stop_all(self) -> None:
        await asyncio.gather(*(c.stop() for c in self.conns.values()), return_exceptions=True)

    def list(self) -> list[dict]:
        data = self._read()
        out = []
        for name, cfg in data.items():
            conn = self.conns.get(name)
            info = conn.info() if conn else Connection(name, cfg).info()
            info["enabled"] = cfg.get("enabled", True)
            out.append(info)
        return out
