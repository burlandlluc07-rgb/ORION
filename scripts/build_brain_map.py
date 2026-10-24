"""Build the map behind Memory → View brain (orion/assets/brain_map.json).

graphify (https://github.com/Graphify-Labs/graphify) parses ORION's own source with tree-sitter, offline and
without any AI model, into a graph of files, classes and functions and how they call and import each other.
This script keeps ORION's code (the engine under orion/ and the interface under ui/src), sorts it into brain
regions by folder, lays it out once with a force layout and writes a compact JSON the app draws live.

    pip install -e ".[brainmap]"
    python scripts/build_brain_map.py

Run it after code changes; BUILD.cmd does. The app keeps working with the map it shipped with if you don't.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "orion" / "assets" / "brain_map.json"

# (folder prefix, key, name, what it does). The first match wins, so files directly in orion/ come last.
REGIONS = [
    ("orion/brain/", "reasoning", "Reasoning", "Thinks with Gemini, Gemma or the backup brain and keeps within the free quota."),
    ("orion/voice/", "voice", "Hearing & voice", "Listens for the wake word, hears you and speaks."),
    ("orion/memory/", "memory", "Memory", "The Obsidian vault of notes about you, and what ORION learns from chats."),
    ("orion/tools/", "hands", "Hands", "The tools: apps, files, browser, Windows settings, documents, the screen."),
    ("orion/ecosystem/", "connections", "Connections", "Email, calendar, WhatsApp, smart home, connectors and plugins."),
    ("orion/tasks/", "planning", "Planning", "Tasks, approvals, missions, routines and checking that work really got done."),
    ("orion/background/", "background", "Background agent", "Queued chores that run off-screen in their own folders."),
    ("orion/server/", "links", "Links", "The local API the window talks to, and the phone app."),
    ("orion/desktop/", "desktop", "Desktop shell", "The window, tray icon, title bar and hotkeys."),
    ("orion/skills/", "skills", "Skills", "Packaged playbooks like the morning briefing."),
    ("ui/src/", "interface", "Interface", "What you see: chat, tasks, settings and this map."),
    ("orion/", "core", "Core", "The engine that wires everything together: settings, events, start-up."),
]
SKIP = ("orion/web/",)  # the built copy of ui/src


def relative(path: str) -> str:
    """The repo-relative path with forward slashes, whatever the OS wrote."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(ROOT)
        except ValueError:
            return ""
    return p.as_posix().replace("\\", "/")


def region_of(path: str) -> int | None:
    if not path or path.startswith(SKIP):
        return None
    for i, (prefix, *_rest) in enumerate(REGIONS):
        if path.startswith(prefix):
            return i
    return None


def kind_of(node: dict) -> str:
    """F file, c class, m method, f function, s anything else (a type, a constant)."""
    label = node["label"]
    if label in (Path(node["source_file"]).name, node["source_file"]):
        return "F"
    if node.get("_callable_class"):
        return "c"
    if label.startswith("."):
        return "m"
    if label.endswith("()"):
        return "f"
    return "s"


def extract(work: Path) -> dict:
    """Run graphify's code-only pass (local tree-sitter, no API key) and return its graph.json."""
    env = {**os.environ, "GRAPHIFY_BACKEND": "none"}
    cmd = [sys.executable, "-m", "graphify", "extract", str(ROOT), "--code-only", "--no-cluster", "--out", str(work)]
    subprocess.run(cmd, check=True, env=env)
    return json.loads((work / "graphify-out" / "graph.json").read_text(encoding="utf-8"))


# Where each region sits, like lobes of a brain seen from the side (front to the right): reasoning and planning in
# front, the interface at the back where vision is, the background agent as the cerebellum and the desktop shell
# as the brain stem. Same order as REGIONS.
LOBES = {
    "reasoning": (0.62, -0.08), "voice": (0.40, 0.42), "memory": (0.02, 0.30), "hands": (-0.05, -0.52),
    "connections": (-0.55, -0.34), "planning": (0.40, -0.45), "background": (-0.42, 0.66), "links": (-0.80, 0.04),
    "desktop": (0.08, 0.86), "skills": (0.78, 0.26), "interface": (-0.62, 0.36), "core": (0.05, -0.06),
}


def layout(n: int, regions: list[int], edges: list[tuple[int, int]]) -> list[tuple[float, float]]:
    """Force layout around a fixed hub per region, so every region forms its own lobe of one brain."""
    import random

    import networkx as nx

    rnd = random.Random(7)
    g = nx.Graph()
    pos: dict = {}
    hubs = []
    for r, (_prefix, key, *_rest) in enumerate(REGIONS):
        hx, hy = LOBES[key]
        hub = ("hub", r)
        g.add_node(hub)
        pos[hub] = (hx, hy)
        hubs.append(hub)
    for i in range(n):
        hx, hy = pos[("hub", regions[i])]
        g.add_edge(("hub", regions[i]), i, weight=0.3)
        pos[i] = (hx + rnd.uniform(-0.05, 0.05), hy + rnd.uniform(-0.05, 0.05))
    for a, b in edges:
        g.add_edge(a, b, weight=0.5 if regions[a] == regions[b] else 0.02)
    pos = nx.spring_layout(g, pos=pos, fixed=hubs, weight="weight", k=0.05, iterations=300, seed=7)
    return [(pos[i][0] * 1000, pos[i][1] * 1000) for i in range(n)]


def build(graph: dict) -> dict:
    keep: dict[str, int] = {}
    nodes: list[dict] = []
    for node in graph["nodes"]:
        path = relative(node.get("source_file") or "")
        r = region_of(path)
        if r is None or node.get("file_type") != "code":
            continue
        keep[node["id"]] = len(nodes)
        nodes.append({**node, "source_file": path, "region": r})

    pairs: set[tuple[int, int]] = set()
    for e in graph.get("links") or graph.get("edges") or []:
        a, b = keep.get(e["source"]), keep.get(e["target"])
        if a is not None and b is not None and a != b:
            pairs.add((min(a, b), max(a, b)))
    edges = sorted(pairs)

    pos = layout(len(nodes), [n["region"] for n in nodes], edges)
    files = sorted({n["source_file"] for n in nodes})
    file_ix = {f: i for i, f in enumerate(files)}
    rows = []
    for node, (x, y) in zip(nodes, pos):
        line = int((node.get("source_location") or "L0").lstrip("L") or 0)
        rows.append([node["label"], file_ix[node["source_file"]], line, node["region"], kind_of(node),
                     round(x), round(y)])

    from orion import __version__

    counts = Counter(n["region"] for n in nodes)
    return {
        "format": 1,
        "orion_version": __version__,
        "regions": [{"key": k, "name": name, "about": about, "nodes": counts.get(i, 0)}
                    for i, (_p, k, name, about) in enumerate(REGIONS)],
        "files": files,
        # [label, file index, line, region index, kind (see kind_of), x, y]
        "nodes": rows,
        "edges": edges,
    }


def main() -> None:
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory() as tmp:
        data = build(extract(Path(tmp)))
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(data['nodes'])} nodes, {len(data['edges'])} connections, "
          f"{len(data['regions'])} regions ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
