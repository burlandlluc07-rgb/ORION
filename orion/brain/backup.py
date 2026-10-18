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

import base64
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
    def _error(resp: httpx.Response) -> BackupError:
        try:
            body = resp.json()
            msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        except Exception:
            msg = resp.text[:200]
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
        body = self._body(contents, system, tools, model, stream=True)
        calls: dict[int, dict] = {}
        tokens = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as c:
            async with c.stream("POST", base + "/chat/completions", headers=self._headers(key), json=body) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise self._error(r)
                served = r.headers.get("x-omniroute-decision", "")
                yield "model", f"backup · {model}" + (f" ({served[:60]})" if served else "")
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
        for i in sorted(calls):
            if calls[i]["name"]:
                yield "part", _call_part(calls[i])
        yield "usage", tokens

    async def generate_text(self, contents: str | list[types.Content], *, system: str | None = None,
                            json_mode: bool = False) -> str:
        if isinstance(contents, str):
            contents = [types.Content(role="user", parts=[types.Part(text=contents)])]
        base, model, key = self.endpoint()
        body = self._body(contents, system, None, model, stream=False, json_mode=json_mode)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as c:
                r = await c.post(base + "/chat/completions", headers=self._headers(key), json=body)
                if r.status_code == 400 and json_mode:  # not every model takes response_format; the prompt asks for JSON
                    body.pop("response_format", None)
                    r = await c.post(base + "/chat/completions", headers=self._headers(key), json=body)
        except httpx.HTTPError as exc:
            raise self._unreachable(base, exc) from exc
        if r.status_code >= 400:
            raise self._error(r)
        choices = r.json().get("choices") or []
        return ((choices[0].get("message") or {}).get("content") or "") if choices else ""
