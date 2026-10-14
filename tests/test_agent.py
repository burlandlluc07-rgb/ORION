"""Agent loop tests with a scripted fake Gemini (no network)."""

import asyncio

from google.genai import types

from orion.tools.registry import Tool


class FakeBrain:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []

    async def stream(self, contents, *, system=None, tools=None, tier="fast", patient=False):
        self.calls.append({"contents": list(contents), "system": system, "tools": tools, "tier": tier})
        yield "model", "fake-model"
        parts = self.rounds.pop(0) if self.rounds else [types.Part(text="(no more script)")]
        for p in parts:
            await asyncio.sleep(0)
            yield "part", p
        yield "usage", 0


def call(fn_name, **args):
    return types.Part(function_call=types.FunctionCall(name=fn_name, args=args, id=f"id-{fn_name}"))


def add_probe(engine, risk="safe"):
    seen = []

    def probe(x: str) -> str:
        """Probe tool.

        Args:
            x: value
        """
        seen.append(x)
        return f"probed {x}"

    from orion.tools.registry import schema_for

    schema, _, desc = schema_for(probe)
    engine.registry.add(Tool(name="probe", description=desc, parameters=schema, fn=probe, risk=risk))
    return seen


async def test_plain_answer_streams_and_saves(engine):
    engine.brain = FakeBrain([[types.Part(text="Hello "), types.Part(text="there.")]])
    events = engine.bus.subscribe()
    res = await engine.agent.handle("tell me a joke about cats please")
    assert res["reply"] == "Hello there."
    msgs = engine.store.messages(res["conv_id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    types_seen = []
    while not events.empty():
        types_seen.append(events.get_nowait()["type"])
    assert "message.delta" in types_seen and "message.end" in types_seen
    # system prompt carries persona + skills index
    assert "SKILLS" in engine.brain.calls[0]["system"]


async def test_tool_call_round_trip(engine):
    seen = add_probe(engine)
    engine.brain = FakeBrain([
        [call("update_plan", items=["Probe", "Answer"]), call("probe", x="42")],
        [types.Part(text="The probe said 42.")],
    ])
    res = await engine.agent.handle("please run the probe with 42 and explain")
    assert seen == ["42"]
    assert res["reply"] == "The probe said 42."
    second = engine.brain.calls[1]["contents"]
    responses = [p.function_response for p in second[-1].parts]
    assert [r.name for r in responses] == ["update_plan", "probe"]
    assert responses[1].response == {"result": "probed 42"}
    assert responses[1].id == "id-probe"
    task = engine.store.list_tasks()[0]
    assert [p["text"] for p in task["plan"]] == ["Probe", "Answer"]
    assert task["steps"][0]["status"] == "done"


async def test_risky_tool_waits_for_approval(engine):
    seen = add_probe(engine, risk="confirm")
    engine.brain = FakeBrain([[call("probe", x="go")], [types.Part(text="ok")]])
    events = engine.bus.subscribe()

    async def approver():
        while True:
            ev = await events.get()
            if ev["type"] == "approval.request":
                engine.approvals.respond(ev["approval"]["id"], "approve")
                return ev["approval"]

    appr = asyncio.create_task(approver())
    res = await asyncio.wait_for(engine.agent.handle("probe go"), 5)
    assert (await appr)["tool"] == "probe"
    assert seen == ["go"] and res["reply"] == "ok"


async def test_denied_tool_is_not_run(engine):
    seen = add_probe(engine, risk="danger")
    engine.brain = FakeBrain([[call("probe", x="nope")], [types.Part(text="Understood.")]])
    events = engine.bus.subscribe()

    async def denier():
        while True:
            ev = await events.get()
            if ev["type"] == "approval.request":
                engine.approvals.respond(ev["approval"]["id"], "deny")
                return

    asyncio.create_task(denier())
    await asyncio.wait_for(engine.agent.handle("probe nope"), 5)
    assert seen == []
    resp = engine.brain.calls[1]["contents"][-1].parts[0].function_response.response
    assert "declined" in resp["error"]


async def test_trust_mode_skips_approval(engine):
    engine.config.settings.approval_mode = "trust"
    seen = add_probe(engine, risk="danger")
    engine.brain = FakeBrain([[call("probe", x="t")], [types.Part(text="done")]])
    await asyncio.wait_for(engine.agent.handle("probe t"), 5)
    assert seen == ["t"]


async def test_unknown_tool_and_repeat_guard(engine):
    add_probe(engine)
    engine.brain = FakeBrain([
        [call("does_not_exist")],
        [call("probe", x="1")], [call("probe", x="1")], [call("probe", x="1")],
        [types.Part(text="giving up")],
    ])
    res = await engine.agent.handle("loop forever please")
    first = engine.brain.calls[1]["contents"][-1].parts[0].function_response.response
    assert "Unknown" in first["error"]
    third = engine.brain.calls[4]["contents"][-1].parts[0].function_response.response
    assert "already ran" in third["error"]
    assert res["reply"] == "giving up"


async def test_stop_cancels_turn(engine):
    async def slow(x: str) -> str:
        """Slow tool.

        Args:
            x: value
        """
        await asyncio.sleep(0.3)
        return "slow done"

    from orion.tools.registry import schema_for

    schema, _, desc = schema_for(slow)
    engine.registry.add(Tool(name="slow", description=desc, parameters=schema, fn=slow))
    engine.brain = FakeBrain([[call("slow", x="a")], [types.Part(text="should not be reached")]])
    turn = asyncio.create_task(engine.agent.handle("please execute the slow thing"))
    await asyncio.sleep(0.1)
    engine.stop_everything()
    res = await asyncio.wait_for(turn, 5)
    assert "stopped" in res["reply"].lower()
    assert len(engine.brain.calls) == 1


async def test_fast_path_uses_no_model(engine):
    engine.brain = FakeBrain([])
    res = await engine.agent.handle("what time is it")
    assert res["model"] == "local" and res["reply"].startswith("It's ")
    assert engine.brain.calls == []


async def test_skill_slash_command_injects_playbook(engine):
    engine.brain = FakeBrain([[types.Part(text="Briefing…")]])
    await engine.agent.handle("/morning-briefing")
    call0 = engine.brain.calls[0]
    assert "ACTIVE SKILL" in call0["system"] and "get_weather" in call0["system"]
    assert call0["contents"][-1].parts[-1].text.startswith("Run the morning-briefing skill")


async def test_memory_is_injected(engine):
    engine.store.remember("User's dog is named Rex")
    engine.brain = FakeBrain([[types.Part(text="Rex!")]])
    await engine.agent.handle("what is my dog called again?")
    assert "Rex" in engine.brain.calls[0]["system"]


async def test_reminder_tool_and_scheduler(engine):
    from orion.tools.registry import ToolContext

    t = engine.registry.get("set_reminder")
    out = await t.run({"message": "stretch", "when": "in 2 minutes"}, ToolContext(engine=engine))
    assert "Reminder set" in out
    items = engine.store.list_routines()
    assert items and items[0]["prompt"] == "!notify:stretch"


async def test_safe_calls_run_in_parallel_and_keep_order(engine):
    import time

    async def slow_read(x: str) -> str:
        """Slow read-only lookup.

        Args:
            x: value
        """
        await asyncio.sleep(0.3)
        return f"read {x}"

    from orion.tools.registry import schema_for

    schema, _, desc = schema_for(slow_read)
    engine.registry.add(Tool(name="slow_read", description=desc, parameters=schema, fn=slow_read, risk="safe"))
    engine.brain = FakeBrain([[call("slow_read", x="a"), call("slow_read", x="b"), call("slow_read", x="c")],
                              [types.Part(text="done")]])
    t0 = time.perf_counter()
    await engine.agent.handle("look up three things in parallel please")
    assert time.perf_counter() - t0 < 0.8  # 3 × 0.3 s would be ~0.9 s sequentially
    responses = [p.function_response.response["result"] for p in engine.brain.calls[1]["contents"][-1].parts]
    assert responses == ["read a", "read b", "read c"]


async def test_history_includes_compact_action_log(engine):
    add_probe(engine)
    engine.brain = FakeBrain([[call("probe", x="report.txt")], [types.Part(text="Made it.")], [types.Part(text="Undone.")]])
    res = await engine.agent.handle("make the report file please now")
    await engine.agent.handle("now undo that", res["conv_id"])
    history = engine.brain.calls[2]["contents"]
    model_turn = next(c for c in history if c.role == "model")
    joined = " ".join(p.text for p in model_turn.parts)
    assert "[Actions I took: Probe: “report.txt” → done (probed report.txt)]" in joined


async def test_fast_path_hardware_error_is_reported_not_sent_to_gemini(engine, monkeypatch):
    from orion.tools import system
    from orion.tools.registry import ToolError

    def no_speakers(level: int) -> str:
        raise ToolError("I can't reach the speakers — no audio output device seems to be connected or enabled.")

    engine.registry.get("set_volume").fn = no_speakers
    engine.brain = FakeBrain([])
    res = await engine.agent.handle("volume 30")
    assert "can't reach the speakers" in res["reply"] and engine.brain.calls == []
    _ = system


def add_kit_tool(engine, name="make_sheet", kit="documents"):
    def fn() -> str:
        """Kit tool."""
        return "made"

    from orion.tools.registry import schema_for

    schema, _, desc = schema_for(fn)
    engine.registry.add(Tool(name=name, description=desc, parameters=schema, fn=fn, kit=kit))


def _names(decls):
    return {d["name"] for d in decls}


async def test_toolkits_load_by_keyword_and_on_demand(engine):
    add_kit_tool(engine)
    engine.brain = FakeBrain([[types.Part(text="Sure.")]])
    res = await engine.agent.handle("what's a good name for a cat?")
    first = _names(engine.brain.calls[0]["tools"])
    assert "make_sheet" not in first and "open_toolkit" in first

    # The model asks for the toolkit mid-task; the next round offers its tools.
    engine.brain = FakeBrain([[call("open_toolkit", name="documents")], [call("make_sheet")], [types.Part(text="Done.")]])
    await engine.agent.handle("now organise that for me", res["conv_id"])
    assert "make_sheet" not in _names(engine.brain.calls[0]["tools"])
    assert "make_sheet" in _names(engine.brain.calls[1]["tools"])
    assert engine.store.list_tasks()[0]["steps"][-1]["status"] == "done"

    # Mentioning the subject preloads it in a new conversation.
    engine.brain = FakeBrain([[types.Part(text="Ok.")]])
    await engine.agent.handle("make me an Excel spreadsheet of my budget")
    assert "make_sheet" in _names(engine.brain.calls[0]["tools"])


def test_select_kits():
    from orion.tools.kits import select_kits

    assert select_kits("reply to the last email from Sam") == {"email"}
    assert "documents" in select_kits("turn this into a PowerPoint deck")
    assert "browser" in select_kits("log in to my bank website and download the statement")
    assert "windows" in select_kits("turn on dark mode and connect to wifi")
    assert select_kits("what time is it") == set()


async def test_empty_turn_mid_task_is_nudged_not_called_done(engine):
    add_probe(engine)
    engine.brain = FakeBrain([[call("probe", x="1")], [], [types.Part(text="Finished: probed 1.")]])
    res = await engine.agent.handle("probe one please and report back to me", mode="smart")
    assert res["reply"] == "Finished: probed 1."
    assert "empty" in engine.brain.calls[2]["contents"][-1].parts[-1].text

    engine.brain = FakeBrain([[call("probe", x="2")], [], [], []])
    res = await engine.agent.handle("probe two please and report back to me", mode="smart")
    assert res["reply"].startswith("⚠") and "incomplete" in res["reply"]  # never a fake "Done."
