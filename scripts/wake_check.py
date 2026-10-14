"""Check ORION's wake-word detector on real Windows speech: Windows' own voice says phrases into WAV files.

Usage (Windows):  python scripts/wake_check.py      → prints one JSON line per case and exits 1 on any failure.
The Windows e2e runs the same cases against the installed build.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orion.voice.wake import detect_in_wav, threshold_for  # noqa: E402

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


if __name__ == "__main__":
    results = run_cases()
    for r in results:
        print(json.dumps(r))
    print(f"{sum(r['ok'] for r in results)}/{len(results)} wake cases passed")
    sys.exit(0 if all(r["ok"] for r in results) else 1)
