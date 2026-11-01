"""The Brain tab: memory graph and the live thought feed."""

import asyncio

from orion.memory.brain import ThoughtLog, build_graph, note_label, short_label


def mem(i, text, note="Facts.md", tier="permanent", created=None):
    return {"id": i, "text": text, "kind": "fact", "tier": tier, "note": note, "created": created or 1000 + i}


def by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def has_link(graph, a, b, kind=None):
    return any({l["source"], l["target"]} == {a, b} and (kind is None or l["kind"] == kind) for l in graph["links"])


def test_graph_nodes_notes_and_links():
    g = build_graph([
        mem(1, "Sarah is Lluc's manager at Acme", "People/Sarah Lane.md"),
        mem(2, "Lunch with Sarah on Friday at the padel club", "Short-term/This week.md", tier="short"),
        mem(3, "Plays padel every Thursday evening at the club"),
        mem(4, "Prefers dark mode", "About me.md"),
    ])
    nodes = by_id(g)
    assert nodes["core"]["kind"] == "core"
    assert nodes["note:People/Sarah Lane.md"]["label"] == "Sarah Lane"
    assert nodes["note:Short-term/This week.md"]["label"] == "This week"
    assert nodes["mem:2"]["tier"] == "short" and nodes["mem:2"]["group"] == "short"
    assert has_link(g, "core", "note:About me.md", "region")
    assert has_link(g, "note:About me.md", "mem:4", "note")
    assert has_link(g, "mem:2", "note:People/Sarah Lane.md", "mention")  # names Sarah → Sarah's note
    assert has_link(g, "mem:2", "mem:3", "related")  # shares "padel" and "club"
    assert not has_link(g, "mem:4", "mem:3")  # nothing in common
    assert g["stats"] == {"memories": 4, "notes": 4, "links": len(g["links"]), "short": 1, "truncated": False}
    ends = {l["source"] for l in g["links"]} | {l["target"] for l in g["links"]}
    assert ends <= set(nodes)  # no dangling links


def test_empty_graph_is_just_orion():
    g = build_graph([])
    assert [n["id"] for n in g["nodes"]] == ["core"] and g["links"] == []


def test_big_memory_is_capped_and_fast():
    import time

    rows = [mem(i, f"Fact number {i} about topic{i % 37} and thing{i % 11}") for i in range(800)]
    t0 = time.perf_counter()
    g = build_graph(rows)
    assert time.perf_counter() - t0 < 5
    assert g["stats"]["memories"] == 600 and g["stats"]["truncated"]
    assert "mem:799" in by_id(g) and "mem:0" not in by_id(g)  # newest kept


def test_labels():
    assert note_label("Plans & dates.md") == "Plans & dates"
    assert note_label("Projects/ORION.md") == "ORION"
    assert short_label("a " * 60).endswith("…")
    assert short_label("short one") == "short one"


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, type_, **data):
        self.events.append((type_, data))


def test_thought_log_ring_and_events():
    bus = Bus()
    log = ThoughtLog(bus, size=3)
    for i in range(5):
        log.add("action", f"step {i}", [i])
    assert [t["text"] for t in log.recent()] == ["step 2", "step 3", "step 4"]
    assert bus.events[-1][0] == "brain.thought" and bus.events[-1][1]["thought"]["memories"] == [4]


async def test_recall_and_reply_reach_the_thought_feed(engine):
    engine.vault.ensure()
    engine.vault.add("Sarah is Lluc's manager at Acme", "person", subject="Sarah Lane")
    q = engine.bus.subscribe()
    engine.agent.system_prompt("what does Sarah do?")
    thought = engine.thoughts.recent()[-1]
    assert thought["kind"] == "recall" and "Sarah" in thought["text"]
    sarah = next(m for m in engine.store.list_memories() if "Sarah" in m["text"])
    assert sarah["id"] in thought["memories"]
    await asyncio.sleep(0)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e["type"] == "brain.thought" for e in events)


async def test_brain_graph_api(engine):
    from fastapi.testclient import TestClient

    from orion.server.api import create_app

    engine.vault.ensure()
    engine.vault.add("Prefers dark mode and 24-hour time", "preference")
    engine.thoughts.add("action", "Opened Notepad")
    c = TestClient(create_app(engine, token="secret-token"))
    r = c.get("/api/brain/graph", headers={"x-orion-token": "secret-token"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(n["kind"] == "memory" and "dark mode" in n["text"] for n in body["nodes"])
    assert body["thoughts"][-1]["text"] == "Opened Notepad"
