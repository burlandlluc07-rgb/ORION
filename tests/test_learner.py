import json
import time

from orion.memory.learner import is_duplicate


async def test_learns_new_facts_from_idle_conversations_and_dedupes(engine, monkeypatch):
    engine.store.remember("User's name is Lluc", "profile")
    c = engine.store.create_conversation("Chat")
    engine.store.add_message(c["id"], "user", "My sister Maya is visiting next week and I'm building ORION, my JARVIS app")
    engine.store.add_message(c["id"], "assistant", "Sounds great!")
    prompts = []

    async def fake_generate(prompt, **kw):
        prompts.append(prompt)
        return json.dumps({"facts": [
            {"text": "User's sister is named Maya", "kind": "person"},
            {"text": "User is building ORION, a JARVIS-style desktop assistant", "kind": "project"},
            {"text": "User's name is Lluc.", "kind": "profile"},  # duplicate of a known fact
            {"text": "x", "kind": "fact"},  # too short
        ]})

    monkeypatch.setattr(engine.brain, "generate_text", fake_generate)
    assert await engine.learner.scan(now=time.time()) == 0  # not idle yet
    assert await engine.learner.scan(now=time.time() + 600) == 2
    kinds = {m["text"]: m["kind"] for m in engine.store.list_memories()}
    assert kinds["User's sister is named Maya"] == "person" and "User's name is Lluc" in prompts[0]
    assert await engine.learner.scan(now=time.time() + 1200) == 0  # nothing new since
    assert len(prompts) == 1


def test_duplicate_detection():
    assert is_duplicate("User likes jazz music.", ["User likes jazz music"])
    assert not is_duplicate("User's dog is called Rex", ["User likes jazz music"])
