from fastapi.testclient import TestClient

from orion import brainmap
from orion.server.api import create_app


def test_shipped_map_is_well_formed():
    data = brainmap.load()
    assert data and data["format"] == 1
    n, regions, files = len(data["nodes"]), len(data["regions"]), len(data["files"])
    assert n > 500 and regions == 12
    for label, file, line, region, kind, x, y in data["nodes"]:
        assert label and 0 <= file < files and line >= 0 and 0 <= region < regions and kind in "Fcmfs"
        assert isinstance(x, int) and isinstance(y, int)
    assert all(0 <= a < b < n for a, b in data["edges"])
    assert all(not f.startswith(("orion/web/", "tests/")) for f in data["files"])


def test_every_anchor_is_on_the_map():
    """A rename in the code without rebuilding the map would silently stop that part of the brain lighting up."""
    data = brainmap.load()
    index = brainmap._index(data)
    missing = [key for key, spot in brainmap.ANCHORS.items() if spot not in index]
    assert not missing, f"Rebuild the map (scripts/build_brain_map.py) or update ANCHORS: {missing}"


async def test_api_maps_tools_to_their_code(engine):
    client = TestClient(create_app(engine, token="t"))
    assert client.get("/api/brain").status_code == 401
    data = client.get("/api/brain", headers={"x-orion-token": "t"}).json()
    assert data["available"] and set(data["anchors"]) == set(brainmap.ANCHORS)

    node = data["nodes"][data["tools"]["open_app"]]
    assert node[0] == "open_app()" and data["files"][node[1]] == "orion/tools/apps.py"
    core = [t.name for t in engine.registry.all(include_disabled=True) if t.source == "core"]
    on_map = [name for name in core if name in data["tools"]]
    assert len(on_map) >= 0.9 * len(core), sorted(set(core) - set(on_map))


async def test_missing_map_is_reported_not_raised(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(brainmap, "MAP_FILE", tmp_path / "nope.json")
    brainmap.load.cache_clear()
    try:
        assert brainmap.payload(engine) == {"available": False}
    finally:
        brainmap.load.cache_clear()
