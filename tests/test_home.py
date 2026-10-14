import pytest

from orion.brain import router
from orion.tools.registry import ToolError


class R:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.content = data, status, b"x"

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(self.status_code)


STATES = [
    {"entity_id": "light.living_room", "state": "off", "attributes": {"friendly_name": "Living Room Lamp", "brightness": 0}},
    {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen", "brightness": 255}},
    {"entity_id": "scene.movie_night", "state": "scening", "attributes": {"friendly_name": "Movie Night"}},
    {"entity_id": "sensor.indoor_temp", "state": "21.5", "attributes": {"friendly_name": "Indoor temperature", "unit_of_measurement": "°C"}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {"friendly_name": "Front Door"}},
    {"entity_id": "automation.x", "state": "on", "attributes": {}},
]


@pytest.fixture
def ha(engine, monkeypatch):
    import httpx

    calls = []
    monkeypatch.setattr(httpx, "get", lambda url, **k: R(STATES))

    def request(method, url, json=None, **k):
        calls.append((method, url.split("/api", 1)[1], json))
        return R(STATES if url.endswith("/api/states") else [])

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setattr("orion.ecosystem.home._secret_set", lambda n, v: True)
    monkeypatch.setattr("orion.ecosystem.home._secret_get", lambda n, f="": "tok")
    engine.home.connect_ha("homeassistant.local:8123", "tok")
    return calls


def test_tools_appear_only_when_connected(engine, ha):
    assert engine.home.public()["devices"] == 5
    for name, risk in [("home_devices", "safe"), ("home_control", "safe"), ("home_scene", "safe"), ("home_security", "danger")]:
        assert engine.registry.get(name).risk == risk
    engine.home.disconnect("ha")
    assert engine.registry.get("home_control") is None


def test_control_dim_colour_scene_and_sensors(engine, ha):
    out = engine.home.home_control("living room", "on", brightness=30, color="warm")
    assert "Living Room Lamp" in out
    assert ha[-1] == ("POST", "/services/light/turn_on", {"entity_id": "light.living_room", "brightness_pct": 30, "hs_color": [35, 45]})
    engine.home.home_control("all lights", "off")
    assert {c[2]["entity_id"] for c in ha if c[1] == "/services/light/turn_off"} == {"light.living_room", "light.kitchen"}
    assert engine.home.home_scene("movie") == "Activated Movie Night."
    temp = engine.home.home_devices("temperature")
    assert temp[0]["state"] == "21.5 °C"


def test_locks_are_not_reachable_from_home_control(engine, ha):
    with pytest.raises(ToolError):
        engine.home.home_control("front door", "off")
    assert engine.home.home_security("front door", "unlock") == "Front Door: unlock."
    assert ha[-1][1] == "/services/lock/unlock"


@pytest.mark.parametrize("text,args", [
    ("turn on the lights", {"target": "lights", "action": "on"}),
    ("turn off the kitchen light", {"target": "kitchen light", "action": "off"}),
    ("lights off", {"target": "lights", "action": "off"}),
    ("dim the bedroom to 20%", {"target": "bedroom", "action": "set", "brightness": 20}),
])
def test_light_commands_hit_the_fast_path(text, args):
    r = router.match(text)
    assert r and r.tool == "home_control" and r.args == args
