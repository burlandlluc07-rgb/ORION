"""Check ORION's wake-word detector on real Windows speech: Windows' own voice says phrases into WAV files.

Usage (Windows):  python scripts/wake_check.py      → prints one JSON line per case and exits 1 on any failure.
The Windows e2e runs the same cases against the installed build.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orion.voice.commands import ALWAYS_APPS, build_catalog  # noqa: E402
from orion.voice.wake import SpeechService, detect_in_wav, threshold_for  # noqa: E402

PHRASES = ["Hey ORION", "Friday", "Hey Jarvis"]
POSITIVE = {
    "alone": ("Hey Orion.", "Hey Orion"),
    "then_command": ("Hey Orion, what is the weather like today?", "Hey Orion"),
    "friday": ("Friday, open my email please.", "Friday"),
    "jarvis": ("Hey Jarvis, set a timer for five minutes.", "Hey Jarvis"),
}
NEGATIVE = {
    "pizza": "Let us order a pizza and watch a movie tonight.",
    "mention": "I watched a documentary about the Orion constellation last night.",
}
# Known limitation, reported but not failed: one-word phrases can match similar-sounding speech. That's why the
# defaults are two-word phrases and Settings suggests "Hey <name>" when a single word is added.
KNOWN_LIMITS = {"fried_day": "It was a fried egg kind of day."}


def say(text: str, path: Path) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"], check=True, timeout=60)


def run_cases(phrases: list[str] = PHRASES, sensitivity: float = 0.5) -> list[dict]:
    threshold = threshold_for(sensitivity)
    tmp = Path(tempfile.mkdtemp(prefix="orion-wake-"))
    out = []
    for name, (text, want) in POSITIVE.items():
        wav = tmp / f"{name}.wav"
        say(text, wav)
        res = detect_in_wav(str(wav), phrases)
        ok = any(h["phrase"] == want and h["confidence"] >= threshold for h in res["hits"])
        out.append({"case": name, "expect": "wake", "ok": ok, **res})
    for name, text in NEGATIVE.items():
        wav = tmp / f"{name}.wav"
        say(text, wav)
        res = detect_in_wav(str(wav), phrases)
        ok = not any(h["confidence"] >= threshold for h in res["hits"]) and not res["error"]
        out.append({"case": name, "expect": "ignore", "ok": ok, **res})
    for name, text in KNOWN_LIMITS.items():
        wav = tmp / f"{name}.wav"
        say(text, wav)
        res = detect_in_wav(str(wav), phrases)
        woke = any(h["confidence"] >= threshold for h in res["hits"])
        out.append({"case": name, "expect": "known limit (single-word phrase)", "ok": True, "woke": woke, **res})
    return out


# Wake phrase + instant command in one breath: the command must be recognised exactly, and ordinary questions
# must NOT be mistaken for a command (they go to Gemini).
WAKE_COMMANDS = {
    "pause": ("Hey Orion, pause the music.", "pause"),
    "volume": ("Hey Orion, set the volume to thirty.", "volume 30"),
    "timer": ("Hey Orion, set a timer for five minutes.", "timer 5 minutes"),
    "question": ("Hey Orion, what is the weather like in Paris today?", None),
}
COMMAND_CONFIDENCE = 0.8


def run_command_cases() -> list[dict]:
    catalog = build_catalog(ALWAYS_APPS)
    tmp = Path(tempfile.mkdtemp(prefix="orion-cmd-"))
    out = []
    for name, (text, want) in WAKE_COMMANDS.items():
        wav = tmp / f"{name}.wav"
        say(text, wav)
        res = detect_in_wav(str(wav), ["Hey ORION"], catalog=catalog)
        woke = [h for h in res["hits"] + res["commands"] if h["confidence"] >= threshold_for(0.5)]
        got = next((h["command"] for h in res["commands"] if h["command"] and h["confidence"] >= COMMAND_CONFIDENCE),
                   None)
        ok = bool(woke) and got == want
        out.append({"case": f"wake+{name}", "expect": want, "got": got, "ok": ok, "catalog": len(catalog), **res})

    # The persistent service: command check on a recorded utterance, offline dictation, offline voice.
    sp = SpeechService([], catalog, listen=False)
    started = sp.start(90)
    out.append({"case": "service_start", "ok": started and not sp.fatal, "language": sp.language, "fatal": sp.fatal})
    if not started:
        return out
    try:
        for name, text, want in (("rec_open", "Open notepad.", "open notepad"),
                                 ("rec_lock", "Lock the computer.", "lock the pc"),
                                 ("rec_question", "What is the weather like in Paris today?", None)):
            wav = tmp / f"{name}.wav"
            say(text, wav)
            t0 = time.perf_counter()
            command, conf, said = sp.match_command(wav)
            ms = round((time.perf_counter() - t0) * 1000)
            got = command if conf >= COMMAND_CONFIDENCE else None
            out.append({"case": name, "expect": want, "got": got, "conf": conf, "said": said, "ms": ms,
                        "ok": got == want})
        wav = tmp / "rec_question.wav"
        t0 = time.perf_counter()
        text, conf = sp.dictate(wav)
        out.append({"case": "dictation", "text": text, "conf": conf, "ms": round((time.perf_counter() - t0) * 1000),
                    "ok": "weather" in text.lower()})
        t0 = time.perf_counter()
        pcm = sp.synthesize("Good evening. All systems are running normally.", "en-GB", "male", 1)
        out.append({"case": "offline_voice", "seconds": round(len(pcm) / 2 / 24000, 2),
                    "ms": round((time.perf_counter() - t0) * 1000), "ok": len(pcm) / 2 / 24000 > 1.0})
    finally:
        sp.stop()
    return out


def run_context_case() -> list[dict]:
    """Select text in Notepad, then ask ORION what's selected (UI Automation, no keystrokes from ORION)."""
    from pywinauto import Application  # type: ignore

    from orion.tools import context

    app = Application(backend="uia").start("notepad.exe")
    try:
        time.sleep(2)
        win = app.top_window()
        win.set_focus()
        win.type_keys("The quick brown fox jumps over the lazy dog.", with_spaces=True)
        win.type_keys("^a")
        time.sleep(0.5)
        snap = context.snapshot()
        ok = "quick brown fox" in snap["selection"] and "notepad" in (snap["app"] or "").lower()
        ctx = context.context_for("summarise this")
        errors: list = []
        if not ok:
            try:
                context.read_selection(context.FOREGROUND.hwnd, errors)
            except Exception as exc:
                import traceback

                errors.append(traceback.format_exc()[-800:] + str(exc))
        return [{"case": "context_selection", "ok": ok and "quick brown fox" in ctx, "errors": errors, **snap}]
    finally:
        try:
            app.kill()
        except Exception:
            pass


if __name__ == "__main__":
    results = run_cases()
    if "--wake-only" not in sys.argv:
        results += run_command_cases() + run_context_case()
    for r in results:
        print(json.dumps(r))
    print(f"{sum(r['ok'] for r in results)}/{len(results)} speech cases passed")
    sys.exit(0 if all(r["ok"] for r in results) else 1)
