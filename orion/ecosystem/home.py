"""Smart home: Home Assistant (thousands of brands) and Philips Hue (direct, local).

One interface for ORION and the user: ``home_devices`` lists things, ``home_control``
switches/dims/colours them, ``home_scene`` runs scenes, and ``home_security``
(locks, alarms, garage doors) always needs approval. Tools are registered only
when a smart home is connected. Tokens live in the OS credential vault.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any

import httpx

from ..tools.registry import Registry, Tool, ToolError, schema_for

log = logging.getLogger("orion.home")
KEYRING_SERVICE = "ORION"
SECURITY_DOMAINS = {"lock", "alarm_control_panel"}
CONTROLLABLE = {"light", "switch", "fan", "climate", "media_player", "cover", "scene", "script", "input_boolean",
                "humidifier", "vacuum", "lock", "alarm_control_panel", "sensor", "binary_sensor", "button"}
COLORS = {"red": (0, 100), "orange": (30, 100), "yellow": (55, 100), "green": (120, 100), "cyan": (180, 100),
          "blue": (230, 100), "purple": (275, 100), "pink": (320, 70), "white": (0, 0), "warm": (35, 45), "cool": (210, 15)}


def _secret_get(name: str, fallback: str = "") -> str:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, name) or fallback
    except Exception:
        return fallback


def _secret_set(name: str, value: str) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, name, value)
        return True
    except Exception:
        return False


class SmartHome:
    def __init__(self, accounts: Any, registry: Registry) -> None:
        self.accounts = accounts  # shares accounts.json persistence
        self.registry = registry

    # ── config ─────────────────────────────────────────────────────────────
    @property
    def cfg(self) -> dict:
        return self.accounts.data.setdefault("home", {})

    def _save(self) -> None:
        self.accounts._write()
        self.sync_tools()

    def _ha_token(self) -> str:
        return _secret_get("home_assistant", self.cfg.get("ha_token_fallback", ""))

    def public(self) -> dict:
        c = self.cfg
        return {"ha_url": c.get("ha_url"), "ha_connected": bool(c.get("ha_url")), "hue_ip": c.get("hue_ip"),
                "hue_connected": bool(c.get("hue_user")), "devices": c.get("device_count", 0)}

    def connect_ha(self, url: str, token: str) -> dict:
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        token = token.strip() or self._ha_token()
        try:
            r = httpx.get(f"{url}/api/states", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        except httpx.HTTPError as exc:
            raise ToolError(f"Couldn't reach Home Assistant at {url} ({exc}).") from exc
        if r.status_code == 401:
            raise ToolError("Home Assistant rejected the token. Create a long-lived access token in your HA profile.")
        r.raise_for_status()
        self.cfg["ha_url"] = url
        if not _secret_set("home_assistant", token):
            self.cfg["ha_token_fallback"] = token
        self.cfg["device_count"] = len([e for e in r.json() if e["entity_id"].split(".")[0] in CONTROLLABLE])
        self._save()
        return self.public()

    def disconnect(self, which: str) -> dict:
        keys = {"ha": ("ha_url", "ha_token_fallback"), "hue": ("hue_ip", "hue_user")}[which]
        for k in keys:
            self.cfg.pop(k, None)
        self._save()
        return self.public()

    @staticmethod
    def discover_hue() -> list[str]:
        try:
            return [b["internalipaddress"] for b in httpx.get("https://discovery.meethue.com", timeout=8).json()]
        except Exception:
            return []

    def pair_hue(self, ip: str) -> dict:
        """Call within 30 s of pressing the round link button on the Hue Bridge."""
        try:
            r = httpx.post(f"http://{ip}/api", json={"devicetype": "orion#pc"}, timeout=8).json()
        except httpx.HTTPError as exc:
            raise ToolError(f"Couldn't reach a Hue Bridge at {ip}.") from exc
        item = r[0] if isinstance(r, list) and r else {}
        if "error" in item:
            raise ToolError("Press the round button on the Hue Bridge, then try again within 30 seconds.")
        self.cfg["hue_ip"], self.cfg["hue_user"] = ip, item["success"]["username"]
        self._save()
        return self.public()

    def sync_tools(self) -> None:
        self.registry.remove_source("home")
        if not (self.cfg.get("ha_url") or self.cfg.get("hue_user")):
            return
        for fn, risk, summary in (
            (self.home_devices, "safe", None),
            (self.home_control, "safe", lambda a: f"{str(a.get('action', '')).title()} {a.get('target')}"),
            (self.home_scene, "safe", lambda a: f"Scene: {a.get('name')}"),
            (self.home_security, "danger", lambda a: f"{str(a.get('action', '')).title()} {a.get('target')}"),
        ):
            schema, _, desc = schema_for(fn)
            self.registry.add(Tool(name=fn.__name__, description=desc, parameters=schema, fn=fn, risk=risk,  # type: ignore[arg-type]
                                   category="smart home", source="home", summarize=summary))

    # ── backends ───────────────────────────────────────────────────────────
    def _ha(self, method: str, path: str, json_body: dict | None = None) -> Any:
        url = self.cfg.get("ha_url")
        if not url:
            raise ToolError("Home Assistant isn't connected.")
        try:
            r = httpx.request(method, f"{url}{path}", json=json_body, timeout=15,
                              headers={"Authorization": f"Bearer {self._ha_token()}"})
            r.raise_for_status()
            return r.json() if r.content else None
        except httpx.HTTPError as exc:
            raise ToolError(f"Home Assistant error: {exc}") from exc

    def _hue(self, method: str, path: str, json_body: dict | None = None) -> Any:
        ip, user = self.cfg.get("hue_ip"), self.cfg.get("hue_user")
        if not user:
            raise ToolError("Philips Hue isn't connected.")
        try:
            r = httpx.request(method, f"http://{ip}/api/{user}{path}", json=json_body, timeout=8)
            return r.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"Hue Bridge error: {exc}") from exc

    def _devices(self) -> list[dict]:
        out: list[dict] = []
        if self.cfg.get("ha_url"):
            for e in self._ha("GET", "/api/states"):
                domain = e["entity_id"].split(".")[0]
                if domain not in CONTROLLABLE:
                    continue
                attrs = e.get("attributes", {})
                item = {"id": e["entity_id"], "name": attrs.get("friendly_name", e["entity_id"]), "type": domain,
                        "state": e["state"]}
                if "unit_of_measurement" in attrs:
                    item["state"] += f" {attrs['unit_of_measurement']}"
                if domain == "light" and attrs.get("brightness") is not None:
                    item["brightness"] = round(attrs["brightness"] / 2.55)
                out.append(item)
        if self.cfg.get("hue_user"):
            for lid, light in (self._hue("GET", "/lights") or {}).items():
                st = light.get("state", {})
                out.append({"id": f"hue:light:{lid}", "name": light.get("name"), "type": "light",
                            "state": "on" if st.get("on") else "off", "brightness": round(st.get("bri", 0) / 2.54)})
            for gid, group in (self._hue("GET", "/groups") or {}).items():
                if group.get("type") in ("Room", "Zone"):
                    out.append({"id": f"hue:group:{gid}", "name": group.get("name"), "type": "room",
                                "state": "on" if group.get("state", {}).get("any_on") else "off"})
        return out

    def _resolve(self, target: str, devices: list[dict], allow: set[str] | None = None) -> list[dict]:
        t = target.strip().lower()
        pool = [d for d in devices if allow is None or d["type"] in allow]
        if t in ("all lights", "lights", "all the lights", "the lights", "everything"):
            return [d for d in pool if d["type"] == "light"]
        exact = [d for d in pool if d["id"].lower() == t or str(d["name"]).lower() == t]
        if exact:
            return exact
        rooms = [d for d in pool if d["type"] == "room" and str(d["name"]).lower() in t]
        if rooms:
            return rooms
        part = [d for d in pool if t in str(d["name"]).lower() or str(d["name"]).lower() in t]
        if part:
            return part
        names = {str(d["name"]).lower(): d for d in pool}
        close = difflib.get_close_matches(t, list(names), n=1, cutoff=0.6)
        if close:
            return [names[close[0]]]
        raise ToolError(f"No device matching “{target}”. Use home_devices to see what's available.")

    # ── tools ──────────────────────────────────────────────────────────────
    def home_devices(self, query: str = "", type: str = "") -> list[dict]:
        """List smart-home devices (lights, switches, plugs, climate, media, covers, sensors, scenes) and their state.

        Args:
            query: Optional text to filter by name, e.g. "kitchen" or "temperature".
            type: Optional type filter: light, switch, climate, sensor, scene, room…
        """
        items = [d for d in self._devices() if (not query or query.lower() in str(d["name"]).lower())
                 and (not type or d["type"] == type)]
        return items[:60] or [{"note": "No matching devices."}]

    def home_control(self, target: str, action: str = "on", brightness: int = -1, color: str = "",
                     temperature: float = -1) -> str:
        """Control lights, plugs, switches, fans, thermostats, media players and blinds. Brightness is 0-100.

        Args:
            target: Device, room or group name ("living room lamp", "bedroom", "all lights") or an entity id.
            action: on, off, toggle, open, close, play, pause, or set (for brightness/colour/temperature only).
            brightness: 0-100 to dim a light (-1 = leave as is).
            color: Colour name: red, orange, yellow, green, cyan, blue, purple, pink, white, warm, cool.
            temperature: Thermostat target in °C (-1 = leave as is).
        """
        devices = self._devices()
        targets = self._resolve(target, devices, allow={"light", "switch", "fan", "climate", "media_player", "cover",
                                                        "input_boolean", "room", "humidifier", "vacuum"})
        action = action.lower().strip()
        done = []
        for d in targets[:20]:
            if d["id"].startswith("hue:"):
                self._hue_set(d, action, brightness, color)
            else:
                self._ha_set(d, action, brightness, color, temperature)
            done.append(d["name"])
        what = action if action != "set" else "updated"
        extra = f" at {brightness}%" if brightness >= 0 else ""
        return f"{', '.join(done)}: {what}{extra}{' ' + color if color else ''}."

    def _hue_set(self, d: dict, action: str, brightness: int, color: str) -> None:
        body: dict[str, Any] = {}
        if action in ("on", "off"):
            body["on"] = action == "on"
        elif action == "toggle":
            body["on"] = d["state"] != "on"
        if brightness >= 0:
            body.update(on=brightness > 0, bri=max(1, min(254, round(brightness * 2.54))))
        if color:
            h, s = COLORS.get(color.lower(), (None, None))
            if h is None:
                raise ToolError(f"Unknown colour {color}.")
            body.update(on=True, hue=round(h / 360 * 65535), sat=round(s * 2.54))
        _, kind, num = d["id"].split(":")
        path = f"/lights/{num}/state" if kind == "light" else f"/groups/{num}/action"
        self._hue("PUT", path, body)

    def _ha_set(self, d: dict, action: str, brightness: int, color: str, temperature: float) -> None:
        domain = d["type"]
        data: dict[str, Any] = {"entity_id": d["id"]}
        service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle", "open": "open_cover",
                   "close": "close_cover", "play": "media_play", "pause": "media_pause"}.get(action, "turn_on")
        if domain == "light" and (brightness >= 0 or color):
            service = "turn_on" if brightness != 0 else "turn_off"
            if brightness > 0:
                data["brightness_pct"] = brightness
            if color:
                h, s = COLORS.get(color.lower(), (None, None))
                if h is None:
                    raise ToolError(f"Unknown colour {color}.")
                data["hs_color"] = [h, s]
        if domain == "climate" and temperature >= 0:
            service, data["temperature"] = "set_temperature", temperature
        if domain in ("cover",) and action in ("on", "off"):
            service = "open_cover" if action == "on" else "close_cover"
        self._ha("POST", f"/api/services/{domain}/{service}", data)

    def home_scene(self, name: str) -> str:
        """Activate a scene or run a script/automation by name (e.g. "movie night", "good morning").

        Args:
            name: Scene or script name.
        """
        devices = self._devices()
        if self.cfg.get("ha_url"):
            matches = self._resolve(name, devices, allow={"scene", "script"})
            d = matches[0]
            self._ha("POST", f"/api/services/{d['type']}/turn_on", {"entity_id": d["id"]})
            return f"Activated {d['name']}."
        scenes = self._hue("GET", "/scenes") or {}
        names = {v.get("name", "").lower(): (k, v) for k, v in scenes.items()}
        hit = difflib.get_close_matches(name.lower(), list(names), n=1, cutoff=0.5)
        if not hit:
            raise ToolError(f"No scene called “{name}”.")
        sid, scene = names[hit[0]]
        self._hue("PUT", f"/groups/{scene.get('group', '0')}/action", {"scene": sid})
        return f"Activated {scene.get('name')}."

    def home_security(self, target: str, action: str) -> str:
        """Lock/unlock doors, arm/disarm alarms, open/close garage doors. Always requires the user's approval.

        Args:
            target: Lock, alarm or garage door name.
            action: lock, unlock, arm_home, arm_away, disarm, open or close.
        """
        devices = self._devices()
        d = self._resolve(target, devices, allow=SECURITY_DOMAINS | {"cover"})[0]
        domain = d["type"]
        service = {"lock": "lock", "unlock": "unlock", "arm_home": "alarm_arm_home", "arm_away": "alarm_arm_away",
                   "disarm": "alarm_disarm", "open": "open_cover", "close": "close_cover"}.get(action)
        if not service:
            raise ToolError("Unknown security action.")
        self._ha("POST", f"/api/services/{domain}/{service}", {"entity_id": d["id"]})
        return f"{d['name']}: {action.replace('_', ' ')}."

