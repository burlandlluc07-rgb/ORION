"""Instant voice commands: the spoken phrases Windows' offline recogniser listens for.

Each spoken phrase maps to the canonical text the local fast path (brain/router.py) understands, so
"Hey ORION, set the volume to thirty five" runs ``volume 35`` straight away: no transcription, no Gemini
request, no quota. Anything that isn't in this catalog goes the normal way (Gemini transcription).

Phrases are written as words (numbers spelled out) because that's how the recogniser matches speech.
"""

from __future__ import annotations

import re

from ..brain import router

STOP = "__stop__"  # special: stop talking / cancel what's running

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

ALWAYS_APPS = ["notepad", "calculator", "settings", "file explorer", "task manager", "paint", "microsoft edge", "edge",
               "chrome", "google chrome", "spotify", "word", "excel", "powerpoint", "outlook", "teams", "discord",
               "steam", "whatsapp", "obsidian", "visual studio code", "control panel", "camera", "photos", "clock"]
APP_ALIASES = {"file explorer": "explorer"}  # the router treats "file" as "not an app name"
FOLDERS = {"downloads": "downloads", "my downloads": "downloads", "documents": "documents", "my documents": "documents",
           "desktop": "desktop", "pictures": "pictures", "my pictures": "pictures", "music": "music", "videos": "videos"}


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    return "one hundred"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", text.lower())).strip()


def speakable_app(name: str) -> str | None:
    """An installed app's name as people say it ("Microsoft Edge"), or None if it can't be said reliably."""
    n = re.sub(r"\(.*?\)|\b(x64|x86|64-bit|32-bit)\b", " ", name)
    if re.search(r"\d", n):
        return None  # "7-Zip" can't be matched reliably once the digits are gone
    n = n.replace("&", " and ").replace("+", " plus ")
    n = re.sub(r"[^A-Za-z' ]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip().lower()
    if not n or len(n) < 3 or len(n.split()) > 4 or re.search(r"\b(uninstall|update|helper|setup|readme)\b", n):
        return None
    return n


def build_catalog(apps: list[str] | None = None, smart_home: bool = False) -> dict[str, str]:
    """spoken phrase → canonical fast-path text."""
    c: dict[str, str] = {}

    def add(spoken: str, canonical: str) -> None:
        c[_norm(spoken)] = canonical

    for p in ("what time is it", "what's the time", "what is the time", "tell me the time"):
        add(p, "what time is it")
    for p in ("what's the date", "what is the date", "what day is it", "what's today's date"):
        add(p, "what's the date")
    for n in range(0, 101):
        w = number_words(n)
        for p in (f"volume {w}", f"set the volume to {w}", f"set volume to {w}", f"volume to {w}",
                  f"volume {w} percent", f"set the volume to {w} percent"):
            add(p, f"volume {n}")
        for p in (f"brightness {w}", f"set the brightness to {w}", f"brightness {w} percent",
                  f"set the brightness to {w} percent"):
            add(p, f"brightness {n}")
    for p in ("volume up", "turn it up", "turn the volume up", "louder"):
        add(p, "volume up")
    for p in ("volume down", "turn it down", "turn the volume down", "quieter"):
        add(p, "volume down")
    for p in ("mute", "mute the sound", "mute the volume"):
        add(p, "mute")
    for p in ("unmute", "unmute the sound"):
        add(p, "unmute")
    for p in ("pause", "pause the music", "pause the video", "play", "play the music", "resume", "resume the music",
              "resume playback"):
        add(p, "pause")
    for p in ("next", "next song", "next track", "skip", "skip this song", "skip this one", "skip the song"):
        add(p, "next song")
    for p in ("previous song", "previous track", "last song", "go back a song", "play the previous song"):
        add(p, "previous song")
    for p in ("lock the computer", "lock my computer", "lock the pc", "lock my pc", "lock the screen", "lock my laptop"):
        add(p, "lock the pc")
    for p in ("take a screenshot", "screenshot", "grab a screenshot"):
        add(p, "take a screenshot")
    for p in ("system status", "how's my pc doing", "how is my computer doing", "how's my computer doing"):
        add(p, "system status")
    for p in ("undo that", "undo", "undo the last change"):
        add(p, "undo that")
    for p in ("stop", "stop talking", "be quiet", "cancel", "cancel that", "never mind", "shut up", "that's enough"):
        add(p, STOP)
    for n in list(range(1, 61)):
        w = number_words(n)
        unit = "minute" if n == 1 else "minutes"
        for p in (f"set a timer for {w} {unit}", f"timer for {w} {unit}", f"{w} minute timer", f"start a {w} minute timer"):
            add(p, f"timer {n} {unit}")
    for n in (10, 15, 20, 30, 45, 90):
        add(f"set a timer for {number_words(n)} seconds", f"timer {n} seconds")
    for n in range(1, 6):
        unit = "hour" if n == 1 else "hours"
        add(f"set a timer for {number_words(n)} {unit}", f"timer {n} {unit}")
    add("set a timer for half an hour", "timer 30 minutes")
    for folder, canonical in FOLDERS.items():
        add(f"open {folder}", f"open {canonical}")
        add(f"open the {canonical} folder", f"open {canonical}")
    names = list(dict.fromkeys(ALWAYS_APPS + [s for s in (speakable_app(a) for a in (apps or [])) if s]))[:400]
    for app in names:
        if app in FOLDERS:
            continue
        target = APP_ALIASES.get(app, app)
        if router.match(f"open {target}") is None:
            continue
        for verb in ("open", "launch", "start"):
            add(f"{verb} {app}", f"open {target}")
        for verb in ("close", "quit"):
            add(f"{verb} {app}", f"close {target}")
    if smart_home:
        for state in ("on", "off"):
            add(f"turn {state} the lights", f"turn {state} the lights")
            add(f"lights {state}", f"turn {state} the lights")
    return c


def lookup(catalog: dict[str, str], text: str, wake_phrases: list[str] | None = None) -> str | None:
    """Recogniser text (optionally starting with a wake phrase) → canonical command, or None."""
    t = _norm(text.replace("...", " "))
    for w in sorted((_norm(p) for p in wake_phrases or []), key=len, reverse=True):
        if t.startswith(w + " ") or t == w:
            t = t[len(w):].strip()
            break
    return catalog.get(t)
