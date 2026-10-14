import asyncio

import pytest

from orion.tasks.tracker import parse_yes_no


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yeah go ahead", True), ("sure, do it", True), ("okay", True), ("approve", True),
    ("no", False), ("nope", False), ("don't do that", False), ("cancel it", False), ("wait", False),
    ("", None), ("hmm", None), ("yes no", None), ("what was that", None),
])
def test_parse_yes_no(text, expected):
    assert parse_yes_no(text) is expected


async def test_voice_tasks_trigger_spoken_prompt(engine):
    asked = []

    async def hook(info):
        asked.append(info)
        engine.approvals.respond(info["id"], "approve")

    engine.approvals.on_voice_request = hook
    text_task = engine.tracker.start("typed", None, "text")
    voice_task = engine.tracker.start("spoken", None, "voice")
    # Typed task: nobody answers by voice, so answer from "the UI".
    pending = asyncio.create_task(engine.approvals.request("close_app", "Close X", "confirm", {}, text_task))
    await asyncio.sleep(0.05)
    assert asked == []
    engine.approvals.respond(engine.approvals.list()[0]["id"], "deny")
    assert await pending is False
    assert await asyncio.wait_for(engine.approvals.request("close_app", "Close X", "confirm", {}, voice_task), 2) is True
    assert asked and asked[0]["label"] == "Close X"
