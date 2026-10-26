"""Check offline speech recognition on real Windows: download the model the way Settings does (SHA-256 checked),
then have Windows' own voice say wake phrases, commands and yes/no answers into WAV files and transcribe them.

Usage:  python scripts/offline_stt_check.py [--models DIR]   → one JSON line per case; exit 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orion.tasks.tracker import parse_yes_no  # noqa: E402
from orion.voice.commands import build_catalog, lookup  # noqa: E402
from orion.voice.offline_stt import ModelStore, load, match_wake  # noqa: E402

PHRASES = ["Hey ORION", "Hey Jarvis"]
CASES = [  # (name, what Windows says, check)
    ("wake_question", "Hey Orion, what is the weather like today?", "wake"),
    ("wake_alone", "Hey Orion.", "wake"),
    ("wake_command", "Hey Jarvis, pause the music.", "wake_command"),
    ("mention", "I watched a documentary about the Orion constellation last night.", "no_wake"),
    ("pizza", "Let us order a pizza and watch a movie tonight.", "no_wake"),
    ("yes", "Yes.", "yes"),
    ("yes_go", "Yes, go ahead.", "yes"),
    ("no", "No.", "no"),
    ("no_dont", "No, don't do that.", "no"),
    ("dictation", "Remind me to call my mother at six o'clock.", "text"),
]


def say(text: str, path: Path) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$s.SetOutputToWaveFile('{path}'); $s.Speak(\"{text}\"); $s.Dispose()"], check=True, timeout=60)


def pcm16k(path: Path) -> bytes:
    """Mono 16 kHz 16-bit, as ORION's microphone delivers it."""
    with wave.open(str(path), "rb") as w:
        rate, ch, data = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
    x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return x.astype(np.int16).tobytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=str(Path(tempfile.gettempdir()) / "orion-models"))
    args = ap.parse_args()
    store = ModelStore(Path(args.models))
    t = time.monotonic()
    if not store.installed():
        store.start_download()
        store.wait()
    print(json.dumps({"case": "download", "ok": store.installed(), "seconds": round(time.monotonic() - t, 1),
                      "error": store.error}), flush=True)
    if not store.installed():
        return 1
    t = time.monotonic()
    rec = load(store)
    print(json.dumps({"case": "load", "ok": True, "seconds": round(time.monotonic() - t, 1)}), flush=True)
    catalog = build_catalog([])
    tmp = Path(tempfile.mkdtemp(prefix="orion-stt-"))
    failures = 0
    for name, text, check in CASES:
        wav = tmp / f"{name}.wav"
        say(text, wav)
        pcm = pcm16k(wav)
        t = time.monotonic()
        heard = rec.transcribe(pcm)
        secs = time.monotonic() - t
        wake = match_wake(heard, PHRASES)
        if check == "wake":
            ok = wake is not None
        elif check == "wake_command":
            ok = wake is not None and lookup(catalog, wake[1]) is not None
        elif check == "no_wake":
            ok = wake is None
        elif check in ("yes", "no"):
            ok = parse_yes_no(heard) is (check == "yes")
        else:
            ok = len(heard.split()) >= 4
        failures += not ok
        print(json.dumps({"case": name, "ok": ok, "said": text, "heard": heard, "wake": wake,
                          "audio_s": round(len(pcm) / 32000, 2), "transcribe_s": round(secs, 2)}), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
