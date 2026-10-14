"""Tool registry.

Tools are plain Python functions decorated with :func:`tool`. The JSON schema
Gemini needs is generated from type hints and the docstring's ``Args:``
section, so adding a capability is one function — no hand-written schemas.

Every tool has a *risk* level that drives the approval flow:

* ``safe``     — read-only or trivially reversible (time, search, volume).
* ``confirm``  — changes something on the PC (write a file, type keys, open URL in a session).
* ``danger``   — destructive or hard to undo (delete, shutdown, run shell).
"""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
import typing
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, get_args, get_origin

Risk = Literal["safe", "confirm", "danger"]
MAX_RESULT_CHARS = 8000


class ToolError(Exception):
    """Raised by tools for expected failures; the message is shown to the model."""


@dataclass
class ToolContext:
    """Injected into tools that declare a parameter annotated ``ToolContext``."""

    engine: Any = None
    cancel: threading.Event = field(default_factory=threading.Event)
    progress: Callable[[str], None] = lambda _msg: None
    conv_id: str | None = None

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise ToolError("Cancelled by user.")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]]
    risk: Risk = "safe"
    category: str = "general"
    source: str = "core"  # core | plugin:<name> | mcp:<server>
    summarize: Callable[[dict], str] | None = None
    wants_context: str | None = None
    kit: str | None = None  # None = core (always offered); otherwise the toolkit it belongs to (see kits.py)

    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters_json_schema": self.parameters}

    def describe_call(self, args: dict) -> str:
        if self.summarize:
            try:
                return self.summarize(args)
            except Exception:
                pass
        title = self.name.split("__")[-1].replace("_", " ").capitalize()
        if self.source.startswith("mcp:"):
            title = f"{self.source[4:]} · {title}"
        shown = ", ".join(_short(v) if isinstance(v, str) else f"{k}={_short(v)}" for k, v in args.items())
        return f"{title}: {shown}" if shown else title

    async def run(self, args: dict, ctx: ToolContext) -> str:
        kwargs = dict(args)
        if self.wants_context:
            kwargs[self.wants_context] = ctx
        if inspect.iscoroutinefunction(self.fn):
            result = await self.fn(**kwargs)
        else:
            result = await asyncio.to_thread(self.fn, **kwargs)
        return _format_result(result)


def _short(v: Any, n: int = 48) -> str:
    s = f"“{v}”" if isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _format_result(result: Any) -> str:
    if result is None:
        text = "Done."
    elif isinstance(result, str):
        text = result
    else:
        import json

        text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n…[truncated {len(text) - MAX_RESULT_CHARS} chars]"
    return text


# ── Schema generation ──────────────────────────────────────────────────────
def _json_type(tp: Any) -> dict:
    origin = get_origin(tp)
    if tp is ToolContext:
        return {}
    if origin is typing.Union or (origin is not None and str(origin) == "<class 'types.UnionType'>"):
        args = [a for a in get_args(tp) if a is not type(None)]
        return _json_type(args[0]) if args else {"type": "string"}
    if origin is Literal:
        vals = list(get_args(tp))
        base = "integer" if all(isinstance(v, int) for v in vals) else "string"
        return {"type": base, "enum": vals}
    if origin in (list, tuple, set):
        inner = get_args(tp)
        return {"type": "array", "items": _json_type(inner[0]) if inner else {"type": "string"}}
    if origin is dict or tp is dict:
        return {"type": "object"}
    return {
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        str: {"type": "string"},
    }.get(tp, {"type": "string"})


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    doc = inspect.cleandoc(doc or "")
    parts = re.split(r"^\s*Args:\s*$", doc, maxsplit=1, flags=re.M)
    summary = parts[0].strip()
    params: dict[str, str] = {}
    if len(parts) > 1:
        current = None
        for line in parts[1].splitlines():
            m = re.match(r"^\s{0,8}(\w+)\s*(?:\([^)]*\))?:\s*(.*)$", line)
            if m:
                current = m.group(1)
                params[current] = m.group(2).strip()
            elif current and line.strip():
                params[current] += " " + line.strip()
    return summary, params


def schema_for(fn: Callable) -> tuple[dict, str | None, str]:
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    summary, param_docs = _parse_docstring(fn.__doc__ or "")
    props: dict[str, dict] = {}
    required: list[str] = []
    ctx_param = None
    for name, p in sig.parameters.items():
        tp = hints.get(name, str)
        if tp is ToolContext:
            ctx_param = name
            continue
        prop = _json_type(tp)
        if name in param_docs:
            prop["description"] = param_docs[name]
        if p.default is inspect.Parameter.empty:
            required.append(name)
        elif p.default is not None:
            prop["default"] = p.default
        props[name] = prop
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema, ctx_param, summary


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()

    def add(self, t: Tool) -> None:
        self._tools[t.name] = t

    def remove_source(self, source: str) -> None:
        for name in [n for n, t in self._tools.items() if t.source == source]:
            del self._tools[name]

    def get(self, name: str) -> Tool | None:
        t = self._tools.get(name)
        return None if t is None or name in self._disabled else t

    def set_enabled(self, name: str, enabled: bool) -> None:
        (self._disabled.discard if enabled else self._disabled.add)(name)

    def all(self, include_disabled: bool = False) -> list[Tool]:
        return [t for n, t in self._tools.items() if include_disabled or n not in self._disabled]

    def declarations(self, names: set[str] | None = None, kits: set[str] | None = None) -> list[dict]:
        """Declarations to send to Gemini. ``kits=None`` means every tool; otherwise core tools plus those kits."""
        return [t.declaration() for t in self.all()
                if (names is None or t.name in names) and (kits is None or t.kit is None or t.kit in kits)]

    def kits(self) -> list[str]:
        """Toolkits that currently have at least one enabled tool."""
        return sorted({t.kit for t in self.all() if t.kit})

    def kit_tools(self, kit: str) -> list[str]:
        return [t.name for t in self.all() if t.kit == kit]

    def is_disabled(self, name: str) -> bool:
        return name in self._disabled


# Tools register into this module-level collection at import time; the engine
# copies them into its own Registry.
_DECORATED: list[Tool] = []


def tool(
    risk: Risk = "safe",
    category: str = "general",
    name: str | None = None,
    summary: Callable[[dict], str] | None = None,
    kit: str | None = None,
):
    def wrap(fn: Callable) -> Callable:
        schema, ctx_param, description = schema_for(fn)
        t = Tool(
            name=name or fn.__name__,
            description=description or fn.__name__.replace("_", " "),
            parameters=schema,
            fn=fn,
            risk=risk,
            category=category,
            summarize=summary,
            wants_context=ctx_param,
            kit=kit,
        )
        _DECORATED.append(t)
        fn.__orion_tool__ = t  # type: ignore[attr-defined]
        return fn

    return wrap


def decorated_tools() -> list[Tool]:
    return list(_DECORATED)
