"""Obsidian vault memory: notes are the source of truth, SQLite is the index, short-term is reviewed weekly."""

import datetime as dt
import json
import time

import pytest


@pytest.fixture
def vault(engine):
    engine.vault.ensure()
    return engine.vault


def read(vault, rel):
    return (vault.root / rel).read_text("utf-8")


def test_layout_and_permanent_notes(vault, engine):
    root = vault.root
    for rel in ("Home.md", "About me.md", "Facts.md", "Plans & dates.md", "Short-term/This week.md"):
        assert (root / rel).exists(), rel
    vault.add("Lluc's name is Lluc and he lives in Barcelona", "profile")
    vault.add("Prefers dark mode and 24-hour time", "preference")
    vault.add("Sarah is Lluc's manager at Acme", "person", subject="Sarah Lane")
    vault.add("ORION is a JARVIS-style assistant Lluc is building", "project", subject="ORION")
    about = read(vault, "About me.md")
    assert about.index("## Profile") < about.index("lives in Barcelona") < about.index("## Preferences") < about.index("dark mode")
    assert read(vault, "People/Sarah Lane.md").startswith("---\ntype: person")
    assert "[[People/Sarah Lane|Sarah Lane]]" in read(vault, "Home.md")  # linked for Obsidian's graph
    mems = {m["text"]: m for m in engine.store.list_memories()}
    assert mems["Sarah is Lluc's manager at Acme"]["kind"] == "person"
    assert {m["text"] for m in engine.store.core_memories()} >= {"Prefers dark mode and 24-hour time"}
    assert engine.store.recall("who is Sarah")[0]["note"] == "People/Sarah Lane.md"


def test_duplicates_promotion_and_forget(vault, engine):
    a = vault.add("Parents visiting on Saturday", "plan", tier="short")
    assert a["tier"] == "short" and "[" in read(vault, "Short-term/This week.md")
    assert vault.add("Parents visiting on Saturday.", "plan", tier="short")["id"] == a["id"]  # no duplicate
    vault.add("Parents visiting on Saturday", "plan")  # made permanent → leaves short-term
    assert "Parents visiting" not in read(vault, "Short-term/This week.md")
    m = next(m for m in engine.store.list_memories() if m["note"] == "Plans & dates.md")
    assert vault.remove(m["id"]) and "Parents visiting" not in read(vault, "Plans & dates.md")
    assert not any("Parents" in x["text"] for x in engine.store.list_memories())


def test_edits_in_obsidian_are_picked_up(vault, engine):
    (vault.root / "People" / "Tom.md").write_text("---\ntype: person\n---\n# Tom\n\n- Plays padel on Thursdays\n"
                                                  "- [[Home]]\n", "utf-8")
    (vault.root / "Recipes.md").write_text("# Recipes\n\nPaella needs bomba rice.\n", "utf-8")
    vault.sync(force=True)
    texts = {m["text"] for m in engine.store.list_memories()}
    assert "Tom: Plays padel on Thursdays" in texts and "Recipes: Paella needs bomba rice." in texts
    assert not any("Home" in t for t in texts)  # bare links aren't memories
    tom = next(m for m in engine.store.list_memories() if m["text"].startswith("Tom:"))
    (vault.root / "People" / "Tom.md").write_text("# Tom\n\n- Plays padel on Thursdays\n- Has a dog\n", "utf-8")
    vault.sync(force=True)
    same = next(m for m in engine.store.list_memories() if m["text"].startswith("Tom: Plays"))
    assert same["id"] == tom["id"]  # unchanged lines keep their id
    vault.remove(tom["id"])
    assert "padel" not in read(vault, "People/Tom.md") and "Has a dog" in read(vault, "People/Tom.md")


def test_migrates_old_sqlite_memories(engine):
    engine.store.remember("User's name is Lluc", "profile")
    engine.store.remember("User likes jazz", "fact")
    assert engine.vault.migrate() == 2
    assert "User's name is Lluc" in read(engine.vault, "About me.md") and "User likes jazz" in read(engine.vault, "Facts.md")
    texts = [m["text"] for m in engine.store.list_memories()]
    assert texts.count("User likes jazz") == 1 and all(m["key"] for m in engine.store.list_memories())
    assert engine.vault.migrate() == 0


async def test_weekly_review_keeps_carries_and_deletes(vault, engine, monkeypatch):
    vault.add("Sister Maya got a job at Google", "person", tier="short")
    vault.add("Dentist appointment next Tuesday", "plan", tier="short")
    vault.add("Ordered pizza tonight", "fact", tier="short")
    vault.add("Something the model forgets to judge", "fact", tier="short")
    prompts = []

    async def fake(prompt, **kw):
        prompts.append(prompt)
        return json.dumps({"decisions": [
            {"n": 1, "action": "keep", "text": "Lluc's sister Maya works at Google", "kind": "person", "subject": "Maya"},
            {"n": 2, "action": "carry"},
            {"n": 3, "action": "discard"},
        ]})

    monkeypatch.setattr(engine.brain, "generate_text", fake)
    res = await vault.review()
    assert res["kept"] == ["Lluc's sister Maya works at Google"] and res["discarded"] == ["Ordered pizza tonight"]
    short = read(vault, "Short-term/This week.md")
    assert "Dentist" in short and "forgets to judge" in short and "pizza" not in short and "Maya" not in short
    assert "works at Google" in read(vault, "People/Maya.md")
    log = (vault.root / "ORION" / "Review log.md").read_text("utf-8")
    assert "Kept: Lluc's sister Maya works at Google" in log and "Deleted: Ordered pizza tonight" in log
    assert not any("Review log" in (m["note"] or "") for m in engine.store.list_memories())  # logs aren't memories


async def test_review_failure_deletes_nothing(vault, engine, monkeypatch):
    vault.add("Keep me safe", "fact", tier="short")

    async def boom(*a, **k):
        raise RuntimeError("quota")

    monkeypatch.setattr(engine.brain, "generate_text", boom)
    with pytest.raises(RuntimeError, match="nothing was deleted"):
        await vault.review()
    assert "Keep me safe" in read(vault, "Short-term/This week.md")


def test_review_schedule(vault, engine):
    engine.config.settings.memory_review_day, engine.config.settings.memory_review_hour = 6, 20
    sunday_night = dt.datetime(2026, 10, 18, 21, 0)
    assert vault.last_slot(sunday_night) == dt.datetime(2026, 10, 18, 20, 0)
    assert vault.last_slot(dt.datetime(2026, 10, 18, 19, 0)) == dt.datetime(2026, 10, 11, 20, 0)
    assert vault.review_due() is False  # first run only starts the clock
    engine.store.kv_set("vault_last_review", time.time() - 8 * 86400)
    assert vault.review_due() is True


def test_points_into_existing_obsidian_vault(engine, tmp_path):
    mine = tmp_path / "My Vault"
    (mine / ".obsidian").mkdir(parents=True)
    engine.config.settings.vault_path = str(mine)
    assert engine.vault.root == mine / "ORION Brain"


def test_remember_tool_routes_tiers(engine):
    from orion.tools.productivity import recall, remember
    from orion.tools.registry import ToolContext

    ctx = ToolContext(engine=engine)
    assert "People/Ana.md" in remember("Ana is Lluc's cousin", kind="person", subject="Ana", ctx=ctx)
    assert "short-term" in remember("Ana arrives Friday", kind="plan", permanent=False, ctx=ctx)
    hits = recall("Ana", ctx=ctx)
    assert {h["tier"] for h in hits} == {"permanent", "short"}
