"""The Brain tab: ORION's memory as a live graph.

``build_graph`` turns the memory index (the Obsidian vault, mirrored in SQLite) into nodes and links:

* one ``core`` node for ORION itself,
* one ``note`` node per vault note (About me, Facts, Plans & dates, each person and project, This week),
* one ``memory`` node per remembered fact, linked to its note, to any person/project note it names,
  and to the one or two other memories it shares the most distinctive words with.

``ThoughtLog`` keeps ORION's latest thoughts (memories it recalled for a request, tool steps it took,
replies it gave) in a small ring buffer and publishes each one as a ``brain.thought`` event, so the
graph lights up live while ORION works. Nothing here calls a model or costs quota.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, deque
from typing import Any

MAX_MEMORIES = 600  # keeps the pairwise similarity pass well under 100 ms
MAX_THOUGHTS = 40
RELATED_PER_MEMORY = 2
MIN_SIMILARITY = 0.12

NOTE_LABELS = {
    "About me.md": "About me",
    "Facts.md": "Facts",
    "Plans & dates.md": "Plans & dates",
    "Short-term/This week.md": "This week",
}

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each every few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just like likes me more
most my myself no nor not now of off on once only or other our ours out over own same she should so some
such than that the their theirs them then there these they this those through to too under until up very
was we were what when where which while who whom why will with would you your yours lluc user orion uses
use used want wants wanted prefers prefer every week weekly day days time
""".split())

WORD = re.compile(r"[a-zA-ZÀ-ɏ][a-zA-ZÀ-ɏ'\-]{2,}")


def _words(text: str) -> set[str]:
    out = set()
    for w in WORD.findall(text.lower()):
        w = w.strip("'-")
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) >= 3 and w not in STOPWORDS:
            out.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def note_label(note: str | None) -> str:
    if not note:
        return "Facts"
    if note in NOTE_LABELS:
        return NOTE_LABELS[note]
    return note.rsplit("/", 1)[-1].removesuffix(".md")


def note_group(note: str | None) -> str:
    if not note:
        return "fact"
    if note.startswith("People/"):
        return "person"
    if note.startswith("Projects/"):
        return "project"
    if note.startswith("Short-term/"):
        return "short"
    return {"About me.md": "profile", "Plans & dates.md": "plan"}.get(note, "fact")


def short_label(text: str, limit: int = 46) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",.;:") + "…"


def build_graph(memories: list[dict]) -> dict[str, Any]:
    """Nodes and links for the Brain view. ``memories`` are rows from ``Store.list_memories``."""
    mems = sorted(memories, key=lambda m: m.get("created") or 0, reverse=True)[:MAX_MEMORIES]
    nodes: list[dict] = [{"id": "core", "kind": "core", "label": "ORION core", "group": "core"}]
    links: list[dict] = []
    seen_links: set[tuple[str, str]] = set()

    def link(a: str, b: str, kind: str, weight: float = 1.0) -> None:
        key = (a, b) if a < b else (b, a)
        if a != b and key not in seen_links:
            seen_links.add(key)
            links.append({"source": a, "target": b, "kind": kind, "weight": round(weight, 3)})

    # Notes: the regions of the brain. Each hangs off the core.
    notes: dict[str, dict] = {}
    for m in mems:
        note = m.get("note") or "Facts.md"
        if note not in notes:
            notes[note] = {"id": f"note:{note}", "kind": "note", "label": note_label(note), "group": note_group(note),
                           "note": note, "count": 0}
        notes[note]["count"] += 1
    for n in sorted(notes.values(), key=lambda n: n["label"].lower()):
        nodes.append(n)
        link("core", n["id"], "region")

    # Names of people and projects, so a fact that mentions "Sarah" also links to Sarah's note.
    names: list[tuple[re.Pattern, str]] = []
    for note, n in notes.items():
        if n["group"] in ("person", "project") and len(n["label"]) >= 3:
            first = n["label"].split()[0]
            alts = {re.escape(n["label"])} | ({re.escape(first)} if len(first) >= 3 else set())
            names.append((re.compile(r"\b(" + "|".join(sorted(alts, key=len, reverse=True)) + r")\b", re.I), n["id"]))

    words: dict[int, set[str]] = {}
    for m in mems:
        mid = f"mem:{m['id']}"
        note = m.get("note") or "Facts.md"
        nodes.append({"id": mid, "kind": "memory", "label": short_label(m["text"]), "text": m["text"],
                      "group": note_group(note), "tier": m.get("tier") or "permanent", "note": note,
                      "memory_id": m["id"], "created": m.get("created")})
        link(notes[note]["id"], mid, "note")
        for pat, nid in names:
            if nid != notes[note]["id"] and pat.search(m["text"]):
                link(mid, nid, "mention")
        words[m["id"]] = _words(m["text"])

    # Related memories: shared distinctive words, weighted by how rare each word is (IDF).
    df = Counter(w for ws in words.values() for w in ws)
    total = max(len(words), 1)
    idf = {w: math.log(1 + total / c) for w, c in df.items()}
    ids = list(words)
    best: dict[int, list[tuple[float, int]]] = {i: [] for i in ids}
    for i_pos, a in enumerate(ids):
        wa = words[a]
        if not wa:
            continue
        for b in ids[i_pos + 1:]:
            wb = words[b]
            shared = wa & wb
            if not shared:
                continue
            score = sum(idf[w] for w in shared) / (sum(idf[w] for w in wa | wb) or 1)
            if score >= MIN_SIMILARITY:
                best[a].append((score, b))
                best[b].append((score, a))
    for a, cands in best.items():
        for score, b in sorted(cands, reverse=True)[:RELATED_PER_MEMORY]:
            link(f"mem:{a}", f"mem:{b}", "related", score)

    return {"nodes": nodes, "links": links, "stats": {
        "memories": len(mems), "notes": len(notes), "links": len(links),
        "short": sum(1 for m in mems if m.get("tier") == "short"), "truncated": len(memories) > len(mems)}}


class ThoughtLog:
    """ORION's latest thoughts, newest last. Each one is also emitted live as ``brain.thought``."""

    def __init__(self, bus: Any, size: int = MAX_THOUGHTS) -> None:
        self.bus = bus
        self._items: deque[dict] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._n = 0

    def add(self, kind: str, text: str, memories: list[int] | None = None, **extra: Any) -> dict:
        text = re.sub(r"\s+", " ", text or "").strip()
        with self._lock:
            self._n += 1
            item = {"id": f"t{self._n}", "kind": kind, "text": short_label(text, 120),
                    "memories": [int(x) for x in (memories or [])][:12], "ts": time.time(), **extra}
            self._items.append(item)
        try:
            self.bus.emit("brain.thought", thought=item)
        except Exception:  # the graph is a view; it must never break a turn
            pass
        return item

    def recent(self, limit: int = MAX_THOUGHTS) -> list[dict]:
        with self._lock:
            return list(self._items)[-limit:]
