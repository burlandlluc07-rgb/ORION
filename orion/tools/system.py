"""System tools: time, vitals, volume, brightness, media keys, power, processes, clipboard."""

from __future__ import annotations

import datetime as dt
import platform
import shutil
import time
from typing import Literal

import logging

import psutil

from .platform import IS_LINUX, IS_MAC, IS_WIN, powershell, run
from .registry import ToolContext, ToolError, tool

log = logging.getLogger("orion.tools.system")


@tool(category="system")
def get_time_and_date() -> str:
    """Get the current local date, time and weekday."""
    now = dt.datetime.now()
    return now.strftime("%A, %d %B %Y — %H:%M:%S") + f" (timezone {time.strftime('%Z')})"


def vitals() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(_system_drive()))
    out = {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": vm.percent,
        "ram_used_gb": round(vm.used / 2**30, 1),
        "ram_total_gb": round(vm.total / 2**30, 1),
        "disk": disk.percent,
        "disk_free_gb": round(disk.free / 2**30, 1),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    batt = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if batt:
        out["battery"] = round(batt.percent)
        out["plugged"] = bool(batt.power_plugged)
    net = psutil.net_io_counters()
    out["net_sent_mb"] = round(net.bytes_sent / 2**20)
    out["net_recv_mb"] = round(net.bytes_recv / 2**20)
    return out


def _system_drive():
    import os
    from pathlib import Path

    return Path(os.environ.get("SystemDrive", "C:") + "\\") if IS_WIN else Path("/")


@tool(category="system")
def system_status() -> dict:
    """Report CPU, memory, disk, battery, network and uptime of this PC, plus the top processes."""
    data = vitals()
    data["os"] = f"{platform.system()} {platform.release()}"
    data["cpu_cores"] = psutil.cpu_count()
    data["top_processes"] = top_processes(5)
    return data


def top_processes(n: int = 8, sort: str = "memory") -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            procs.append({"pid": p.info["pid"], "name": p.info["name"], "mem_mb": round(mem / 2**20),
                          "cpu": p.info["cpu_percent"] or 0.0})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "cpu" if sort == "cpu" else "mem_mb"
    return sorted(procs, key=lambda x: x[key], reverse=True)[:n]


@tool(category="system")
def list_processes(sort: Literal["memory", "cpu"] = "memory", limit: int = 10) -> list[dict]:
    """List the heaviest running processes.

    Args:
        sort: Order by "memory" or "cpu".
        limit: How many to return (max 30).
    """
    return top_processes(min(max(limit, 1), 30), sort)


@tool(risk="danger", category="system", summary=lambda a: f"Force-close every process named “{a.get('name')}”")
def kill_process(name: str) -> str:
    """Force-close all processes whose name matches (e.g. "chrome.exe" or "spotify").

    Args:
        name: Process name, with or without .exe.
    """
    target = name.lower().removesuffix(".exe")
    killed = 0
    for p in psutil.process_iter(["name"]):
        pname = (p.info["name"] or "").lower().removesuffix(".exe")
        if pname == target:
            try:
                p.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    if not killed:
        raise ToolError(f"No running process named {name}.")
    return f"Closed {killed} process(es) named {name}."


# ── Volume ──────────────────────────────────────────────────────────────────
def _win_endpoint():
    from pycaw.pycaw import AudioUtilities  # type: ignore

    speakers = AudioUtilities.GetSpeakers()
    ep = getattr(speakers, "EndpointVolume", None)
    if ep is not None:
        return ep
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL  # type: ignore
    from pycaw.pycaw import IAudioEndpointVolume  # type: ignore

    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume() -> int | None:
    try:
        if IS_WIN:
            return round(_win_endpoint().GetMasterVolumeLevelScalar() * 100)
        if IS_MAC:
            return int(run(["osascript", "-e", "output volume of (get volume settings)"]).stdout.strip())
        if IS_LINUX and shutil.which("pactl"):
            out = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]).stdout
            import re

            m = re.search(r"(\d+)%", out)
            return int(m.group(1)) if m else None
    except Exception:
        return None
    return None


@tool(category="system", summary=lambda a: f"Set volume to {a.get('level')}%")
def set_volume(level: int) -> str:
    """Set the master speaker volume.

    Args:
        level: Volume percentage 0-100.
    """
    level = max(0, min(100, int(level)))
    if IS_WIN:
        try:
            ep = _win_endpoint()
        except Exception as exc:
            # Don't fake it with key presses: report the real problem (verified on a speaker-less Windows VM).
            log.warning("Speaker endpoint unavailable: %s", exc)
            raise ToolError("I can't reach the speakers — no audio output device seems to be connected or enabled.") from exc
        ep.SetMute(0, None)
        ep.SetMasterVolumeLevelScalar(level / 100, None)
        actual = round(ep.GetMasterVolumeLevelScalar() * 100)
        return f"Volume set to {actual}%."
    if IS_MAC:
        run(["osascript", "-e", f"set volume output volume {level}"])
    elif shutil.which("pactl"):
        run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
    else:
        raise ToolError("No volume control available on this system.")
    return f"Volume set to {level}%."


@tool(category="system", summary=lambda a: f"Change volume by {a.get('delta')}")
def change_volume(delta: int) -> str:
    """Raise or lower the volume relative to the current level.

    Args:
        delta: Percentage points to add (negative to lower), e.g. 10 or -20.
    """
    cur = get_volume()
    if cur is None:
        steps = max(1, abs(int(delta)) // 2)
        for _ in range(steps):
            _app_command("volume_up" if delta > 0 else "volume_down")
        return f"Turned the volume {'up' if delta > 0 else 'down'} (couldn't read the exact level)."
    return set_volume(cur + int(delta))


@tool(category="system")
def mute(state: Literal["toggle", "on", "off"] = "toggle") -> str:
    """Mute or unmute the speakers.

    Args:
        state: "on" mutes, "off" unmutes, "toggle" flips.
    """
    if IS_WIN:
        try:
            ep = _win_endpoint()
            cur = bool(ep.GetMute())
            new = (not cur) if state == "toggle" else state == "on"
            ep.SetMute(int(new), None)
            return "Muted." if new else "Unmuted."
        except Exception:
            _app_command("volume_mute")
            return "Toggled mute."
    if IS_MAC:
        val = {"on": "true", "off": "false"}.get(state, "not (output muted of (get volume settings))")
        run(["osascript", "-e", f"set volume output muted {val}"])
        return "Done."
    if shutil.which("pactl"):
        run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", {"on": "1", "off": "0"}.get(state, "toggle")])
        return "Done."
    raise ToolError("No mute control available.")


@tool(category="system", summary=lambda a: f"Set brightness to {a.get('level')}%")
def set_brightness(level: int) -> str:
    """Set the display brightness (laptops and DDC-capable monitors).

    Args:
        level: Brightness percentage 0-100.
    """
    level = max(0, min(100, int(level)))
    try:
        import screen_brightness_control as sbc  # type: ignore

        sbc.set_brightness(level)
        return f"Brightness set to {level}%."
    except ImportError:
        pass
    except Exception as exc:
        raise ToolError(f"This display does not allow brightness control ({exc}).") from exc
    if IS_WIN:
        powershell(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})")
        return f"Brightness set to {level}%."
    if shutil.which("brightnessctl"):
        run(["brightnessctl", "set", f"{level}%"])
        return f"Brightness set to {level}%."
    raise ToolError("Brightness control is not available on this display.")


@tool(category="media")
def media_control(action: Literal["play_pause", "next", "previous", "stop"]) -> str:
    """Control whatever media is playing (Spotify, YouTube, etc.) with the media keys.

    Args:
        action: play_pause, next, previous or stop.
    """
    if IS_LINUX and shutil.which("playerctl"):
        run(["playerctl", {"play_pause": "play-pause", "previous": "previous"}.get(action, action)])
        return "Done."
    if IS_WIN:
        _app_command(action)
        return f"Media: {action.replace('_', '/')}."
    import pyautogui

    key = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack", "stop": "stop"}[action]
    pyautogui.press(key)
    return f"Media: {action.replace('_', '/')}."


# WM_APPCOMMAND codes. Sent as a window message to the shell, the same command a media key produces, but
# without injecting a key press: nothing reaches the focused window and the mouse and focus stay put.
APPCOMMANDS = {"volume_mute": 8, "volume_down": 9, "volume_up": 10, "next": 11, "previous": 12, "stop": 13,
               "play_pause": 14}


def _app_command(action: str) -> None:
    if not IS_WIN:
        import pyautogui

        pyautogui.press({"volume_mute": "volumemute", "volume_down": "volumedown", "volume_up": "volumeup"}[action])
        return
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    shell = user32.FindWindowW("Shell_TrayWnd", None)
    if not shell:
        raise ToolError("The Windows shell (taskbar) isn't running, so media commands can't be sent.")
    res = ctypes.c_ulong()
    user32.SendMessageTimeoutW(shell, 0x0319, shell, APPCOMMANDS[action] << 16, 0x2, 2000, ctypes.byref(res))


# ── Power ───────────────────────────────────────────────────────────────────
@tool(risk="confirm", category="system", summary=lambda a: "Lock the PC")
def lock_pc() -> str:
    """Lock the computer (requires password to return)."""
    if IS_WIN:
        import ctypes

        ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
    elif IS_MAC:
        run(["pmset", "displaysleepnow"])
    else:
        run(["loginctl", "lock-session"])
    return "Locked."


@tool(risk="danger", category="system", summary=lambda a: f"Power: {a.get('action')} the PC")
def power(action: Literal["sleep", "shutdown", "restart", "sign_out"], delay_seconds: int = 10) -> str:
    """Sleep, shut down, restart or sign out of the computer.

    Args:
        action: sleep, shutdown, restart or sign_out.
        delay_seconds: Seconds before shutdown/restart so the user can cancel.
    """
    d = str(max(0, int(delay_seconds)))
    if IS_WIN:
        cmds = {
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            "shutdown": ["shutdown", "/s", "/t", d],
            "restart": ["shutdown", "/r", "/t", d],
            "sign_out": ["shutdown", "/l"],
        }
    elif IS_MAC:
        cmds = {
            "sleep": ["pmset", "sleepnow"],
            "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
            "restart": ["osascript", "-e", 'tell app "System Events" to restart'],
            "sign_out": ["osascript", "-e", 'tell app "System Events" to log out'],
        }
    else:
        cmds = {
            "sleep": ["systemctl", "suspend"],
            "shutdown": ["shutdown", "-h", f"+{max(1, int(delay_seconds) // 60)}"],
            "restart": ["shutdown", "-r", f"+{max(1, int(delay_seconds) // 60)}"],
            "sign_out": ["loginctl", "terminate-user", ""],
        }
    run(cmds[action])
    return f"{action.replace('_', ' ').title()} initiated."


@tool(category="system")
def cancel_shutdown() -> str:
    """Cancel a pending shutdown or restart."""
    run(["shutdown", "/a"] if IS_WIN else ["shutdown", "-c"])
    return "Pending shutdown cancelled."


# ── Clipboard & notifications ───────────────────────────────────────────────
@tool(category="system")
def read_clipboard() -> str:
    """Read the text currently on the clipboard."""
    import pyperclip

    text = pyperclip.paste() or ""
    return text if text else "(clipboard is empty)"


@tool(category="system", summary=lambda a: "Copy text to the clipboard")
def write_clipboard(text: str) -> str:
    """Put text on the clipboard so the user can paste it.

    Args:
        text: The text to copy.
    """
    import pyperclip

    pyperclip.copy(text)
    return "Copied to clipboard."


@tool(category="system")
def notify(title: str, message: str, ctx: ToolContext) -> str:
    """Show a desktop notification.

    Args:
        title: Notification title.
        message: Body text.
    """
    if ctx.engine is not None:
        ctx.engine.notify(title, message)
    return "Notification shown."
