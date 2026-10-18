"""Backup brain: an OpenAI-compatible provider ORION switches to when Gemini's free quota runs out.

It speaks the Chat Completions API, so it works with a local OmniRoute gateway
(http://localhost:20128/v1, which itself falls back across many free providers),
OpenRouter, Groq or any other OpenAI-compatible endpoint. Gemini stays the main
brain; this one only answers while every Gemini model is rate-limited or out
for the day, and ORION goes back to Gemini as soon as it has quota again.

Callers keep using Gemini types: contents, tool declarations and the streamed
("model" | "part" | "usage") tuples are translated here, so the agent loop,
tools and approvals work unchanged. The key lives in the OS credential vault
(see Config.get_backup_key) and is never logged.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx
from google.genai import types

from ..config import Config

log = logging.getLogger("orion.backup")


@dataclass(frozen=True)
class Preset:
    label: str
    base_url: str
    model: str  # "" = pick one from the provider's model list when the key is tested
    key_url: str
    needs_key: bool = True


PRESETS: dict[str, Preset] = {
    "omniroute": Preset("OmniRoute on this PC", "http://localhost:20128/v1", "auto",
                        "https://github.com/diegosouzapw/OmniRoute", needs_key=False),
    "openrouter": Preset("OpenRouter (free models)", "https://openrouter.ai/api/v1", "",
                         "https://openrouter.ai/keys"),
    "groq": Preset("Groq", "https://api.groq.com/openai/v1", "",  # Groq retires models often: pick from its list
                   "https://console.groq.com/keys"),
    "custom": Preset("Custom (OpenAI-compatible)", "", "", "", needs_key=False),
}


class BackupError(Exception):
    pass


def is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def pick_default_model(provider: str, models: list[str]) -> str:
    """A sensible model from the provider's list when none was chosen."""
    if not models:
        return ""
    if provider == "openrouter":
        free = [m for m in models if m.endswith(":free")]
        for want in ("llama-3.3-70b", "gemma", "qwen", "deepseek", "mistral"):
            hit = next((m for m in free if want in m), None)
            if hit:
                return hit
        return free[0] if free else ""
    if "auto" in models:
        return "auto"
    chat = [m for m in models if not re.search(r"whisper|guard|tts|embed|playai|orpheus|moderation|image|audio", m, re.I)]
    for want in ("llama-3.3-70b", "gpt-oss-120b", "llama-4", "qwen", "gpt-oss", "llama", "kimi", "mistral", "gemma"):
        hit = next((m for m in chat if want in m.lower()), None)
        if hit:
            return hit
    return chat[0] if chat else ""


# ── Gemini → OpenAI translation ────────────────────────────────────────────
class NeedsGemini(BackupError):
    """The request has content the backup can't take (audio, video, documents)."""


def to_messages(contents: list[types.Content], system: str | None) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": system}] if system else []
    pending: list[tuple[str, str]] = []  # (call id, name) of model calls still waiting for a response
    seq = 0
    for content in contents:
        role = "assistant" if content.role == "model" else "user"
        text: list[str] = []
        images: list[dict] = []
        calls: list[dict] = []
        results: list[dict] = []
        for part in content.parts or []:
            if part.thought:
                continue
            if part.function_call:
                seq += 1
                cid = part.function_call.id or f"call_{seq}"
                pending.append((cid, part.function_call.name or ""))
                calls.append({"id": cid, "type": "function", "function": {
                    "name": part.function_call.name, "arguments": json.dumps(part.function_call.args or {})}})
            elif part.function_response:
                fr = part.function_response
                cid = fr.id or next((c for c, n in pending if n == fr.name), None) or f"call_{seq}"
                pending = [(c, n) for c, n in pending if c != cid]
                results.append({"role": "tool", "tool_call_id": cid,
                                "content": json.dumps(fr.response or {}, ensure_ascii=False, default=str)})
            elif part.inline_data and part.inline_data.data:
                mime = part.inline_data.mime_type or ""
                if not mime.startswith("image/"):
                    raise NeedsGemini(f"The backup brain can't read {mime or 'this attachment'}.")
                b64 = base64.b64encode(part.inline_data.data).decode()
                images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            elif part.text:
                text.append(part.text)
        msgs.extend(results)
        if role == "assistant":
            if text or calls:
                m: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
                if calls:
                    m["tool_calls"] = calls
                msgs.append(m)
        elif images:
            msgs.append({"role": "user", "content": [{"type": "text", "text": "\n".join(text)}, *images]})
        elif text:
            msgs.append({"role": "user", "content": "\n".join(text)})
    return msgs


def to_tools(decls: list[dict] | None) -> list[dict]:
    out = []
    for d in decls or []:
        params = d.get("parameters_json_schema") or d.get("parameters") or {"type": "object", "properties": {}}
        out.append({"type": "function", "function": {
            "name": d["name"], "description": d.get("description", ""), "parameters": params}})
    return out


# ── Fitting a request into a small provider limit ─────────────────────────
# Free plans cap a request's size: Groq counts the prompt plus max_tokens against a tokens-per-minute
# limit (8000 for openai/gpt-oss-120b), and ORION's system prompt, tools and history can be bigger than
# that. When a provider says a request is too large, the request is trimmed to fit and sent again.
CHARS_PER_TOKEN = 3.2  # rough and on the safe side; corrected with the provider's own count when it gives one
MIN_REPLY = 1024
_TPM_RE = re.compile(r"limit\D{0,3}(\d+)\D+requested\D{0,3}(\d+)", re.I)
_CONTEXT_RE = re.compile(r"maximum context length is (\d+)", re.I)
_CUT = "… [cut to fit the backup brain]"


def estimate_tokens(body: dict) -> int:
    size = len(json.dumps(body.get("messages") or [], ensure_ascii=False))
    size += len(json.dumps(body.get("tools") or [], ensure_ascii=False))
    return int(size / CHARS_PER_TOKEN) + int(body.get("max_tokens") or 0)


def too_large(status: int, msg: str) -> tuple[int, int] | None:
    """(limit, requested) in the provider's tokens when it refused the request as too large, else None.
    Either number is 0 when the provider didn't say."""
    low = msg.lower()
    if status == 413 or "too large" in low or "context length" in low or "reduce your message" in low:
        if m := _TPM_RE.search(msg):
            return int(m.group(1)), int(m.group(2))
        if m := _CONTEXT_RE.search(msg):
            return int(m.group(1)), 0
        return 0, 0
    return None


def _cut(text: str, keep: int) -> str:
    return text if len(text) <= keep else text[:keep] + _CUT


def _first_sentence(text: str, keep: int = 160) -> str:
    head = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return _cut(head, keep)


def _slim_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _slim_schema(v) for k, v in schema.items() if k not in ("description", "examples", "title")}
    if isinstance(schema, list):
        return [_slim_schema(v) for v in schema]
    return schema


def fit_body(body: dict, limit: int) -> dict:
    """A copy of a Chat Completions body trimmed to about ``limit`` estimated tokens (see estimate_tokens).

    In order, until it fits: a smaller reply budget, long tool results and old messages cut short,
    shorter tool descriptions, the oldest turns dropped (the current request is always kept whole),
    a shorter system prompt, and finally fewer tools."""
    body = copy.deepcopy(body)
    target = int(limit * 0.9)
    body["max_tokens"] = max(MIN_REPLY, min(int(body.get("max_tokens") or 4096), target // 4))

    msgs: list[dict] = body["messages"]
    system = msgs[0] if msgs and msgs[0]["role"] == "system" else None
    rest = msgs[1:] if system else msgs[:]

    def over() -> bool:
        body["messages"] = ([system] if system else []) + rest
        return estimate_tokens(body) > target

    # The current request: from the last real user message on (it may be followed by tool calls and results).
    start = max((i for i, m in enumerate(rest) if m["role"] == "user"), default=0)

    if over():
        for i, m in enumerate(rest):
            if isinstance(m.get("content"), str):
                m["content"] = _cut(m["content"], 1500 if m["role"] == "tool" or i < start else 6000)
    if over() and body.get("tools"):
        for t in body["tools"]:
            fn = t["function"]
            fn["description"] = _first_sentence(fn.get("description") or "")
            fn["parameters"] = _slim_schema(fn.get("parameters") or {})
    while over() and start > 0:
        # Drop the oldest turn: everything up to the next real user message, so no tool result loses its call.
        nxt = next((i for i in range(1, start + 1) if rest[i]["role"] == "user"), start)
        del rest[:nxt]
        start -= nxt
    def cut_system(floor: int) -> None:
        if over() and system and isinstance(system.get("content"), str):
            others = estimate_tokens({**body, "messages": rest})
            room = int((target - others) * CHARS_PER_TOKEN)
            system["content"] = _cut(system["content"], max(floor, room))

    cut_system(4000)
    if over() and body.get("tools"):
        # Drop tools from the end of the list, keeping the ones this request already uses and open_toolkit.
        used = {c["function"]["name"] for m in rest for c in m.get("tool_calls") or []}
        tools = body["tools"]
        for t in reversed(list(tools)):
            if not over():
                break
            name = t["function"]["name"]
            if name not in used and "toolkit" not in name:
                tools.remove(t)
        if not tools:
            body.pop("tools")
    cut_system(1500)
    if over():
        for m in rest:
            if isinstance(m.get("content"), str):
                m["content"] = _cut(m["content"], 1500)
    body["messages"] = ([system] if system else []) + rest
    return body


def _retry_after(resp: httpx.Response, msg: str) -> float | None:
    """Seconds a rate-limited provider asks us to wait, when it says."""
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", msg)
    if not m:
        return None
    secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return secs + 60 * int(m.group(1) or 0)


def _call_part(call: dict) -> types.Part:
    raw = call.get("arguments") or "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    part = types.Part.from_function_call(name=call.get("name", ""), args=args)
    if call.get("id"):
        part.function_call.id = call["id"]
    return part


class BackupBrain:
    def __init__(self, config: Config, endpoint: tuple[str, str, str] | None = None) -> None:
        self.config = config
        self._fixed = endpoint  # (base_url, model, key) to test a provider before it's saved
        self._limits: dict[tuple[str, str], int] = {}  # (base_url, model) → request size it takes, in our estimate

    # ── Settings ───────────────────────────────────────────────────────────
    def endpoint(self) -> tuple[str, str, str]:
        """→ (base_url, model, key) from settings; empty base_url when the backup is off."""
        if self._fixed:
            return self._fixed
        s = self.config.settings
        preset = PRESETS.get(s.backup_provider)
        if preset is None:
            return "", "", ""
        base = (s.backup_base_url or preset.base_url).rstrip("/")
        model = s.backup_model or preset.model
        return base, model, self.config.get_backup_key()

    def ready(self) -> bool:
        base, model, key = self.endpoint()
        if not base or not model:
            return False
        preset = PRESETS[self.config.settings.backup_provider]
        return bool(key) or not preset.needs_key

    def status(self) -> dict:
        s = self.config.settings
        base, model, key = self.endpoint()
        return {"provider": s.backup_provider, "base_url": base, "model": model,
                "has_key": bool(key), "ready": self.ready()}

    @staticmethod
    def _headers(key: str) -> dict:
        h = {"Content-Type": "application/json", "X-Title": "ORION"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    @staticmethod
    def _message(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        except Exception:
            msg = resp.text[:300]
        return str(msg or "")

    @classmethod
    def _error(cls, resp: httpx.Response) -> BackupError:
        msg = cls._message(resp)
        if too_large(resp.status_code, msg) is not None:
            return BackupError("This request is too big for the backup brain's plan, even trimmed down. Start a new "
                               "chat, or pick a model with a bigger limit in Settings → Brain → Backup brain.")
        if resp.status_code in (401, 403):
            return BackupError("The backup brain rejected the key. Check it in Settings → Brain → Backup brain.")
        if resp.status_code == 429:
            return BackupError("The backup brain is rate-limited too. Try again in a minute.")
        return BackupError(f"Backup brain error {resp.status_code}: {str(msg or '')[:200]}")

    @classmethod
    async def list_models(cls, base_url: str, key: str) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(base_url.rstrip("/") + "/models", headers=cls._headers(key))
        except httpx.HTTPError as exc:
            raise BackupError(f"Couldn't reach {base_url} ({type(exc).__name__}). Check the address, "
                              "and for OmniRoute that it's running.") from exc
        if r.status_code >= 400:
            raise cls._error(r)
        data = r.json().get("data") or []
        return [m["id"] for m in data if isinstance(m, dict) and m.get("id")]

    # ── Generation ─────────────────────────────────────────────────────────
    def _body(self, contents: list[types.Content], system: str | None, tools: list[dict] | None,
              model: str, *, stream: bool, json_mode: bool = False) -> dict:
        body: dict[str, Any] = {"model": model, "messages": to_messages(contents, system),
                                "temperature": 0.6, "max_tokens": 4096, "stream": stream}
        if tools:
            body["tools"] = to_tools(tools)
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _fitted(self, body: dict, base: str, model: str) -> dict:
        limit = self._limits.get((base, model))
        return fit_body(body, limit) if limit and estimate_tokens(body) > limit * 0.9 else body

    async def _should_retry(self, resp: httpx.Response, body: dict, base: str, model: str, attempt: int) -> bool:
        """After a refused request: learn the provider's size limit (the next send is trimmed to it), or wait
        out a short rate limit. True when sending again may work."""
        if attempt >= 2:
            return False
        msg = self._message(resp)
        big = too_large(resp.status_code, msg)
        if big is not None:
            limit, requested = big
            est = estimate_tokens(body)
            if limit and requested:
                fit = int(limit * est / requested)  # the provider's limit in our estimate's units
            elif limit:
                fit = limit
            else:
                fit = int(est * 0.6)
            old = self._limits.get((base, model))
            self._limits[(base, model)] = min(fit, old) if old else fit
            log.info("Backup brain request too large (%s est. tokens); trimming to %s", est, self._limits[(base, model)])
            return True
        if resp.status_code == 429:
            wait = _retry_after(resp, msg)
            if wait is not None and wait <= 30:
                log.info("Backup brain rate-limited; waiting %.1fs", wait)
                await asyncio.sleep(wait + 0.5)
                return True
        return False

    def _unreachable(self, base: str, exc: Exception) -> BackupError:
        hint = " Is OmniRoute running?" if self.config.settings.backup_provider == "omniroute" else ""
        return BackupError(f"Couldn't reach the backup brain at {base} ({type(exc).__name__}).{hint}")

    async def stream(self, contents: list[types.Content], *, system: str | None = None,
                     tools: list[dict] | None = None) -> AsyncIterator[tuple[str, Any]]:
        """Same protocol as GeminiBrain.stream: ("model", name), ("part", Part)…, ("usage", tokens)."""
        base = self.endpoint()[0]
        try:
            async for item in self._stream(contents, system=system, tools=tools):
                yield item
        except httpx.HTTPError as exc:
            raise self._unreachable(base, exc) from exc

    async def _stream(self, contents: list[types.Content], *, system: str | None,
                      tools: list[dict] | None) -> AsyncIterator[tuple[str, Any]]:
        base, model, key = self.endpoint()
        full = self._body(contents, system, tools, model, stream=True)
        calls: dict[int, dict] = {}
        tokens = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as c:
            for attempt in range(3):
                body = self._fitted(full, base, model)
                async with c.stream("POST", base + "/chat/completions", headers=self._headers(key), json=body) as r:
                    if r.status_code < 400:
                        async for item in self._read_stream(r, model, calls):
                            if item[0] == "usage":
                                tokens = item[1]
                            else:
                                yield item
                        break
                    await r.aread()
                    if not await self._should_retry(r, body, base, model, attempt):
                        raise self._error(r)
        for i in sorted(calls):
            if calls[i]["name"]:
                yield "part", _call_part(calls[i])
        yield "usage", tokens

    async def _read_stream(self, r: httpx.Response, model: str,
                           calls: dict[int, dict]) -> AsyncIterator[tuple[str, Any]]:
        """("model", name), text parts and finally ("usage", tokens) from an SSE reply; tool calls collect in ``calls``."""
        served = r.headers.get("x-omniroute-decision", "")
        yield "model", f"backup · {model}" + (f" ({served[:60]})" if served else "")
        tokens = 0
        async for line in r.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise BackupError(f"Backup brain error: {str(chunk['error'])[:200]}")
            usage = chunk.get("usage") or {}
            tokens = usage.get("total_tokens") or tokens
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield "part", types.Part(text=delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
        yield "usage", tokens

    async def generate_text(self, contents: str | list[types.Content], *, system: str | None = None,
                            json_mode: bool = False) -> str:
        if isinstance(contents, str):
            contents = [types.Content(role="user", parts=[types.Part(text=contents)])]
        base, model, key = self.endpoint()
        full = self._body(contents, system, None, model, stream=False, json_mode=json_mode)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as c:
                for attempt in range(3):
                    body = self._fitted(full, base, model)
                    r = await c.post(base + "/chat/completions", headers=self._headers(key), json=body)
                    if (r.status_code == 400 and json_mode and "response_format" in full
                            and too_large(400, self._message(r)) is None):
                        # Not every model takes response_format; the prompt asks for JSON anyway.
                        full.pop("response_format")
                        body = self._fitted(full, base, model)
                        r = await c.post(base + "/chat/completions", headers=self._headers(key), json=body)
                    if r.status_code < 400 or not await self._should_retry(r, body, base, model, attempt):
                        break
        except httpx.HTTPError as exc:
            raise self._unreachable(base, exc) from exc
        if r.status_code >= 400:
            raise self._error(r)
        choices = r.json().get("choices") or []
        return ((choices[0].get("message") or {}).get("content") or "") if choices else ""
