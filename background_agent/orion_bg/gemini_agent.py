"""Gemini provider for the agent loop (google-genai, same SDK as ORION).

The key comes from GEMINI_API_KEY (or GOOGLE_API_KEY), else from the key
ORION already saved in the Windows credential vault. It is never written to
the config file or the logs.

Web search is a function tool that makes its own Gemini call with Google
Search grounding and returns the answer with its source links.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .agent_loop import CONTINUE, DONE, EMPTY, MAX_TOKENS, REFUSAL, TOOLS, AgentLoop, Turn
from .runners import RunContext
from .safety import ALLOW, Verdict
from .tools import Tool, Toolbox

log = logging.getLogger("orion_bg.gemini")

# Same entry ORION's config.py uses, so a key saved in ORION works here too.
KEYRING_SERVICE = "ORION"
KEYRING_USER = "gemini_api_key"

RETRY_CODES = (429, 500, 502, 503, 504)
QUOTA_COOLDOWN = 600.0  # seconds a model is skipped after a quota error
BLOCKED = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION",
           "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT"}
NUDGE = ("(Your last reply was empty. Continue the task from where you left off. "
         "When everything is done, reply with a short summary.)")


def gemini_api_key(cfg: dict) -> str | None:
    gcfg = cfg["gemini"]
    for name in (gcfg.get("api_key_env") or "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    if gcfg.get("use_orion_keyring", True):
        try:
            import keyring

            value = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if value:
                return value.strip()
        except Exception:
            pass
    return None


def _enum_name(value: Any) -> str:
    return getattr(value, "name", None) or str(value or "")


def _dump(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    return obj.model_dump(mode="json", exclude_none=True)


class GeminiAgentRunner(AgentLoop):
    provider = "gemini"

    def __init__(self, cfg: dict, client: Any = None, toolbox: Toolbox | None = None):
        super().__init__(cfg, toolbox)
        self.gcfg = cfg["gemini"]
        self._client = client
        self._exhausted: dict[str, float] = {}
        self.last_model: str | None = None
        if cfg["agent"].get("web_search"):
            self.toolbox.add(Tool(
                "web_search",
                "Search the web with Google and get a short answer with source links.",
                {"type": "object", "properties": {"query": {"type": "string"}},
                 "required": ["query"], "additionalProperties": False},
                lambda args: Verdict(ALLOW),
                self._web_search,
            ))

    # -- client ------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("The google-genai package is not installed") from exc
        key = gemini_api_key(self.cfg)
        if not key:
            env = self.gcfg.get("api_key_env") or "GEMINI_API_KEY"
            raise RuntimeError(f"Set the {env} environment variable to your Gemini API key")
        self._client = genai.Client(api_key=key)
        return self._client

    def _config(self, ctx: RunContext, with_tools: bool = True, search: bool = False) -> Any:
        from google.genai import types

        timeout_ms = int(max(30.0, min(600.0, ctx.remaining())) * 1000)
        cfg: dict[str, Any] = {
            "max_output_tokens": int(self.gcfg["max_output_tokens"]),
            "http_options": types.HttpOptions(timeout=timeout_ms),
        }
        if with_tools:
            cfg["system_instruction"] = self.system_prompt()
            cfg["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=d["name"], description=d["description"],
                                          parameters_json_schema=d["input_schema"])
                for d in self.toolbox.definitions()
            ])]
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if search:
            cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        budget = self.gcfg.get("thinking_budget")
        if budget is not None and budget != "":
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=int(budget))
        return types.GenerateContentConfig(**cfg)

    def _models(self) -> list[str]:
        """Primary model first, then fallbacks not marked out of quota."""
        now = time.monotonic()
        chain = [self.gcfg["model"], *self.gcfg.get("fallback_models", [])]
        usable = [m for m in dict.fromkeys(chain) if self._exhausted.get(m, 0) <= now]
        return usable or [chain[-1]]

    def _generate(self, contents: Any, config: Any, ctx: RunContext) -> Any:
        """generate_content with backoff on overload, and on a quota error (429)
        a switch to the next model in [gemini].fallback_models, like ORION does."""
        from google.genai import errors

        client = self._get_client()
        attempts = int(self.gcfg.get("retries", 3)) + 1
        models = self._models()
        index, attempt = 0, 0
        while True:
            ctx.check()
            model = models[index]
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
                self.last_model = model
                return response
            except errors.APIError as exc:
                if exc.code == 429 and index + 1 < len(models):
                    self._exhausted[model] = time.monotonic() + QUOTA_COOLDOWN
                    log.warning("Gemini %s is out of quota; switching to %s", model, models[index + 1])
                    index += 1
                    continue
                attempt += 1
                if exc.code not in RETRY_CODES or attempt >= attempts:
                    raise
                wait = min(60.0, 5.0 * (2 ** (attempt - 1)))
                log.warning("Gemini %s on %s; retrying in %.0f s", exc.code, model, wait)
                ctx.sleep(wait)

    def describe_error(self, exc: Exception) -> str | None:
        try:
            from google.genai import errors
        except ImportError:
            return None
        if isinstance(exc, errors.APIError):
            return f"Gemini API error {exc.code}: {exc.message or exc.status}"
        return None

    # -- loop hooks --------------------------------------------------------

    def initial_messages(self, prompt: str) -> list[dict]:
        return [{"role": "user", "parts": [{"text": prompt}]}]

    def call_model(self, messages: list[dict], step: int, ctx: RunContext) -> Turn:
        response = self._generate(messages, self._config(ctx), ctx)
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            reason = _enum_name(getattr(feedback, "block_reason", None))
            if reason:
                return Turn(None, REFUSAL, detail=f"blocked: {reason}")
            return Turn(None, EMPTY, detail="no candidates")
        cand = candidates[0]
        finish = _enum_name(getattr(cand, "finish_reason", None))
        content = getattr(cand, "content", None)
        parts = list(getattr(content, "parts", None) or []) if content is not None else []
        if finish in BLOCKED:
            return Turn(None, REFUSAL, detail=finish)
        if not parts:
            return Turn(None, EMPTY, detail=finish or "no parts")

        message = _dump(content)
        message["role"] = "model"
        calls, texts = [], []
        for i, part in enumerate(parts):
            fc = getattr(part, "function_call", None)
            if fc is not None:
                calls.append({
                    "id": fc.id or f"g{step}-{i}",
                    "native_id": fc.id,
                    "name": fc.name,
                    "input": dict(fc.args or {}),
                })
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                texts.append(part.text)
        text = "".join(texts)
        if calls:
            return Turn(message, TOOLS, text=text, calls=calls)
        if finish == "MAX_TOKENS":
            return Turn(message, MAX_TOKENS, text=text)
        if finish == "MALFORMED_FUNCTION_CALL" or not text.strip():
            return Turn(None, EMPTY, detail=finish)
        return Turn(message, DONE if finish != "CONTINUATION" else CONTINUE, text=text)

    def results_message(self, calls: list[dict], results: dict[str, dict]) -> dict:
        parts = []
        for call in calls:
            res = results[call["id"]]
            response = {"error": res["content"]} if res["is_error"] else {"result": res["content"]}
            fr: dict[str, Any] = {"name": call["name"], "response": response}
            if call.get("native_id"):
                fr["id"] = call["native_id"]
            parts.append({"function_response": fr})
        return {"role": "user", "parts": parts}

    def add_nudge(self, messages: list[dict]) -> None:
        if messages and messages[-1].get("role") == "user":
            messages[-1]["parts"].append({"text": NUDGE})
        else:
            messages.append({"role": "user", "parts": [{"text": NUDGE}]})

    # -- web search --------------------------------------------------------

    def _web_search(self, args: dict, ctx: RunContext) -> str:
        query = str(args["query"]).strip()
        if not query:
            return "Error: empty query"
        response = self._generate(query, self._config(ctx, with_tools=False, search=True), ctx)
        text = (getattr(response, "text", None) or "").strip()
        sources = []
        for cand in getattr(response, "candidates", None) or []:
            meta = getattr(cand, "grounding_metadata", None)
            for chunk in getattr(meta, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web is not None and getattr(web, "uri", None):
                    sources.append(f"- {getattr(web, 'title', None) or web.uri}: {web.uri}")
        if sources:
            text += "\n\nSources:\n" + "\n".join(dict.fromkeys(sources))
        return text or "Error: the search returned nothing"
