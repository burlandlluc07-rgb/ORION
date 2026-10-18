import asyncio

import pytest
from google.genai import types

from orion.tasks import tracker
from orion.tasks.tracker import parse_approval_reply, parse_yes_no

from .test_agent import FakeBrain, add_probe, call


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


# ── Answering an approval with a typed or spoken yes/no (any task, desktop or phone) ──────────────────

@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yes.", True), ("yes please", True), ("ok go ahead", True), ("sure, send it", True),
    ("no", False), ("No thanks", False), ("nope", False), ("don't send it", False), ("cancel", False),
    ("yes but send it to Ana instead", None), ("yes, change the text first", None), ("hmm", None),
    ("yes no", None), ("", None), ("what time is it in Tokyo right now please", None),
])
def test_parse_approval_reply_is_strict(text, expected):
    assert parse_approval_reply(text) is expected


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(tracker, "REPLY_MIN_AGE", 0)


async def _pending(engine, task, label="Send WhatsApp to Ana", tool="probe"):
    fut = asyncio.create_task(engine.approvals.ask(tool, label, "confirm", {}, task))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(i["label"] == label for i in engine.approvals.list()):
            return fut
    raise AssertionError("approval never appeared")


async def test_typed_yes_approves_the_waiting_step_without_the_model(engine, no_wait):
    engine.brain = FakeBrain([])  # any model call would fail: the answer must be handled locally
    task = engine.tracker.start("send", None, "text")
    fut = await _pending(engine, task)
    res = await engine.agent.handle("yes", source="phone")
    assert res["approval"]["approved"] is True and res["reply"] == "Okay, going ahead."
    assert await fut == "approve"  # this one step only, never "always"
    assert "probe" not in engine.approvals.session_allow
    assert engine.brain.calls == []


async def test_typed_no_denies(engine, no_wait):
    engine.brain = FakeBrain([])
    fut = await _pending(engine, engine.tracker.start("send", None, "text"))
    res = await engine.agent.handle("no thanks")
    assert res["reply"] == "Okay, I won't do that."
    assert await fut == "deny"


async def test_unclear_reply_leaves_the_approval_pending(engine, no_wait):
    engine.brain = FakeBrain([[types.Part(text="Sure, what should it say?")]])
    fut = await _pending(engine, engine.tracker.start("send", None, "text"))
    res = await engine.agent.handle("yes but send it to Ana instead")
    assert "approval" not in res and res["reply"] == "Sure, what should it say?"
    assert not fut.done() and len(engine.approvals.list()) == 1
    engine.approvals.deny_all()
    await fut


async def test_several_waiting_means_no_answer(engine, no_wait):
    engine.brain = FakeBrain([])
    a = await _pending(engine, engine.tracker.start("one", None, "text"), "First")
    b = await _pending(engine, engine.tracker.start("two", None, "text"), "Second")
    res = await engine.agent.handle("yes")
    assert "2 actions are waiting" in res["reply"]
    assert not a.done() and not b.done()
    engine.approvals.deny_all()
    await asyncio.gather(a, b)


async def test_several_waiting_but_one_in_this_conversation(engine, no_wait):
    conv = engine.store.create_conversation("chat")
    other = engine.store.create_conversation("other")
    a = await _pending(engine, engine.tracker.start("one", other["id"], "text"), "First")
    b = await _pending(engine, engine.tracker.start("two", conv["id"], "text"), "Second")
    answered = engine.approvals.answer_from_text("yes", conv["id"], engine.tracker.active)
    assert answered["label"] == "Second"
    assert await b == "approve" and not a.done()
    engine.approvals.deny_all()
    await a


async def test_a_reply_never_lands_on_a_brand_new_prompt(engine):
    fut = await _pending(engine, engine.tracker.start("send", None, "text"))
    assert engine.approvals.answer_from_text("yes") is None  # popped up less than a second ago
    assert not fut.done()
    engine.approvals.deny_all()
    await fut


async def test_setting_off_turns_typed_answers_off(engine, no_wait):
    engine.config.settings.voice_approvals = False
    engine.brain = FakeBrain([[types.Part(text="Yes to what?")]])
    fut = await _pending(engine, engine.tracker.start("send", None, "text"))
    res = await engine.agent.handle("yes")
    assert res["reply"] == "Yes to what?" and not fut.done()
    engine.approvals.deny_all()
    await fut


async def test_open_voice_session_asks_out_loud_for_any_task(engine):
    asked = []

    async def hook(info):
        asked.append(info)
        engine.approvals.respond(info["id"], "approve")

    engine.approvals.on_voice_request = hook
    engine.approvals.voice_active = lambda: True
    task = engine.tracker.start("typed", None, "text")
    assert await asyncio.wait_for(engine.approvals.request("close_app", "Close X", "confirm", {}, task), 2) is True
    assert asked and asked[0]["source"] == "text"


async def test_yes_answers_a_real_tool_prompt(engine, prompts_on, no_wait):
    seen = add_probe(engine, risk="confirm")
    engine.brain = FakeBrain([[call("probe", x="go")], [types.Part(text="Sent.")]])
    run = asyncio.create_task(engine.agent.handle("probe go"))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if engine.approvals.list():
            break
    res = await engine.agent.handle("yes")
    assert res["approval"]["approved"] is True
    assert (await asyncio.wait_for(run, 5))["reply"] == "Sent."
    assert seen == ["go"]
