"""Provider-neutral agent loop: call the model, run or gate its tool calls,
repeat until it answers.

A manual loop (not an SDK helper) because a task has to be able to stop
mid-turn, sit in the database as needs_approval for hours, survive a restart,
and resume exactly where it left off. The whole conversation is saved after
every step and history is only ever appended to.

A provider (gemini_agent.py, claude_agent.py) supplies how to call the model,
how to read its reply into a Turn, and how to send tool results back.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass, field

from .runners import Completed, Failed, NeedsApproval, RunContext
from .safety import ALLOW, APPROVE, DENY
from .tools import Toolbox

log = logging.getLogger("orion_bg.agent")

DENIED = "The user denied this action. Do not try to get around the denial; continue without it or explain what you could not do."
MAX_EMPTY_NUDGES = 2

SYSTEM_PROMPT = """You are ORION's background worker. You carry out one task for the user while they keep using their computer.

How you work:
- You run in the background with no window. You cannot see the screen, click, type or switch windows, and you must not try to (for example, do not use shell commands that open apps, browsers or windows).
- Use only your tools: files inside the allowed folders, shell commands with no window, web search, and a quiet notification.
- Some actions (overwriting or deleting files, unknown shell commands) pause for the user's approval. That is normal; if one is denied, carry on without it.
- Never claim something worked without evidence from a tool result.
- Finish with a short plain-text answer: what you did, what you found, and anything left undone.

Allowed folders:
{roots}

Operating system: {os}
"""

# Turn.stop values
DONE = "done"            # final answer, no tool calls
TOOLS = "tools"          # the model asked for tools
CONTINUE = "continue"    # resend as-is (e.g. a paused server-side tool loop)
REFUSAL = "refusal"      # the model or a safety filter declined
MAX_TOKENS = "max_tokens"
EMPTY = "empty"          # nothing usable came back; nudge the model


@dataclass
class Turn:
    message: dict | None           # appended to history unchanged (None: append nothing)
    stop: str
    text: str = ""
    calls: list[dict] = field(default_factory=list)  # each {"id", "name", "input", ...}
    detail: str = ""


class AgentLoop:
    provider = "base"

    def __init__(self, cfg: dict, toolbox: Toolbox | None = None):
        self.cfg = cfg
        self.toolbox = toolbox or Toolbox(cfg)

    # -- provider hooks ----------------------------------------------------

    def initial_messages(self, prompt: str) -> list[dict]:
        raise NotImplementedError

    def call_model(self, messages: list[dict], step: int, ctx: RunContext) -> Turn:
        raise NotImplementedError

    def results_message(self, calls: list[dict], results: dict[str, dict]) -> dict:
        raise NotImplementedError

    def add_nudge(self, messages: list[dict]) -> None:
        """Ask the model to carry on after an empty reply."""
        raise NotImplementedError

    def describe_error(self, exc: Exception) -> str | None:
        """A user-facing message for a provider API error, or None to re-raise."""
        return None

    # -- shared ------------------------------------------------------------

    def system_prompt(self) -> str:
        custom = (self.cfg["agent"].get("system_prompt") or "").strip()
        roots = "\n".join(f"- {r}" for r in self.toolbox.roots) or "- (none)"
        text = SYSTEM_PROMPT.format(roots=roots, os=f"{platform.system()} {platform.release()}")
        return text + ("\n" + custom if custom else "")

    def run(self, task: dict, ctx: RunContext):
        try:
            return self._run(task, ctx)
        except RuntimeError as exc:
            return Failed(str(exc))
        except Exception as exc:
            message = self.describe_error(exc)
            if message:
                return Failed(message)
            raise

    def _run(self, task: dict, ctx: RunContext):
        state = task.get("state") or {
            "provider": self.provider,
            "messages": self.initial_messages(task["prompt"]),
            "steps": 0,
        }
        if state.get("provider", self.provider) != self.provider:
            return Failed(f"This task was started with {state['provider']}; it cannot resume on {self.provider}")

        if state.get("final") is not None:
            return Completed(state["final"])  # finished just before a crash
        if state.get("pending"):
            self._resume_after_approval(state, ctx)
            ctx.save_state(state)
        elif state.get("open_calls"):
            # Interrupted (shutdown or crash) after the model asked for tools
            # but before the results were saved: handle those calls first.
            parked = self._handle_calls(state, state["open_calls"], ctx)
            if parked:
                return parked

        max_steps = int(self.cfg["tasks"]["max_steps"])
        nudges = 0
        while True:
            ctx.check()
            if state["steps"] >= max_steps:
                return Failed(f"Stopped after {max_steps} steps without finishing")
            turn = self.call_model(state["messages"], state["steps"], ctx)
            state["steps"] += 1
            if turn.message is not None:
                state["messages"].append(turn.message)
            state["open_calls"] = turn.calls if turn.stop == TOOLS else None
            ctx.save_state(state)

            if turn.stop == REFUSAL:
                return Failed("The model declined this task" + (f" ({turn.detail})" if turn.detail else ""))
            if turn.stop == CONTINUE:
                continue
            if turn.stop == EMPTY:
                if nudges >= MAX_EMPTY_NUDGES:
                    return Failed("The model kept returning empty answers" + (f" ({turn.detail})" if turn.detail else ""))
                nudges += 1
                self.add_nudge(state["messages"])
                ctx.save_state(state)
                continue
            if turn.stop == MAX_TOKENS:
                return Failed("The answer was cut off at the output limit: " + turn.text[-500:])
            if turn.stop == DONE:
                state["final"] = turn.text.strip() or "(finished with no text)"
                ctx.save_state(state)
                return Completed(state["final"])

            parked = self._handle_calls(state, turn.calls, ctx)
            if parked:
                return parked

    def _handle_calls(self, state: dict, calls: list[dict], ctx: RunContext) -> NeedsApproval | None:
        """Run allowed calls, refuse denied ones, and park the task if any
        call needs approval. Otherwise append the results and return None."""
        results: dict[str, dict] = {}
        requests: list[dict] = []
        for call in calls:
            verdict = self.toolbox.check(call["name"], call.get("input") or {})
            if verdict.action == ALLOW:
                results[call["id"]] = self._execute(call, ctx)
            elif verdict.action == APPROVE:
                requests.append({
                    "call_id": call["id"],
                    "tool": call["name"],
                    "input": call.get("input") or {},
                    "reason": verdict.reason,
                })
            else:
                results[call["id"]] = {"content": f"Refused: {verdict.reason}", "is_error": True}

        state["open_calls"] = None
        if requests:
            state["pending"] = {"calls": calls, "results": results}
            return NeedsApproval(state=state, requests=requests)

        state["messages"].append(self.results_message(calls, results))
        ctx.save_state(state)
        return None

    def _resume_after_approval(self, state: dict, ctx: RunContext) -> None:
        pending = state.pop("pending")
        decisions = state.pop("decisions", {})
        results = pending["results"]
        for call in pending["calls"]:
            if call["id"] in results:
                continue
            if decisions.get(call["id"]):
                # Re-check: the file system may have changed while we waited.
                verdict = self.toolbox.check(call["name"], call.get("input") or {})
                if verdict.action == DENY:
                    results[call["id"]] = {"content": f"Refused: {verdict.reason}", "is_error": True}
                else:
                    results[call["id"]] = self._execute(call, ctx)
            else:
                results[call["id"]] = {"content": DENIED, "is_error": True}
        state["messages"].append(self.results_message(pending["calls"], results))

    def _execute(self, call: dict, ctx: RunContext) -> dict:
        ctx.check()
        try:
            out = self.toolbox.run(call["name"], call.get("input") or {}, ctx)
            return {"content": out, "is_error": out.startswith("Error:")}
        except Exception as exc:
            log.warning("tool %s failed: %s", call["name"], exc)
            return {"content": f"Error: {type(exc).__name__}: {exc}", "is_error": True}


def make_runner(cfg: dict) -> AgentLoop:
    provider = str(cfg["agent"].get("provider", "gemini")).lower()
    if provider == "gemini":
        from .gemini_agent import GeminiAgentRunner

        return GeminiAgentRunner(cfg)
    if provider == "claude":
        from .claude_agent import ClaudeAgentRunner

        return ClaudeAgentRunner(cfg)
    raise ValueError(f"Unknown agent provider {provider!r}; use 'gemini' or 'claude'")


__all__ = ["AgentLoop", "Turn", "make_runner", "DENIED"]
