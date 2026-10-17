"""Client-side tools the agent can call. API, file and shell actions only:
nothing here touches the mouse, the keyboard, the focus or any window.

Each tool has a JSON schema for Claude, a `check` that returns a safety
verdict before anything runs, and a `run` that does the work.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .runners import RunContext
from .safety import ALLOW, APPROVE, DENY, PathNotAllowed, Verdict, classify_command, resolve_path

log = logging.getLogger("orion.background.tools")

# Hide any console window a child process would open (Windows only).
CREATE_NO_WINDOW = 0x08000000


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    check: Callable[[dict], Verdict]
    run: Callable[[dict, RunContext], str]

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


class Toolbox:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.safety = cfg["safety"]
        self.roots: list[Path] = cfg.allowed_roots() if hasattr(cfg, "allowed_roots") else []
        for root in self.roots:
            root.mkdir(parents=True, exist_ok=True)
        self.tools: dict[str, Tool] = {t.name: t for t in self._build()}

    # -- public ------------------------------------------------------------

    def add(self, tool: Tool) -> None:
        """Register a provider-specific tool (e.g. Gemini's web search)."""
        self.tools[tool.name] = tool

    def definitions(self) -> list[dict]:
        return [t.definition() for t in self.tools.values()]

    def check(self, name: str, args: dict) -> Verdict:
        tool = self.tools.get(name)
        if tool is None:
            return Verdict(DENY, f"Unknown tool {name!r}")
        if not isinstance(args, dict):
            return Verdict(DENY, "Tool input must be an object")
        missing = [k for k in tool.schema.get("required", []) if k not in args]
        if missing:
            return Verdict(DENY, f"Missing required input: {', '.join(missing)}")
        try:
            return tool.check(args)
        except PathNotAllowed as exc:
            return Verdict(DENY, str(exc))

    def run(self, name: str, args: dict, ctx: RunContext) -> str:
        return self.tools[name].run(args, ctx)

    # -- helpers -----------------------------------------------------------

    def _path(self, raw: str) -> Path:
        return resolve_path(raw, self.roots)

    def _build(self) -> list[Tool]:
        return [
            Tool(
                "list_dir",
                "List files and folders in a directory inside the allowed folders.",
                _schema({"path": {"type": "string", "description": "Directory path; relative paths start in the first allowed folder."}}, ["path"]),
                self._check_read, self._list_dir,
            ),
            Tool(
                "read_file",
                "Read a UTF-8 text file inside the allowed folders.",
                _schema({"path": {"type": "string"}}, ["path"]),
                self._check_read, self._read_file,
            ),
            Tool(
                "write_file",
                "Create or overwrite a UTF-8 text file inside the allowed folders. Overwriting an existing file needs the user's approval.",
                _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
                self._check_write, self._write_file,
            ),
            Tool(
                "delete_file",
                "Delete a file inside the allowed folders. Always needs the user's approval.",
                _schema({"path": {"type": "string"}}, ["path"]),
                self._check_delete, self._delete_file,
            ),
            Tool(
                "run_shell",
                "Run a shell command with no window, in the first allowed folder. Commands not on the safe list need the user's approval. Never use it to open apps or windows.",
                _schema({"command": {"type": "string"}}, ["command"]),
                self._check_shell, self._run_shell,
            ),
            Tool(
                "notify",
                "Show the user a quiet Windows toast notification. Use sparingly, for results they asked to be told about.",
                _schema({"title": {"type": "string"}, "message": {"type": "string"}}, ["title", "message"]),
                lambda args: Verdict(ALLOW), self._notify,
            ),
        ]

    # -- checks ------------------------------------------------------------

    def _check_read(self, args: dict) -> Verdict:
        self._path(args["path"])
        return Verdict(ALLOW)

    def _check_write(self, args: dict) -> Verdict:
        path = self._path(args["path"])
        if path.is_dir():
            return Verdict(DENY, f"{path} is a folder")
        if path.exists():
            return Verdict(APPROVE, f"Overwrites existing file {path}")
        return Verdict(ALLOW)

    def _check_delete(self, args: dict) -> Verdict:
        path = self._path(args["path"])
        if not path.exists():
            return Verdict(DENY, f"{path} does not exist")
        if path.is_dir():
            return Verdict(DENY, "Deleting folders is not supported")
        return Verdict(APPROVE, f"Deletes {path}")

    def _check_shell(self, args: dict) -> Verdict:
        return classify_command(str(args["command"]), list(self.safety["safe_commands"]))

    # -- runs --------------------------------------------------------------

    def _list_dir(self, args: dict, ctx: RunContext) -> str:
        path = self._path(args["path"])
        if not path.is_dir():
            return f"Error: {path} is not a folder"
        entries = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]:
            if child.is_dir():
                entries.append(f"[dir]  {child.name}")
            else:
                entries.append(f"[file] {child.name} ({child.stat().st_size} bytes)")
        return f"{path}\n" + ("\n".join(entries) or "(empty)")

    def _read_file(self, args: dict, ctx: RunContext) -> str:
        path = self._path(args["path"])
        if not path.is_file():
            return f"Error: {path} is not a file"
        limit = int(self.safety["max_read_chars"])
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > limit:
            return text[:limit] + f"\n\n[truncated: showing {limit} of {len(text)} characters]"
        return text

    def _write_file(self, args: dict, ctx: RunContext) -> str:
        path = self._path(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".orion-tmp")
        tmp.write_text(args["content"], encoding="utf-8")
        os.replace(tmp, path)
        # Evidence, not a claim: report what is actually on disk now.
        return f"Wrote {path.stat().st_size} bytes to {path}"

    def _delete_file(self, args: dict, ctx: RunContext) -> str:
        path = self._path(args["path"])
        path.unlink()
        return f"Deleted {path}" if not path.exists() else f"Error: {path} still exists"

    def _run_shell(self, args: dict, ctx: RunContext) -> str:
        command = str(args["command"])
        timeout = min(float(self.safety["shell_timeout_seconds"]), max(1.0, ctx.remaining()))
        kwargs: dict[str, Any] = {
            "shell": True,
            "cwd": str(self.roots[0]) if self.roots else None,
            "stdin": subprocess.DEVNULL,
            "capture_output": True,
            "timeout": timeout,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = CREATE_NO_WINDOW
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = startup
        log.info("shell: %s", command)
        try:
            proc = subprocess.run(command, **kwargs)
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout:.0f} s"
        out = _decode(proc.stdout)
        err = _decode(proc.stderr)
        text = f"exit code {proc.returncode}\n"
        if out:
            text += f"stdout:\n{out}\n"
        if err:
            text += f"stderr:\n{err}\n"
        limit = int(self.safety["max_read_chars"])
        return text if len(text) <= limit else text[:limit] + "\n[truncated]"

    def _notify(self, args: dict, ctx: RunContext) -> str:
        ctx.notify(str(args["title"]), str(args["message"]))
        return "Notification shown"


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "mbcs" if sys.platform == "win32" else "latin-1"):
        try:
            return data.decode(enc).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace").strip()

