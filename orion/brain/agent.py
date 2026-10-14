"""The agent loop: fast path → Gemini with tools → act → verify → answer.

Flow for every request:

1. ``/skill`` prefixes load that skill's playbook.
2. The local router tries to handle it with zero API calls.
3. Otherwise Gemini streams a reply; tool calls are executed (with approval
   for risky ones) and fed back until the model produces a final answer.

Tier routing: short everyday requests go to the fast model (Flash-Lite,
biggest free quota); long, analytical or multi-step requests go to the smart
model. The governor transparently falls back when either runs out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Callable

from google.genai import types

from ..tasks.tracker import Task
from ..tools.registry import ToolContext, ToolError
from . import router
from .gemini import NotConfigured, friendly_error
from .prompts import build_system_prompt

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.agent")

MAX_ROUNDS = 10
TOOL_TIMEOUT = 300
SMART_HINTS = re.compile(
    r"\b(analy[sz]e|explain|why|compare|research|summari[sz]e|write|draft|essay|email|letter|code|script|debug|"
    r"plan|organi[sz]e|step[- ]by[- ]step|then|after that|each|every|all (?:the|my)|report|translate|review)\b",
    re.I,
)

BUILTIN_DECLS = [
    {
        "name": "update_plan",
        "description": "Show the user a checklist for a multi-step job and keep it updated. Call at the start of any "
                       "job with 3+ steps, and again whenever an item is completed.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}, "description": "Short checklist items in order."},
                "done_count": {"type": "integer", "description": "How many items (from the top) are finished."},
            },
            "required": ["items"],
        },
    },
    {
        "name": "use_skill",
        "description": "Load a skill's full playbook (see SKILLS list) and follow it.",
        "parameters_json_schema": {
            "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"],
        },
    },
]


def pick_tier(text: str, has_media: bool = False) -> str:
    if has_media or len(text) > 280:
        return "smart"
    return "smart" if len(SMART_HINTS.findall(text)) >= 2 or len(text.split()) > 45 else "fast"


def make_title(text: str) -> str:
    t = re.sub(r"\s+", " ", router.normalise(text) or text).strip()
    words = t.split(" ")
    title = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
    return (title[:1].upper() + title[1:])[:60] or "New conversation"


class Agent:
    def __init__(self, engine: "Engine") -> None:
        self.e = engine

    # ── Tool execution (shared with the Live voice session) ────────────────
    async def run_tool(self, name: str, args: dict, task: Task, conv_id: str | None) -> tuple[bool, str]:
        e = self.e
        tool = e.registry.get(name)
        if tool is None:
            return False, f"Unknown or disabled tool: {name}"
        label = tool.describe_call(args)
        step = e.tracker.add_step(task, name, label, tool.risk)
        if self.needs_approval(tool.risk, name):
            e.tracker.update_step(task, step, "waiting")
            ok = await e.approvals.request(name, label, tool.risk, args, task)
            if not ok:
                e.tracker.update_step(task, step, "denied", "Declined by user")
                return False, "The user declined this action. Don't retry it; ask what they'd prefer instead."
            e.tracker.update_step(task, step, "running")
        if task.cancel.is_set():
            e.tracker.update_step(task, step, "stopped")
            return False, "Stopped by user."
        ctx = ToolContext(engine=e, cancel=task.cancel, conv_id=conv_id,
                          progress=lambda msg: e.bus.emit("task.progress", task_id=task.id, step_id=step["id"], text=msg))
        try:
            result = await asyncio.wait_for(tool.run(args, ctx), timeout=TOOL_TIMEOUT)
        except ToolError as exc:
            e.tracker.update_step(task, step, "failed", str(exc))
            return False, str(exc)
        except asyncio.TimeoutError:
            e.tracker.update_step(task, step, "failed", "Timed out")
            return False, f"{name} timed out after {TOOL_TIMEOUT}s."
        except TypeError as exc:
            e.tracker.update_step(task, step, "failed", f"Bad arguments: {exc}")
            return False, f"Bad arguments for {name}: {exc}"
        except Exception as exc:
            log.exception("Tool %s crashed", name)
            e.tracker.update_step(task, step, "failed", f"{type(exc).__name__}: {exc}")
            return False, f"{type(exc).__name__}: {exc}"
        e.tracker.update_step(task, step, "done", result)
        return True, result

    def needs_approval(self, risk: str, name: str) -> bool:
        mode = self.e.config.settings.approval_mode
        if risk == "safe" or mode == "trust":
            return False
        if mode == "auto_safe" and risk == "confirm":
            return False
        return True

    # ── Main entry point ───────────────────────────────────────────────────
    async def handle(self, text: str, conv_id: str | None = None, *, source: str = "text", mode: str = "auto",
                     attachments: list[dict] | None = None, extra_system: str = "", max_rounds: int = MAX_ROUNDS,
                     on_task: "Callable[[Task], None] | None" = None) -> dict:
        e = self.e
        text = text.strip()
        attachments = attachments or []
        if not text and not attachments:
            return {"ok": False, "error": "Empty message"}

        conv = e.store.get_conversation(conv_id) if conv_id else None
        if conv is None:
            conv = e.store.create_conversation(make_title(text or "Attachment"))
            e.bus.emit("conversation.created", conversation=conv)
        cid = conv["id"]
        user_msg = e.store.add_message(cid, "user", text, {"source": source,
                                                          "attachments": [a.get("name") for a in attachments]})
        e.bus.emit("message", message=user_msg)

        skill_body, skill_tier = None, None
        m = re.match(r"^/([a-z0-9-]+)\s*(.*)$", text, re.S)
        if m and e.skills.get(m.group(1)):
            sk = e.skills.get(m.group(1))
            skill_body, skill_tier = sk.body, sk.tier
            text = m.group(2).strip() or f"Run the {sk.name} skill now."

        task = e.tracker.start(make_title(text), cid, source)
        if on_task:
            on_task(task)
        try:
            if e.config.settings.fast_path and not skill_body and not attachments and mode != "smart" and not extra_system:
                done = await self._fast_path(text, cid, task, source)
                if done is not None:
                    return done
            return await self._llm(text, cid, task, source=source, mode=mode, skill_body=skill_body,
                                   skill_tier=skill_tier, attachments=attachments, extra_system=extra_system,
                                   max_rounds=max_rounds)
        finally:
            if task.id in e.tracker.active:
                e.tracker.finish(task, "stopped" if task.cancel.is_set() else "done")

    async def _fast_path(self, text: str, cid: str, task: Task, source: str) -> dict | None:
        route = router.match(text)
        if route is None or self.e.registry.get(route.tool) is None:
            return None
        t0 = time.perf_counter()
        ok, result = await self.run_tool(route.tool, route.args, task, cid)
        if not ok and "declined" not in result and "Stopped" not in result and route.fallback \
                and self.e.config.get_api_key():
            log.info("Fast path %s failed (%s); handing to Gemini", route.tool, result)
            task.steps.clear()
            return None
        if ok:
            reply = route.reply(result) if callable(route.reply) else route.reply
        else:
            reply = ("Okay — I won't do that." if "declined" in result else "Stopped." if "Stopped" in result
                     else result)
        ms = round((time.perf_counter() - t0) * 1000)
        msg = self.e.store.add_message(cid, "assistant", reply, {"model": "local", "latency_ms": ms, "task_id": task.id})
        self.e.bus.emit("message", message=msg)
        self.e.tracker.finish(task)
        await self.e.after_reply(reply, source)
        return {"ok": True, "conv_id": cid, "reply": reply, "model": "local"}

    def _history(self, cid: str, limit: int) -> list[types.Content]:
        msgs = self.e.store.messages(cid, limit=limit * 2 + 1)[:-1]  # exclude the just-stored user message
        # Recent assistant turns carry a compact log of the actions they took, so follow-ups like
        # "undo that" or "open the file you made" work without replaying full tool transcripts.
        recent_with_tasks = [m["id"] for m in msgs if m["role"] == "assistant" and m["meta"].get("task_id")][-4:]
        out: list[types.Content] = []
        for msg in msgs:
            text = msg["content"]
            if not text.strip() or msg["role"] not in ("user", "assistant"):
                continue
            if msg["id"] in recent_with_tasks:
                log_line = self._action_log(msg["meta"]["task_id"])
                if log_line:
                    text = f"{log_line}\n{text}"
            role = "model" if msg["role"] == "assistant" else "user"
            if out and out[-1].role == role:  # merge consecutive turns from the same side
                out[-1].parts.append(types.Part(text=text))
            else:
                out.append(types.Content(role=role, parts=[types.Part(text=text)]))
        while out and out[0].role != "user":
            out.pop(0)
        return out

    def _action_log(self, task_id: str) -> str:
        task = self.e.store.get_task(task_id)
        if not task or not task["steps"]:
            return ""
        items = []
        for st in task["steps"][-6:]:
            res = (st.get("result") or "").replace("\n", " ")
            items.append(f"{st['label']} → {st['status']}" + (f" ({res[:90]})" if res and st["status"] == "done" else ""))
        return ("[Actions I took: " + "; ".join(items) + "]")[:700]

    def system_prompt(self, query: str, voice: bool = False, extra: str = "") -> str:
        e = self.e
        mems = [m["text"] for m in e.store.core_memories()]
        for m in e.store.recall(query, limit=5):
            if m["text"] not in mems:
                mems.append(m["text"])
        s = e.config.settings
        return build_system_prompt(persona=s.persona, assistant=s.assistant_name, user=s.user_name, memories=mems,
                                   skills_index=e.skills.index_prompt(), extra=extra, voice=voice)

    async def _llm(self, text: str, cid: str, task: Task, *, source: str, mode: str, skill_body: str | None,
                   skill_tier: str | None, attachments: list[dict], extra_system: str = "",
                   max_rounds: int = MAX_ROUNDS) -> dict:
        e = self.e
        s = e.config.settings
        tier = "smart" if mode == "smart" else ("fast" if mode == "fast" else (skill_tier or pick_tier(text, bool(attachments))))
        extra = f"ACTIVE SKILL — follow these instructions:\n{skill_body}" if skill_body else ""
        if extra_system:
            extra = f"{extra_system}\n\n{extra}".strip()
        system = self.system_prompt(text, voice=source == "voice", extra=extra)

        user_parts: list[types.Part] = []
        for a in attachments:
            if a.get("data") is not None:
                user_parts.append(types.Part.from_bytes(data=a["data"], mime_type=a.get("mime", "application/octet-stream")))
            elif a.get("text"):
                user_parts.append(types.Part(text=f"[Attached file {a.get('name')}]\n{a['text']}"))
        user_parts.append(types.Part(text=text))
        contents = self._history(cid, s.history_turns) + [types.Content(role="user", parts=user_parts)]
        tools = e.registry.declarations() + BUILTIN_DECLS

        msg_id = f"a{int(time.time() * 1000)}"
        full_text = ""
        model_used = ""
        t0 = time.perf_counter()
        seen_calls: dict[str, int] = {}
        e.bus.emit("message.start", conv_id=cid, id=msg_id, task_id=task.id)
        speech = e.voice.stream_speech() if source == "voice" and e.voice is not None else None
        try:
            for round_ in range(max_rounds):
                parts: list[types.Part] = []
                calls: list[types.FunctionCall] = []
                async for kind, val in e.brain.stream(contents, system=system, tools=tools, tier=tier,
                                                      patient=source in ("mission", "routine")):
                    if task.cancel.is_set():
                        break
                    if kind == "model":
                        model_used = val
                        e.bus.emit("message.model", conv_id=cid, id=msg_id, model=val)
                    elif kind == "part":
                        parts.append(val)
                        if val.function_call:
                            calls.append(val.function_call)
                        elif val.text and not val.thought:
                            full_text += val.text
                            e.bus.emit("message.delta", conv_id=cid, id=msg_id, delta=val.text)
                            if speech:
                                speech.push(val.text)
                if task.cancel.is_set():
                    full_text += ("\n\n" if full_text else "") + "*(stopped)*"
                    break
                if not calls:
                    if not full_text.strip() and round_ == 0 and tier == "fast":
                        tier = "smart"  # the small model came back empty — escalate once
                        continue
                    break
                contents.append(types.Content(role="model", parts=parts))
                responses = await self._run_calls(calls, task, cid, seen_calls)
                if full_text and not full_text.endswith("\n"):
                    full_text += "\n\n"
                    e.bus.emit("message.delta", conv_id=cid, id=msg_id, delta="\n\n")
                contents.append(types.Content(role="user", parts=responses))
                if task.cancel.is_set():
                    full_text += "*(stopped)*"
                    break
            else:
                full_text += "\n\nI stopped after too many steps — tell me how you'd like to continue."
        except NotConfigured as exc:
            full_text = str(exc)
            e.bus.emit("setup.required")
        except Exception as exc:
            log.exception("LLM turn failed")
            full_text = (full_text + "\n\n" if full_text else "") + f"⚠ {friendly_error(exc)}"
            e.tracker.finish(task, "failed")

        reply = full_text.strip() or "Done."
        meta = {"model": model_used, "latency_ms": round((time.perf_counter() - t0) * 1000), "task_id": task.id,
                "tier": tier}
        msg = e.store.add_message(cid, "assistant", reply, meta)
        e.bus.emit("message.end", conv_id=cid, id=msg_id, message=msg)
        if task.id in e.tracker.active:
            e.tracker.finish(task, "stopped" if task.cancel.is_set() else "done")
        if speech is not None:
            speech.finish()
            await e.voice.finish_stream(speech, reply)
        else:
            await e.after_reply(reply, source)
        return {"ok": True, "conv_id": cid, "reply": reply, "model": model_used}

    def _parallel_safe(self, name: str) -> bool:
        if name in ("update_plan", "use_skill"):
            return False  # cheap, and their order matters for the UI
        tool = self.e.registry.get(name)
        return tool is not None and not self.needs_approval(tool.risk, name) and tool.risk == "safe"

    async def _run_calls(self, calls: list[types.FunctionCall], task: Task, cid: str,
                         seen_calls: dict[str, int]) -> list[types.Part]:
        """Execute one round of function calls.

        Consecutive read-only calls (search, weather, read_file…) run concurrently —
        a briefing that needs 6 lookups takes as long as the slowest one. Anything
        that changes the PC or needs approval runs strictly in order.
        """
        results: list[tuple[bool, Any] | None] = [None] * len(calls)

        async def one(i: int, call: types.FunctionCall) -> None:
            args = dict(call.args or {})
            key = f"{call.name}:{json.dumps(args, sort_keys=True, default=str)}"
            seen_calls[key] = seen_calls.get(key, 0) + 1
            if seen_calls[key] > 2:
                results[i] = (False, "You already ran this exact call twice. Stop repeating it and answer.")
            else:
                results[i] = await self._dispatch(call.name, args, task, cid)

        i = 0
        while i < len(calls) and not task.cancel.is_set():
            j = i
            while j < len(calls) and self._parallel_safe(calls[j].name):
                j += 1
            if j - i > 1:
                await asyncio.gather(*(one(k, calls[k]) for k in range(i, j)))
                i = j
            else:
                await one(i, calls[i])
                i += 1
        parts: list[types.Part] = []
        for call, res in zip(calls, results, strict=True):
            ok, result = res if res is not None else (False, "Stopped by user before this ran.")
            part = types.Part.from_function_response(name=call.name, response={"result": result} if ok else {"error": result})
            if call.id:
                part.function_response.id = call.id
            parts.append(part)
        return parts

    async def _dispatch(self, name: str, args: dict, task: Task, cid: str) -> tuple[bool, Any]:
        e = self.e
        if name == "update_plan":
            items = [str(i) for i in args.get("items", [])]
            e.tracker.set_plan(task, items)
            done = int(args.get("done_count", 0) or 0)
            for i in range(len(task.plan)):
                e.tracker.mark_plan(task, i, "done" if i < done else ("active" if i == done else "pending"))
            return True, "Plan shown to the user."
        if name == "use_skill":
            sk = e.skills.get(str(args.get("name", "")))
            if not sk:
                return False, "No such skill. Available: " + ", ".join(s.name for s in e.skills.enabled())
            e.bus.emit("skill.used", name=sk.name, task_id=task.id)
            return True, f"SKILL {sk.name} — follow these instructions:\n{sk.body}"
        return await self.run_tool(name, args, task, cid)
