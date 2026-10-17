"""Windows toolkit: settings, connectivity, software and maintenance.

Every action reads the result back (theme, wallpaper, Wi-Fi state, power plan, window position…) and
reports what actually happened. Parsers avoid translated text: they key on SSIDs, percentages, GUIDs,
XML tags and exit codes, so they work on Windows in any language.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Literal

from .files import _human
from .platform import IS_WIN, powershell, resolve_path
from .registry import ToolContext, ToolError, tool

KIT = "windows"


def _need_windows() -> None:
    if not IS_WIN:
        raise ToolError("This only works on Windows.")


def _oem_run(args: list[str] | str, timeout: float = 30) -> tuple[int, str]:
    """Run a console tool and decode its output with the console code page (accents in SSIDs etc.)."""
    import subprocess

    from .platform import CREATE_NO_WINDOW

    enc = "utf-8"
    if IS_WIN:
        import ctypes

        enc = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    res = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return res.returncode, (res.stdout or b"").decode(enc, "replace") + (res.stderr or b"").decode(enc, "replace")


# ── Wi-Fi ──────────────────────────────────────────────────────────────────
def _kv(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    k, v = line.split(":", 1)
    return k.strip(), v.strip()


def parse_wlan_interfaces(text: str) -> list[dict]:
    """`netsh wlan show interfaces` → [{name, ssid, signal, connected}] (works in any UI language)."""
    out: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if not line.startswith((" ", "\t")):
            continue  # "There is 1 interface on the system:" and similar headers
        kv = _kv(line)
        if not kv:
            continue
        k, v = kv
        # The first key of each block is the adapter name ("Name"/"Nombre"/"Nom"…), indented 4 spaces.
        if cur is None or (line.startswith("    ") and not line.startswith("     ") and k.lower() in
                           ("name", "nombre", "nom", "nome", "naam", "名称", "名前")):
            cur = {"name": v, "ssid": "", "signal": None, "connected": False}
            out.append(cur)
            continue
        if k == "SSID":
            cur["ssid"] = v
            cur["connected"] = bool(v)
        elif v.endswith("%") and v[:-1].strip().isdigit():
            cur["signal"] = int(v[:-1])
    return out


def parse_wlan_networks(text: str) -> list[dict]:
    """`netsh wlan show networks mode=bssid` → [{ssid, signal, security}] strongest first."""
    nets: dict[str, dict] = {}
    cur: dict | None = None
    for line in text.splitlines():
        m = re.match(r"^SSID \d+\s*:\s*(.*)$", line.strip())
        if m:
            name = m.group(1).strip()
            cur = nets.setdefault(name or "(hidden network)", {"ssid": name or "(hidden network)", "signal": 0,
                                                              "security": ""})
            continue
        kv = _kv(line)
        if not kv or cur is None:
            continue
        k, v = kv
        if v.endswith("%") and v[:-1].strip().isdigit():
            cur["signal"] = max(cur["signal"], int(v[:-1]))
        elif not cur["security"] and re.search(r"WPA|WEP|Open|Abierta|Ouvert|Offen|Aberta|Aperta|OWE|SAE", v) \
                and not re.search(r"CCMP|GCMP|TKIP|None|Ninguno|Aucun", v):
            cur["security"] = v
    return sorted(nets.values(), key=lambda n: -n["signal"])


def _wifi_profile_xml(ssid: str, password: str, security: str) -> str:
    from xml.sax.saxutils import escape

    sec = security.upper()
    if not password or "OPEN" in sec or "ABIERTA" in sec:
        auth, enc, key = "open", "none", ""
    else:
        auth = "WPA3SAE" if "WPA3" in sec else ("WPAPSK" if sec.startswith("WPA-") or sec == "WPA" else "WPA2PSK")
        enc = "AES"
        key = (f"<sharedKey><keyType>passPhrase</keyType><protected>false</protected>"
               f"<keyMaterial>{escape(password)}</keyMaterial></sharedKey>")
    name = escape(ssid)
    return (f'<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
            f"<name>{name}</name><SSIDConfig><SSID><name>{name}</name></SSID></SSIDConfig>"
            f"<connectionType>ESS</connectionType><connectionMode>auto</connectionMode><MSM><security>"
            f"<authEncryption><authentication>{auth}</authentication><encryption>{enc}</encryption>"
            f"<useOneX>false</useOneX></authEncryption>{key}</security></MSM></WLANProfile>")


def _wifi_status() -> list[dict]:
    rc, out = _oem_run(["netsh", "wlan", "show", "interfaces"])
    ifaces = parse_wlan_interfaces(out)
    if not ifaces:
        raise ToolError("No Wi-Fi adapter found on this PC (or the WLAN service is off).")
    return ifaces


def _set_radio(kind: str, on: bool) -> str:
    """Turn the Wi-Fi or Bluetooth radio on/off through the WinRT Radios API (same switch as Quick Settings)."""
    script = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | Where-Object { $_.Kind -eq '__KIND__' } | Select-Object -First 1
if (-not $r) { 'NONE'; exit }
if ('__STATE__' -ne 'Query') { $res = Await ($r.SetStateAsync('__STATE__')) ([Windows.Devices.Radios.RadioAccessStatus]); "RESULT:$res" }
Start-Sleep -Milliseconds 400
"STATE:$($r.State)"
"""
    try:
        out = powershell(script.replace("__KIND__", kind).replace("__STATE__", "On" if on else "Off"), timeout=30,
                         legacy=True)
    except Exception as exc:
        raise ToolError(f"Couldn't reach the {kind} radio: {str(exc)[:200]}") from exc
    if "NONE" in out:
        raise ToolError(f"This PC has no {kind} radio (or its driver is off).")
    m = re.search(r"RESULT:(\w+)", out)
    if m and m.group(1) != "Allowed":
        raise ToolError(f"Windows refused to change {kind} ({m.group(1)}). It may be disabled by policy or airplane mode.")
    state = re.search(r"STATE:(\w+)", out)
    return state.group(1) if state else "Unknown"


@tool(risk="confirm", category="windows", kit=KIT,
      summary=lambda a: f"Wi-Fi: {a.get('action')}" + (f" “{a.get('network')}”" if a.get("network") else ""))
def wifi(action: Literal["status", "list", "connect", "disconnect", "on", "off"] = "status", network: str = "",
         password: str = "") -> dict | list[dict] | str:
    """Wi-Fi: see the connection, list nearby networks, connect (saved or new network), disconnect, or turn the
    Wi-Fi radio on/off.

    Args:
        action: What to do.
        network: Network name (SSID) for connect.
        password: Password for a network this PC hasn't joined before.
    """
    _need_windows()
    if action in ("on", "off"):
        state = _set_radio("WiFi", action == "on")
        return f"Wi-Fi radio is now {state}."
    if action == "status":
        return _wifi_status()
    if action == "list":
        _oem_run(["netsh", "wlan", "show", "networks", "mode=bssid"])  # nudges a fresh scan
        time.sleep(2)
        rc, out = _oem_run(["netsh", "wlan", "show", "networks", "mode=bssid"])
        nets = parse_wlan_networks(out)
        if not nets:
            _wifi_status()  # raises a clear error if there's no adapter
            return "No networks in range (or Wi-Fi is turned off; try action 'on')."
        return nets[:25]
    if action == "disconnect":
        _oem_run(["netsh", "wlan", "disconnect"])
        time.sleep(2)
        still = [i["ssid"] for i in _wifi_status() if i["connected"]]
        if still:
            raise ToolError(f"Still connected to {still[0]}.")
        return "Disconnected from Wi-Fi."
    if not network:
        raise ToolError("Say which network to connect to.")
    rc, profiles = _oem_run(["netsh", "wlan", "show", "profiles"])
    known = re.search(rf":\s*{re.escape(network)}\s*$", profiles, re.M)
    if not known:
        rc, out = _oem_run(["netsh", "wlan", "show", "networks", "mode=bssid"])
        match = next((n for n in parse_wlan_networks(out) if n["ssid"].lower() == network.lower()), None)
        if match is None:
            raise ToolError(f"“{network}” isn't in range. Use action 'list' to see nearby networks.")
        network = match["ssid"]
        secured = match["security"] and not re.search(r"Open|Abierta|Ouvert|Offen|Aberta|Aperta", match["security"])
        if secured and not password:
            raise ToolError(f"“{network}” needs a password. Ask the user for it.")
        tmp = Path(tempfile.gettempdir()) / f"orion-wifi-{int(time.time())}.xml"
        tmp.write_text(_wifi_profile_xml(network, password, match["security"]), "utf-8")
        try:
            rc, out = _oem_run(f'netsh wlan add profile filename="{tmp}" user=current')
        finally:
            tmp.unlink(missing_ok=True)
        if rc != 0:
            raise ToolError(f"Couldn't save the network: {out.strip()[:200]}")
    if '"' in network:
        raise ToolError("Network names with double quotes can't be joined automatically.")
    rc, out = _oem_run(f'netsh wlan connect name="{network}" ssid="{network}"')
    for _ in range(20):
        time.sleep(1)
        cur = [i for i in _wifi_status() if i["connected"] and i["ssid"] == network]
        if cur:
            return f"Connected to {network}" + (f" (signal {cur[0]['signal']}%)." if cur[0]["signal"] else ".")
    raise ToolError(f"Couldn't connect to {network}. Check the password, or that the network is in range."
                    + (" " + out.strip()[:150] if rc else ""))


@tool(risk="confirm", category="windows", kit=KIT, summary=lambda a: f"Bluetooth {a.get('action')}")
def bluetooth(action: Literal["on", "off", "status"] = "status") -> str:
    """Turn Bluetooth on or off, or check whether it's on. (Pair new devices in Settings → Bluetooth & devices.)

    Args:
        action: on, off or status.
    """
    _need_windows()
    if action == "status":
        try:
            out = powershell(_RADIO_QUERY.replace("__KIND__", "Bluetooth"), timeout=30, legacy=True)
        except Exception as exc:
            raise ToolError(f"Couldn't reach the Bluetooth radio: {str(exc)[:200]}") from exc
        if "NONE" in out:
            raise ToolError("This PC has no Bluetooth radio (or its driver is off).")
        state = re.search(r"STATE:(\w+)", out)
        return f"Bluetooth is {state.group(1) if state else 'in an unknown state'}."
    state = _set_radio("Bluetooth", action == "on")
    if state.lower() != action:
        raise ToolError(f"Asked to turn Bluetooth {action}, but it reports {state}.")
    return f"Bluetooth is now {state}."


_RADIO_QUERY = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | Where-Object { $_.Kind -eq '__KIND__' } | Select-Object -First 1
if (-not $r) { 'NONE' } else { "STATE:$($r.State)" }
"""


# ── Look & feel ────────────────────────────────────────────────────────────
PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _broadcast_setting(area: str) -> None:
    import ctypes

    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x2
    res = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, ctypes.c_wchar_p(area),
                                             SMTO_ABORTIFHUNG, 3000, ctypes.byref(res))


@tool(category="windows", kit=KIT, summary=lambda a: f"Switch to {a.get('mode')} mode")
def set_theme(mode: Literal["dark", "light"], apps_only: bool = False) -> str:
    """Switch Windows between dark and light mode (apps and taskbar, or only apps).

    Args:
        mode: dark or light.
        apps_only: Leave the taskbar/Start menu as they are.
    """
    _need_windows()
    import winreg

    value = 0 if mode == "dark" else 1
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as k:
        winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, value)
        if not apps_only:
            winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)
    _broadcast_setting("ImmersiveColorSet")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as k:
        if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] != value:
            raise ToolError("Windows didn't keep the theme change.")
    return f"Switched to {mode} mode{' for apps' if apps_only else ''}."


@tool(risk="confirm", category="windows", kit=KIT, summary=lambda a: f"Set wallpaper to {a.get('image')}")
def set_wallpaper(image: str, fit: Literal["fill", "fit", "stretch", "center", "tile", "span"] = "fill") -> str:
    """Set the desktop wallpaper to an image file (download it first with download_file if it's online).

    Args:
        image: Path to a .jpg/.png/.bmp image.
        fit: How the image fills the screen.
    """
    _need_windows()
    import ctypes
    import winreg

    p = resolve_path(image)
    if not p.is_file():
        raise ToolError(f"Image not found: {p}")
    style, tile = {"fill": ("10", "0"), "fit": ("6", "0"), "stretch": ("2", "0"), "center": ("0", "0"),
                   "tile": ("0", "1"), "span": ("22", "0")}[fit]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "WallpaperStyle", 0, winreg.REG_SZ, style)
        winreg.SetValueEx(k, "TileWallpaper", 0, winreg.REG_SZ, tile)
    SPI_SETDESKWALLPAPER, SPI_GETDESKWALLPAPER = 0x14, 0x73
    if not ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(p), 0x3):
        raise ToolError("Windows refused that image as a wallpaper.")
    buf = ctypes.create_unicode_buffer(520)
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 520, buf, 0)
    if Path(buf.value).resolve() != p and "TranscodedWallpaper" not in buf.value:
        raise ToolError(f"The wallpaper didn't change (Windows reports {buf.value}).")
    return f"Wallpaper set to {p.name} ({fit})."


# ── Maintenance ────────────────────────────────────────────────────────────
@tool(risk="danger", category="windows", kit=KIT, summary=lambda a: "Empty the Recycle Bin")
def empty_recycle_bin() -> str:
    """Permanently delete everything in the Recycle Bin to free space (can't be undone)."""
    _need_windows()
    import ctypes
    from ctypes import wintypes

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    if info.i64NumItems == 0:
        return "The Recycle Bin is already empty."
    hr = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
    if hr not in (0, -2147418113):  # S_OK, or E_UNEXPECTED when already empty
        raise ToolError(f"Windows couldn't empty the Recycle Bin (error {hr & 0xFFFFFFFF:#x}).")
    return f"Emptied the Recycle Bin: {info.i64NumItems} item(s), {_human(info.i64Size)} freed."


@tool(risk="confirm", category="windows", kit=KIT, summary=lambda a: "Clean temporary files")
def clean_temp_files(older_than_hours: int = 24, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Delete temporary files (the user's Temp folder) older than a day to free space. Files in use are skipped.

    Args:
        older_than_hours: Only delete items not modified for this long.
    """
    roots = {Path(tempfile.gettempdir())}
    if IS_WIN and os.environ.get("LOCALAPPDATA"):
        roots.add(Path(os.environ["LOCALAPPDATA"]) / "Temp")
    cutoff = time.time() - older_than_hours * 3600
    freed, count, skipped = 0, 0, 0
    for root in roots:
        if not root.is_dir():
            continue
        for item in list(root.iterdir()):
            if ctx is not None:
                ctx.check_cancelled()
            try:
                st = item.stat()
                if st.st_mtime > cutoff or item.name.startswith("orion"):
                    continue
                if item.is_dir() and not item.is_symlink():
                    size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                    shutil.rmtree(item)
                else:
                    size = st.st_size
                    item.unlink()
                freed += size
                count += 1
            except (OSError, PermissionError):
                skipped += 1
    return (f"Cleaned {count} temporary item(s), freeing {_human(freed)}."
            + (f" {skipped} in use were left alone." if skipped else ""))


@tool(category="windows", kit=KIT)
def drive_health() -> list[dict]:
    """Check the health of the PC's drives (SSD/HDD): status, type, size, and how full each volume is."""
    _need_windows()
    try:
        out = powershell("Get-PhysicalDisk | Select-Object FriendlyName,@{n='MediaType';e={\"$($_.MediaType)\"}},"
                         "@{n='HealthStatus';e={\"$($_.HealthStatus)\"}},@{n='OperationalStatus';"
                         "e={\"$($_.OperationalStatus)\"}},@{n='SizeGB';e={[math]::Round($_.Size/1GB)}} | "
                         "ConvertTo-Json -Compress", timeout=40)
    except Exception as exc:
        raise ToolError(f"Couldn't read drive health: {str(exc)[:200]}") from exc
    disks = json.loads(out) if out.strip() else []
    disks = disks if isinstance(disks, list) else [disks]
    import psutil

    vols = []
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            vols.append({"volume": part.mountpoint, "used_percent": u.percent, "free": _human(u.free)})
        except OSError:
            continue
    return [{"disk": d.get("FriendlyName"), "type": str(d.get("MediaType")),
             "health": str(d.get("HealthStatus")), "status": str(d.get("OperationalStatus")),
             "size_gb": d.get("SizeGB")} for d in disks] + vols


def parse_battery_report(xml_text: str) -> dict:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    ns = {"b": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}

    def find(path: str):
        return root.find(path, ns) if ns else root.find(path.replace("b:", ""))

    batt = find(".//b:Batteries/b:Battery")
    if batt is None:
        raise ToolError("No battery found (this looks like a desktop PC).")

    def val(tag: str) -> int:
        el = batt.find(f"b:{tag}", ns) if ns else batt.find(tag)
        return int(el.text) if el is not None and el.text and el.text.strip().isdigit() else 0

    design, full = val("DesignCapacity"), val("FullChargeCapacity")
    return {"design_capacity_mWh": design, "full_charge_mWh": full, "cycle_count": val("CycleCount"),
            "health_percent": round(full * 100 / design) if design else None}


@tool(category="windows", kit=KIT)
def battery_health() -> dict:
    """Check the laptop battery's health: design vs current full-charge capacity (wear) and charge cycles."""
    _need_windows()
    import psutil

    if psutil.sensors_battery() is None:
        raise ToolError("No battery found (this looks like a desktop PC).")
    out = Path(tempfile.gettempdir()) / f"orion-battery-{int(time.time())}.xml"
    try:
        rc, text = _oem_run(["powercfg", "/batteryreport", "/xml", "/output", str(out)], timeout=60)
        if not out.exists():
            raise ToolError(f"Windows couldn't create a battery report: {text.strip()[:200]}")
        info = parse_battery_report(out.read_text("utf-8", errors="replace"))
    finally:
        out.unlink(missing_ok=True)
    h = info["health_percent"]
    info["verdict"] = ("unknown" if h is None else "good" if h >= 80 else "worn — expect shorter battery life" if h >= 60
                       else "heavily worn — consider replacing")
    return info


# ── Power plans ────────────────────────────────────────────────────────────
PLAN_ALIASES = {"balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
                "high performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                "power saver": "a1841308-3bce-4f47-aa5d-1c9fb4a4b8ed",
                "ultimate performance": "e9a42b02-d5df-448d-aa00-03f14749eb61"}


def parse_powercfg_list(text: str) -> list[dict]:
    plans = []
    for m in re.finditer(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+\((.+?)\)\s*(\*)?", text, re.I):
        plans.append({"guid": m.group(1).lower(), "name": m.group(2).strip(), "active": bool(m.group(3))})
    return plans


@tool(category="windows", kit=KIT, summary=lambda a: f"Power plan: {a.get('plan') or 'list'}")
def power_plan(plan: str = "") -> list[dict] | str:
    """List power plans, or switch plan: "balanced", "high performance", "power saver" or a plan's name.

    Args:
        plan: The plan to switch to (empty = just list them).
    """
    _need_windows()
    rc, out = _oem_run(["powercfg", "/list"])
    plans = parse_powercfg_list(out)
    if not plan:
        return plans
    want = plan.strip().lower()
    guid = next((p["guid"] for p in plans if p["name"].lower() == want), None) or \
        next((p["guid"] for p in plans if want in p["name"].lower()), None) or PLAN_ALIASES.get(want)
    if guid not in {p["guid"] for p in plans}:
        raise ToolError(f"No power plan called “{plan}”. Plans: " + ", ".join(p["name"] for p in plans))
    _oem_run(["powercfg", "/setactive", guid])
    rc, out = _oem_run(["powercfg", "/getactivescheme"])
    if guid not in out.lower():
        raise ToolError("Windows didn't switch plans (it may be managed by your organisation).")
    return f"Power plan is now {next(p['name'] for p in plans if p['guid'] == guid)}."


# ── Software (winget) ──────────────────────────────────────────────────────
def parse_winget_table(text: str, kind: str = "search") -> list[dict]:
    """Parse winget's fixed-width table using the header's column positions (header words may be translated)."""
    lines = [re.sub(r".*\r", "", ln).rstrip() for ln in text.splitlines()]
    sep = next((i for i, ln in enumerate(lines) if re.fullmatch(r"-{10,}", ln.strip())), None)
    if sep is None or sep == 0:
        return []
    header = lines[sep - 1]
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    if len(starts) >= 5:
        keys = ["name", "id", "version", "available" if kind == "upgrade" else "match", "source"]
    else:
        keys = ["name", "id", "version", "source"]
    rows = []
    for ln in lines[sep + 1:]:
        if not ln.strip() or re.match(r"^\d+\s+\S", ln):  # "2 upgrades available." in any language
            continue
        cells = [ln[s:e].strip() for s, e in zip(starts, starts[1:] + [len(ln) + 1], strict=False)]
        row = dict(zip(keys, cells, strict=False))
        if row.get("id") and " " not in row["id"]:
            rows.append(row)
    return rows


def _winget(*args: str, timeout: float = 120) -> tuple[int, str]:
    _need_windows()
    exe = shutil.which("winget")
    if not exe:
        raise ToolError("winget (App Installer) isn't available. Install “App Installer” from the Microsoft Store.")
    return _oem_run([exe, *args, "--accept-source-agreements", "--disable-interactivity"], timeout=timeout)


@tool(category="windows", kit=KIT)
def find_app_to_install(query: str) -> list[dict] | str:
    """Search the Windows Package Manager catalog (winget) for software to install.

    Args:
        query: App name, e.g. "VLC", "7-Zip", "Discord".
    """
    rc, out = _winget("search", query)
    rows = parse_winget_table(out)
    return rows[:15] or f"No package found for “{query}”."


@tool(risk="confirm", category="windows", kit=KIT, summary=lambda a: f"Install {a.get('app')}")
def install_app(app: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Install an app silently with winget. Pass the exact package id from find_app_to_install when possible
    (e.g. "VideoLAN.VLC"); a plain name works when it's unambiguous.

    Args:
        app: Package id or app name.
    """
    target = app.strip()
    if not re.fullmatch(r"[\w.+-]+\.[\w.+-]+", target):
        rows = parse_winget_table(_winget("search", target)[1])
        exact = [r for r in rows if r["name"].lower() == target.lower()]
        if len(exact) == 1 or len(rows) == 1:
            target = (exact or rows)[0]["id"]
        elif not rows:
            raise ToolError(f"No package found for “{app}”.")
        else:
            raise ToolError("Several packages match; ask which one: " +
                            "; ".join(f"{r['name']} ({r['id']})" for r in rows[:6]))
    if ctx is not None:
        ctx.progress(f"Installing {target}…")
    rc, out = _winget("install", "--id", target, "-e", "--silent", "--accept-package-agreements", timeout=900)
    if rc != 0:
        rc2, listed = _winget("list", "--id", target, "-e")
        if target.lower() in listed.lower():
            return f"{target} is installed (it was already there or needed no changes)."
        tail = [ln for ln in out.splitlines() if ln.strip() and not re.match(r"^[\s\-\\|/█▒]+$", ln)][-3:]
        raise ToolError(f"Installing {target} failed (code {rc & 0xFFFFFFFF:#x}): " + " ".join(tail)[:300])
    return f"Installed {target}."


@tool(risk="danger", category="windows", kit=KIT, summary=lambda a: f"Uninstall {a.get('app')}")
def uninstall_app(app: str) -> str:
    """Uninstall an app with winget (use its id or exact name from `winget list`).

    Args:
        app: Package id or exact app name.
    """
    flag = ["--id", app] if re.fullmatch(r"[\w.+-]+\.[\w.+-]+", app.strip()) else ["--name", app]
    rc, out = _winget("uninstall", *flag, "-e", "--silent", timeout=600)
    if rc != 0:
        raise ToolError(f"Uninstalling {app} failed: " + " ".join(out.strip().splitlines()[-2:])[:300])
    return f"Uninstalled {app}."


@tool(risk="confirm", category="windows", kit=KIT,
      summary=lambda a: "Update all apps" if a.get("update_all") else "Check for app updates")
def app_updates(update_all: bool = False, ctx: ToolContext = None) -> list[dict] | str:  # type: ignore[assignment]
    """List apps with updates available (winget), or update them all silently.

    Args:
        update_all: Install every available update.
    """
    rc, out = _winget("upgrade")
    rows = parse_winget_table(out, "upgrade")
    if not update_all:
        return rows or "All apps are up to date."
    if not rows:
        return "All apps are already up to date."
    if ctx is not None:
        ctx.progress(f"Updating {len(rows)} app(s)…")
    rc, out = _winget("upgrade", "--all", "--silent", "--accept-package-agreements", timeout=3600)
    left = parse_winget_table(_winget("upgrade")[1], "upgrade")
    done = len(rows) - len(left)
    return f"Updated {done} of {len(rows)} app(s)." + (
        f" Still pending: {', '.join(r['name'] for r in left[:8])}." if left else "")


# ── Startup apps ───────────────────────────────────────────────────────────
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"


def _startup_entries() -> list[dict]:
    import winreg

    out = []
    for hive, hive_name in ((winreg.HKEY_CURRENT_USER, "user"), (winreg.HKEY_LOCAL_MACHINE, "all users")):
        try:
            with winreg.OpenKey(hive, RUN_KEY) as k:
                i = 0
                while True:
                    try:
                        name, cmd, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out.append({"name": name, "command": str(cmd)[:160], "scope": hive_name, "kind": "Run", "hive": hive})
                    i += 1
        except OSError:
            continue
    folder = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
    if folder.is_dir():
        for f in folder.iterdir():
            if f.suffix.lower() in (".lnk", ".url", ".bat", ".cmd", ".exe"):
                out.append({"name": f.name, "command": str(f), "scope": "user", "kind": "StartupFolder",
                            "hive": winreg.HKEY_CURRENT_USER})
    for e in out:
        e["enabled"] = True
        try:
            with winreg.OpenKey(e["hive"], f"{APPROVED}\\{e['kind']}") as k:
                data, _ = winreg.QueryValueEx(k, e["name"])
                e["enabled"] = not (data and data[0] & 1)  # 0x02/0x06 enabled, 0x03/0x07 disabled
        except OSError:
            pass
    return out


@tool(risk="confirm", category="windows", kit=KIT,
      summary=lambda a: f"{str(a.get('action', 'list')).title()} startup app {a.get('name', '')}".strip())
def startup_apps(action: Literal["list", "disable", "enable"] = "list", name: str = "") -> list[dict] | str:
    """List the apps that start with Windows, or enable/disable one (like Task Manager → Startup apps).

    Args:
        action: list, disable or enable.
        name: The app's startup entry name (from the list) for disable/enable.
    """
    _need_windows()
    import struct
    import winreg

    entries = _startup_entries()
    if action == "list":
        return [{k: v for k, v in e.items() if k != "hive"} for e in entries] or "Nothing starts with Windows."
    match = [e for e in entries if e["name"].lower() == name.lower()] or \
        [e for e in entries if name.lower() in e["name"].lower()]
    if not match:
        raise ToolError(f"No startup app called “{name}”.")
    e = match[0]
    disabled = action == "disable"
    filetime = int((time.time() + 11644473600) * 10_000_000)
    data = (b"\x03\x00\x00\x00" + struct.pack("<Q", filetime)) if disabled else (b"\x02" + b"\x00" * 11)
    try:
        with winreg.CreateKey(e["hive"], f"{APPROVED}\\{e['kind']}") as k:
            winreg.SetValueEx(k, e["name"], 0, winreg.REG_BINARY, data)
    except PermissionError as exc:
        raise ToolError(f"“{e['name']}” is installed for all users, so changing it needs admin rights. "
                        "Open Task Manager → Startup apps to change it.") from exc
    now = next(x for x in _startup_entries() if x["name"] == e["name"] and x["kind"] == e["kind"])
    if now["enabled"] == disabled:
        raise ToolError("Windows didn't keep the change.")
    return f"{e['name']} will {'no longer' if disabled else 'now'} start with Windows."


# ── Network ────────────────────────────────────────────────────────────────
@tool(category="windows", kit=KIT)
def network_info() -> dict:
    """Show network details: connected adapters and local IP addresses, public IP and location, Wi-Fi network."""
    import socket

    import httpx
    import psutil

    stats = psutil.net_if_stats()
    adapters = []
    for name, addrs in psutil.net_if_addrs().items():
        st = stats.get(name)
        ips = [a.address for a in addrs if a.family == socket.AF_INET and not a.address.startswith("169.254")]
        if st and st.isup and ips and not name.lower().startswith(("loopback", "lo")):
            adapters.append({"adapter": name, "ip": ips[0], "speed_mbps": st.speed or None})
    info: dict = {"adapters": adapters}
    try:
        r = httpx.get("https://ipinfo.io/json", timeout=8).json()
        info["public_ip"] = r.get("ip")
        info["location"] = ", ".join(x for x in (r.get("city"), r.get("region"), r.get("country")) if x)
        info["provider"] = r.get("org")
    except Exception:
        info["public_ip"] = "unavailable (no internet?)"
    if IS_WIN:
        try:
            wl = [i for i in _wifi_status() if i["connected"]]
            if wl:
                info["wifi"] = {"network": wl[0]["ssid"], "signal_percent": wl[0]["signal"]}
        except ToolError:
            pass
    return info


@tool(category="windows", kit=KIT)
def internet_speed_test(ctx: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Measure internet speed (download, upload, latency) against Cloudflare's speed test servers (~15 s)."""
    import httpx

    base = "https://speed.cloudflare.com"
    with httpx.Client(timeout=30, headers={"User-Agent": "ORION speed test"}) as c:
        lat = []
        for _ in range(5):
            t = time.perf_counter()
            c.get(f"{base}/__down?bytes=0")
            lat.append((time.perf_counter() - t) * 1000)
        if ctx is not None:
            ctx.progress("Testing download…")
        size, got = 25_000_000, 0
        t = time.perf_counter()
        with c.stream("GET", f"{base}/__down?bytes={size}") as r:
            for chunk in r.iter_bytes(1 << 16):
                got += len(chunk)
                if time.perf_counter() - t > 12:
                    break
        down = got * 8 / (time.perf_counter() - t) / 1e6
        if ctx is not None:
            ctx.progress("Testing upload…")
        payload = os.urandom(8_000_000)
        t = time.perf_counter()
        c.post(f"{base}/__up", content=payload)
        up = len(payload) * 8 / (time.perf_counter() - t) / 1e6
    return {"download_mbps": round(down, 1), "upload_mbps": round(up, 1), "latency_ms": round(min(lat)),
            "jitter_ms": round(max(lat) - min(lat))}


def parse_ping(text: str, sent: int) -> dict:
    times = [int(t) for t in re.findall(r"[=<](\d+)\s?ms", text)]
    times = times[:sent]
    return {"replies": len(times), "sent": sent, "loss_percent": round((sent - len(times)) * 100 / sent),
            "avg_ms": round(sum(times) / len(times)) if times else None, "min_ms": min(times) if times else None,
            "max_ms": max(times) if times else None}


@tool(category="windows", kit=KIT)
def ping(host: str = "1.1.1.1", count: int = 4) -> dict:
    """Ping a website or IP address to check it's reachable and how fast it responds.

    Args:
        host: Domain or IP, e.g. "google.com" or "192.168.1.1" (the router).
        count: Number of pings (1-20).
    """
    if not re.fullmatch(r"[\w.:-]+", host):
        raise ToolError("That doesn't look like a host name or IP.")
    count = max(1, min(count, 20))
    rc, out = _oem_run(["ping", "-n" if IS_WIN else "-c", str(count), host], timeout=count * 5 + 10)
    res = parse_ping(out, count)
    res["host"] = host
    if not res["replies"]:
        res["note"] = "No replies: the host is down, blocks ping, or the name doesn't resolve."
    return res


@tool(category="windows", kit=KIT)
def check_windows_updates() -> list[dict] | str:
    """Check Windows Update for pending updates (can take a minute). To install them, open the Windows Update
    settings page for the user."""
    _need_windows()
    script = ("$s = New-Object -ComObject Microsoft.Update.Session; $r = $s.CreateUpdateSearcher().Search("
              "'IsInstalled=0 and IsHidden=0'); $r.Updates | ForEach-Object { [pscustomobject]@{ title = $_.Title; "
              "size_mb = [math]::Round($_.MaxDownloadSize/1MB); important = ($_.MsrcSeverity -ne $null) } } | "
              "ConvertTo-Json -Compress")
    try:
        out = powershell(script, timeout=240)
    except Exception as exc:
        raise ToolError(f"Windows Update couldn't be checked: {str(exc)[:200]}") from exc
    items = json.loads(out) if out.strip() else []
    items = items if isinstance(items, list) else [items]
    return items or "Windows is up to date."


# ── Window layout ──────────────────────────────────────────────────────────
POSITIONS = ("left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right", "center",
             "maximize")


SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0004, 0x0010, 0x0040


def _frame_rect(hwnd: int) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # extended frame
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def place_window(hwnd: int, position: str, monitor: int = 0) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    monitors: list[int] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        lambda h, *_: monitors.append(h) or 1)
    user32.EnumDisplayMonitors(None, None, proc, 0)
    hmon = monitors[monitor - 1] if 0 < monitor <= len(monitors) else user32.MonitorFromWindow(hwnd, 2)
    mi = MONITORINFO(ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi))
    wa = mi.rcWork
    W, H = wa.right - wa.left, wa.bottom - wa.top
    # Never activate the window: it moves, but the user's focus stays where it was.
    flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE restores without activating (SW_RESTORE would)
    if position == "maximize":
        user32.SetWindowPos(hwnd, 0, wa.left, wa.top, W, H, flags)
        return _frame_rect(hwnd)
    half_w, half_h = W // 2, H // 2
    x, y, w, h = {
        "left": (0, 0, half_w, H), "right": (half_w, 0, W - half_w, H), "top": (0, 0, W, half_h),
        "bottom": (0, half_h, W, H - half_h), "top-left": (0, 0, half_w, half_h),
        "top-right": (half_w, 0, W - half_w, half_h), "bottom-left": (0, half_h, half_w, H - half_h),
        "bottom-right": (half_w, half_h, W - half_w, H - half_h),
        "center": (W // 6, H // 8, W * 2 // 3, H * 3 // 4),
    }[position]
    x, y = wa.left + x, wa.top + y
    # Compensate for the invisible resize borders Windows 10/11 add around windows.
    wr = wintypes.RECT()
    user32.SetWindowPos(hwnd, 0, x, y, w, h, flags)
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    fl, ft, fr, fb = _frame_rect(hwnd)
    dl, dt, dr, db = fl - wr.left, ft - wr.top, wr.right - fr, wr.bottom - fb
    user32.SetWindowPos(hwnd, 0, x - dl, y - dt, w + dl + dr, h + dt + db, flags)
    return _frame_rect(hwnd)


@tool(category="windows", kit=KIT, summary=lambda a: f"Move “{a.get('title')}” to {a.get('position')}")
def arrange_window(title: str, position: Literal["left", "right", "top", "bottom", "top-left", "top-right",
                                                 "bottom-left", "bottom-right", "center", "maximize"],
                   monitor: int = 0, second_title: str = "") -> str:
    """Snap a window to part of the screen (left/right half, a quarter, centre, maximised), optionally on another
    monitor. With second_title, puts two windows side by side (first left, second right).

    Args:
        title: Part of the window's title (see list_windows).
        position: Where to put it.
        monitor: Monitor number (1, 2…); 0 = the one it's on.
        second_title: Another window to place in the opposite half.
    """
    _need_windows()
    from .apps import _find_window

    w = _find_window(title)
    rect = place_window(w.handle, position, monitor)
    msg = f"Moved “{w.window_text()}” to the {position} ({rect[2] - rect[0]}×{rect[3] - rect[1]})."
    if second_title:
        opposite = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}.get(position, "right")
        w2 = _find_window(second_title)
        place_window(w2.handle, opposite, monitor)
        msg += f" “{w2.window_text()}” is on the {opposite}."
    return msg
