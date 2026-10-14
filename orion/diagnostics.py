"""System check: verifies every moving part and says how to fix what's broken."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import time
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .engine import Engine

Check = dict  # {"id", "name", "ok": True|False|None, "detail", "fix"}


def _result(cid: str, name: str, ok: bool | None, detail: str, fix: str = "") -> Check:
    return {"id": cid, "name": name, "ok": ok, "detail": detail, "fix": fix if ok is not True else ""}


async def check_key(e: "Engine") -> Check:
    if not e.config.get_api_key():
        return _result("key", "Gemini API key", False, "No key saved.", "Add a free key in Settings → Brain.")
    try:
        info = await asyncio.wait_for(e.brain.discover(), 20)
    except Exception as exc:
        from .brain.gemini import friendly_error

        return _result("key", "Gemini API key", False, friendly_error(exc), "Check the key in Settings → Brain.")
    missing = [m for m in (e.config.settings.model_fast, e.config.settings.model_smart)
               if not m.endswith("-latest") and m not in info["models"]]
    detail = f"{len(info['models'])} models available"
    if missing:
        return _result("key", "Gemini API key", False, detail + f"; not available: {', '.join(missing)}",
                       "Pick other models in Settings → Brain.")
    return _result("key", "Gemini API key", True, detail)


async def check_generate(e: "Engine") -> Check:
    if not e.config.get_api_key():
        return _result("gen", "Gemini response", None, "Skipped (no key).")
    t0 = time.perf_counter()
    try:
        out = await asyncio.wait_for(e.brain.generate_text("Reply with exactly: OK", tier="fast"), 30)
    except Exception as exc:
        from .brain.gemini import friendly_error

        return _result("gen", "Gemini response", False, friendly_error(exc), "See the message; free-tier limits reset daily.")
    ms = (time.perf_counter() - t0) * 1000
    return _result("gen", "Gemini response", "ok" in out.lower(), f"Answered in {ms:.0f} ms (uses 1 request).")


async def check_live(e: "Engine") -> Check:
    if e.config.settings.voice.mode != "live":
        return _result("live", "Live voice model", None, "Voice mode isn't Live.")
    if e.brain.live_model:
        return _result("live", "Live voice model", True, e.brain.live_model)
    return _result("live", "Live voice model", False, "No model with real-time audio is available for this key.",
                   "Switch Settings → Voice → mode to Classic.")


async def check_mic(e: "Engine") -> Check:
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        return _result("mic", "Microphone", False, f"Audio library unavailable: {exc}",
                       "Reinstall ORION; on Linux install libportaudio2.")

    def probe() -> float:
        rec = sd.rec(int(0.6 * 16000), samplerate=16000, channels=1, dtype="int16",
                     device=e.config.settings.voice.input_device)
        sd.wait()
        return float(np.abs(rec).mean())

    try:
        level = await asyncio.wait_for(asyncio.to_thread(probe), 5)
    except Exception as exc:
        return _result("mic", "Microphone", False, str(exc)[:160],
                       "Plug in a microphone and allow access: Windows Settings → Privacy → Microphone.")
    if level < 1:
        return _result("mic", "Microphone", False, "The microphone records pure silence.",
                       "It may be muted, or Windows privacy settings are blocking desktop apps.")
    return _result("mic", "Microphone", True, f"Recording works (level {level:.0f}).")


async def check_speakers(e: "Engine") -> Check:
    try:
        import sounddevice as sd

        dev = sd.query_devices(e.config.settings.voice.output_device, "output")
        return _result("spk", "Speakers", True, dev["name"])
    except Exception as exc:
        return _result("spk", "Speakers", False, str(exc)[:160], "Connect speakers/headphones or pick a device in Settings → Voice.")


async def check_tts(e: "Engine") -> Check:
    try:
        from .voice.tts import TTS

        class _Null:
            pass

        t0 = time.perf_counter()
        pcm = await asyncio.wait_for(TTS(_Null(), e.config.settings.voice.tts_voice)._synth_edge("System check."), 15)  # type: ignore[arg-type]
        return _result("tts", "Neural voice (edge-tts)", bool(pcm), f"Synthesised in {(time.perf_counter() - t0) * 1000:.0f} ms")
    except Exception as exc:
        return _result("tts", "Neural voice (edge-tts)", False, str(exc)[:160],
                       "Needs internet. Classic mode falls back to the offline Windows voice.")


async def check_search(_: "Engine") -> Check:
    from .tools.web import web_search

    try:
        res = await asyncio.wait_for(asyncio.to_thread(web_search, "weather", 2), 15)
        return _result("search", "Web search", True, f"{len(res)} results")
    except Exception as exc:
        return _result("search", "Web search", False, str(exc)[:160], "Check your internet connection or firewall.")


async def check_apps(_: "Engine") -> Check:
    from .tools.apps import INDEX

    await asyncio.to_thread(INDEX.ready.wait, 15)
    n = len(INDEX.names())
    return _result("apps", "Installed-app index", n > 0, f"{n} apps found",
                   "App discovery failed; “open <app>” may not work. Check logs.")


async def check_extras(_: "Engine") -> list[Check]:
    out = []
    es = shutil.which("es") or shutil.which("es.exe")
    out.append(_result("everything", "Instant file search (Everything)", True if es else None,
                       es or "Not installed — using the slower built-in search.",
                       "Optional: install Everything + es.exe from voidtools.com and add it to PATH."))
    node, uvx = shutil.which("npx"), shutil.which("uvx")
    out.append(_result("mcp", "Connector runtimes", True if (node and uvx) else None,
                       f"npx: {'yes' if node else 'no'} · uvx: {'yes' if uvx else 'no'}",
                       "Optional: install Node.js and/or uv to use connectors from the catalog."))
    ww = importlib.util.find_spec("openwakeword") is not None
    out.append(_result("wake", "Wake word engine", True if ww else None, "openwakeword installed" if ww else "Not installed.",
                       "Optional: `pip install openwakeword` for the offline “Hey Jarvis” wake word."))
    if sys.platform == "win32":
        uia = importlib.util.find_spec("pywinauto") is not None
        out.append(_result("uia", "Windows app control", uia, "UI Automation available" if uia else "pywinauto missing",
                           "Reinstall ORION."))
    return out


async def check_storage(e: "Engine") -> Check:
    try:
        probe = e.config.root / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        free = shutil.disk_usage(e.config.root).free / 2**30
        return _result("data", "Data folder", free > 0.5, f"{e.config.root} · {free:.1f} GB free",
                       "Free up disk space.")
    except OSError as exc:
        return _result("data", "Data folder", False, str(exc), "Make sure ORION can write to its data folder.")


async def run_all(e: "Engine", include_generation: bool = True) -> list[Check]:
    jobs: list[Callable[["Engine"], Awaitable[Check]]] = [check_key, check_storage, check_mic, check_speakers,
                                                         check_tts, check_search, check_apps]
    if include_generation:
        jobs.insert(1, check_generate)
    results = await asyncio.gather(*(j(e) for j in jobs), return_exceptions=True)
    out: list[Check] = []
    for job, r in zip(jobs, results, strict=True):
        if isinstance(r, BaseException):
            out.append(_result(job.__name__, job.__name__.replace("check_", "").title(), False, str(r)[:200]))
        else:
            out.append(r)
    out.insert(2 if include_generation else 1, await check_live(e))  # after discovery has run
    out.extend(await check_extras(e))
    return out
