"""Claude provider for the agent loop (optional; set [agent].provider = "claude").

Uses Claude's server-side web search, adaptive thinking and refusal
fallbacks. The key comes from the environment variable in [claude].api_key_env.
"""

from __future__ import annotations

import os
from typing import Any

from .agent_loop import CONTINUE, DONE, MAX_TOKENS, REFUSAL, TOOLS, AgentLoop, Turn
from .runners import RunContext
from .tools import Toolbox

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = "web_search_20260209"


def _dump(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json", exclude_none=True)


class ClaudeAgentRunner(AgentLoop):
    provider = "claude"

    def __init__(self, cfg: dict, client: Any = None, toolbox: Toolbox | None = None):
        super().__init__(cfg, toolbox)
        self.ccfg = cfg["claude"]
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("The anthropic package is not installed (pip install anthropic)") from exc
        env = self.ccfg["api_key_env"]
        key = os.environ.get(env)
        if not key:
            raise RuntimeError(f"Set the {env} environment variable to your Claude API key")
        self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def _tools(self) -> list[dict]:
        tools = self.toolbox.definitions()
        if self.cfg["agent"].get("web_search"):
            tools.append({
                "type": WEB_SEARCH_TOOL,
                "name": "web_search",
                "max_uses": int(self.ccfg.get("web_search_max_uses", 5)),
            })
        return tools

    def describe_error(self, exc: Exception) -> str | None:
        try:
            import anthropic
        except ImportError:
            return None
        if isinstance(exc, anthropic.APIError):
            return f"Claude API error: {exc}"
        return None

    # -- loop hooks --------------------------------------------------------

    def initial_messages(self, prompt: str) -> list[dict]:
        return [{"role": "user", "content": prompt}]

    def call_model(self, messages: list[dict], step: int, ctx: RunContext) -> Turn:
        client = self._get_client()
        if hasattr(client, "with_options"):
            client = client.with_options(timeout=max(30.0, min(600.0, ctx.remaining())))
        params: dict[str, Any] = {
            "model": self.ccfg["model"],
            "max_tokens": int(self.ccfg["max_tokens"]),
            "system": self.system_prompt(),
            "tools": self._tools(),
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.ccfg["effort"]},
        }
        if self.ccfg.get("fallbacks"):
            response = client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            response = client.messages.create(**params)

        content = [_dump(b) for b in response.content]
        message = {"role": "assistant", "content": content}
        text = "\n".join(b["text"] for b in content if b.get("type") == "text")
        stop = response.stop_reason
        if stop == "refusal":
            return Turn(message, REFUSAL)
        if stop == "pause_turn":
            return Turn(message, CONTINUE)  # server-side tool loop paused; resend to resume
        calls = [{"id": b["id"], "name": b["name"], "input": b.get("input") or {}}
                 for b in content if b.get("type") == "tool_use"]
        if calls:
            return Turn(message, TOOLS, text=text, calls=calls)
        if stop == "max_tokens":
            return Turn(message, MAX_TOKENS, text=text)
        return Turn(message, DONE, text=text)

    def results_message(self, calls: list[dict], results: dict[str, dict]) -> dict:
        # All results go back in one user message, in the order of the calls.
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c["id"], "content": results[c["id"]]["content"],
             "is_error": results[c["id"]]["is_error"]}
            for c in calls
        ]}

    def add_nudge(self, messages: list[dict]) -> None:
        messages.append({"role": "user", "content": "Continue the task, then reply with a short summary."})
