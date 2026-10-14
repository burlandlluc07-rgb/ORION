"""Gemini client wrapper: streaming with automatic model fallback, vision, transcription, discovery."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from google import genai
from google.genai import errors, types

from ..config import Config
from ..events import EventBus
from .governor import Governor, QuotaExhausted

log = logging.getLogger("orion.gemini")

RETRYABLE = {429, 500, 502, 503, 504}


class BrainError(Exception):
    pass


class NotConfigured(BrainError):
    pass


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, QuotaExhausted):
        return str(exc)
    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        msg = str(getattr(exc, "message", "") or exc)
        if code in (400, 403) and ("API key" in msg or "API_KEY" in msg or "PERMISSION" in msg):
            return "Your Gemini API key was rejected. Check it in Settings → Brain."
        if code == 404:
            return f"Model not available for this key ({msg[:120]}). Pick another model in Settings → Brain."
        if code == 429:
            return "Gemini rate limit reached. ORION will use another model or try again shortly."
        if code and code >= 500:
            return "Gemini is temporarily unavailable. Please try again in a moment."
        return f"Gemini error {code}: {msg[:200]}"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Gemini took too long to respond."
    return f"{type(exc).__name__}: {exc}"


class GeminiBrain:
    def __init__(self, config: Config, governor: Governor, bus: EventBus) -> None:
        self.config = config
        self.governor = governor
        self.bus = bus
        self._client: genai.Client | None = None
        self._client_key = ""
        self.live_model: str | None = None

    # ── Client ─────────────────────────────────────────────────────────────
    @property
    def client(self) -> genai.Client:
        key = self.config.get_api_key()
        if not key:
            raise NotConfigured("No Gemini API key yet. Add one in Settings (it's free at aistudio.google.com).")
        if self._client is None or key != self._client_key:
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=60_000))
            self._client_key = key
        return self._client

    def reset(self) -> None:
        self._client = None

    @staticmethod
    async def validate_key(key: str) -> tuple[bool, str, list[str]]:
        try:
            client = genai.Client(api_key=key.strip())
            names = []
            async for m in await client.aio.models.list(config={"page_size": 200}):
                names.append(m.name.removeprefix("models/"))
            return True, "Key works.", names
        except errors.APIError as exc:
            return False, friendly_error(exc), []
        except Exception as exc:
            return False, f"Couldn't reach Gemini: {exc}", []

    async def discover(self) -> dict:
        """List models available to this key and pick a Live (voice) model."""
        names: list[str] = []
        live: list[str] = []
        async for m in await self.client.aio.models.list(config={"page_size": 200}):
            name = m.name.removeprefix("models/")
            names.append(name)
            if "bidiGenerateContent" in (m.supported_actions or []):
                live.append(name)
        pref = self.config.settings.model_live
        if pref != "auto" and pref in live:
            self.live_model = pref
        else:
            # Prefer native-audio flash models (most natural voice, free tier), newest first.
            ranked = sorted(live, key=lambda n: ("native-audio" in n, "flash" in n, "preview" not in n, n), reverse=True)
            self.live_model = ranked[0] if ranked else None
        return {"models": sorted(names), "live_models": live, "live_model": self.live_model}

    # ── Generation ─────────────────────────────────────────────────────────
    def _config(self, system: str | None, tools: list[dict] | None, model: str, **extra: Any) -> types.GenerateContentConfig:
        cfg: dict[str, Any] = {"temperature": 0.6, "max_output_tokens": 4096, **extra}
        if system:
            cfg["system_instruction"] = system
        if tools:
            cfg["tools"] = [types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in tools])]
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        budget = self.config.settings.thinking_budget
        if "lite" not in model or budget:
            # Thinking adds latency; keep it off unless the user raised the budget.
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
        return types.GenerateContentConfig(**cfg)

    async def stream(
        self, contents: list[types.Content], *, system: str | None = None, tools: list[dict] | None = None,
        tier: str = "fast", patient: bool = False,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield ("model", name) first, then ("part", Part) for every part, then ("usage", n).

        Falls back to the next model in the tier chain if a request fails before
        anything was yielded.
        """
        tried: set[str] = set()
        last_exc: BaseException | None = None
        for _ in range(3):
            model = await self.governor.acquire(tier, exclude=tried, max_wait=180 if patient else 6)
            tried.add(model)
            yielded = False
            try:
                cfg = self._config(system, tools, model)
                try:
                    it = await self.client.aio.models.generate_content_stream(model=model, contents=contents, config=cfg)
                except errors.ClientError as exc:
                    if exc.code == 400 and "thinking" in str(exc).lower():
                        cfg.thinking_config = None
                        it = await self.client.aio.models.generate_content_stream(model=model, contents=contents, config=cfg)
                    else:
                        raise
                yield "model", model
                tokens = 0
                async for chunk in it:
                    if chunk.usage_metadata and chunk.usage_metadata.total_token_count:
                        tokens = chunk.usage_metadata.total_token_count
                    cand = chunk.candidates[0] if chunk.candidates else None
                    if cand and cand.content and cand.content.parts:
                        for part in cand.content.parts:
                            yielded = True
                            yield "part", part
                self.governor.record(model, tokens)
                yield "usage", tokens
                return
            except errors.APIError as exc:
                self.governor.record(model)
                last_exc = exc
                if exc.code == 429:
                    delay = self.governor.cool_down(model, str(exc))
                    log.warning("Rate limited on %s for %.0fs: %s", model, delay, str(exc)[:400])
                if yielded or exc.code not in RETRYABLE | {404}:
                    raise
                log.warning("Model %s failed (%s); falling back", model, exc.code)
            except QuotaExhausted:
                raise
        raise last_exc or BrainError("All models failed.")

    async def generate_text(self, prompt: str | list, *, system: str | None = None, tier: str = "fast",
                            json_mode: bool = False, patient: bool = False) -> str:
        """One-shot request with the same resilience as streaming: on overload (503), rate limits or
        server errors it retries once and then falls back to the other model in the tier chain."""
        contents = prompt if isinstance(prompt, list) else [types.Content(role="user", parts=[types.Part(text=prompt)])]
        extra = {"response_mime_type": "application/json"} if json_mode else {}
        tried: set[str] = set()
        last_exc: BaseException | None = None
        for attempt in range(3):
            model = await self.governor.acquire(tier, exclude=tried, max_wait=180 if patient else 6)
            try:
                r = await self.client.aio.models.generate_content(
                    model=model, contents=contents, config=self._config(system, None, model, **extra))
                return r.text or ""
            except errors.APIError as exc:
                last_exc = exc
                if exc.code == 429:
                    delay = self.governor.cool_down(model, str(exc))
                    log.warning("Rate limited on %s for %.0fs: %s", model, delay, str(exc)[:400])
                if exc.code not in RETRYABLE | {404}:
                    raise
                log.warning("Model %s failed (%s); %s", model, exc.code, "retrying" if attempt == 0 else "falling back")
                if attempt == 0 and exc.code in (500, 502, 503, 504):
                    await asyncio.sleep(1.0)  # transient overload: one quick retry on the same model first
                else:
                    tried.add(model)
            except QuotaExhausted:
                raise
            finally:
                self.governor.record(model)
        raise last_exc or BrainError("All models failed.")

    async def vision(self, question: str, data: bytes, mime: str) -> str:
        contents = [types.Content(role="user", parts=[types.Part.from_bytes(data=data, mime_type=mime),
                                                      types.Part(text=question)])]
        return await self.generate_text(
            contents, tier="smart",
            system="You are the eyes of a desktop assistant. Answer precisely and briefly about the screenshot. "
                   "Quote exact on-screen text when relevant. If asked where something is, give approximate "
                   "pixel coordinates relative to the image size.",
        )

    async def transcribe(self, wav: bytes) -> str:
        contents = [types.Content(role="user", parts=[
            types.Part.from_bytes(data=wav, mime_type="audio/wav"),
            types.Part(text="Transcribe this speech verbatim. Output only the transcript, nothing else. "
                            "If there is no intelligible speech, output nothing."),
        ])]
        return (await self.generate_text(contents, tier="fast")).strip()
