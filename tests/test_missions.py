import asyncio

from google.genai import types

from tests.test_agent import FakeBrain, call


async def test_mission_runs_in_background_with_mission_prompt_and_notifies(engine):
    engine.brain = FakeBrain([
        [call("update_plan", items=["Search", "Write report"])],
        [types.Part(text="- Found 3 laptops\n- Report saved")],
    ])
    events = engine.bus.subscribe()
    m = engine.missions.start("Compare three budget laptops and write a report")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if engine.missions.list()[0]["status"] == "done":
            break
    done = engine.missions.list()[0]
    assert done["status"] == "done" and done["summary"].startswith("- Found 3 laptops") and done["task_id"]
    call0 = engine.brain.calls[0]
    assert "MISSION MODE" in call0["system"] and call0["tier"] == "smart"
    titles = [e["title"] for e in [events.get_nowait() for _ in range(events.qsize())] if e["type"] == "notify"]
    assert any(t.startswith("Mission complete") for t in titles)
    assert m["conv_id"] == done["conv_id"]


async def test_missions_queue_beyond_parallel_limit_and_cancel(engine):
    release = asyncio.Event()

    class SlowBrain(FakeBrain):
        async def stream(self, *a, **k):
            await release.wait()
            async for x in super().stream(*a, **k):
                yield x

    engine.brain = SlowBrain([[types.Part(text="ok")]] * 3)
    ids = [engine.missions.start(f"Long research job number {i}")["id"] for i in range(3)]
    await asyncio.sleep(0.05)
    status = {m["id"]: m["status"] for m in engine.missions.list()}
    assert sorted(status.values()) == ["queued", "running", "running"]
    queued = next(i for i in ids if status[i] == "queued")
    assert engine.missions.cancel(queued)
    assert {m["id"]: m["status"] for m in engine.missions.list()}[queued] == "stopped"
    release.set()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if all(m["status"] != "running" for m in engine.missions.list()):
            break


async def test_start_mission_tool(engine):
    from orion.tools.registry import ToolContext

    engine.brain = FakeBrain([[types.Part(text="done")]])
    out = await engine.registry.get("start_mission").run({"goal": "Research quiet mechanical keyboards"}, ToolContext(engine=engine))
    assert "Mission started" in out and engine.missions.list()[0]["goal"] == "Research quiet mechanical keyboards"
    await asyncio.sleep(0.1)
