"""Local fast path.

Common commands ("open spotify", "volume 30", "next song", "timer 5 minutes")
are recognised with patterns and executed directly: zero Gemini requests,
~instant response, and the free-tier quota is saved for real conversations.

Each rule maps an utterance to (tool name, args, reply template). Anything
ambiguous falls through to Gemini — when in doubt we do NOT match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

WAKE = re.compile(r"^\s*(?:hey\s+|ok\s+|okay\s+)?(?:orion|jarvis)[\s,!.:]*", re.I)
POLITE = re.compile(r"^(?:(?:please|can you|could you|would you|will you|i want you to|go ahead and)\s+)+", re.I)
TRAILING = re.compile(r"[\s,]*(?:please|for me|now|thanks|thank you|right now)?[\s.!?]*$", re.I)


@dataclass
class Route:
    tool: str
    args: dict
    reply: Callable[[str], str] | str  # template or function of the tool result


def normalise(text: str) -> str:
    t = WAKE.sub("", text.strip())
    t = POLITE.sub("", t)
    t = TRAILING.sub("", t)
    return t.strip()


def _num(s: str) -> int:
    words = {"zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
             "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "max": 100, "maximum": 100, "full": 100,
             "half": 50, "min": 0, "minimum": 0}
    s = s.strip().lower().rstrip("%").removesuffix(" percent").strip()
    return int(s) if s.isdigit() else words[s]


_NUM = r"(\d{1,3}|zero|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|max(?:imum)?|full|half|min(?:imum)?)\s*(?:%|percent)?"
_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")
_NOT_APPS = re.compile(r"\b(file|folder|document|the|my|a|an|it|that|this|page|tab|link|and|then|with|to|in|on|for)\b|[,;]", re.I)


def match(text: str) -> Route | None:
    t = normalise(text)
    low = t.lower()
    if not low or len(low) > 80:
        return None

    # ── Time & date ─────────────────────────────────────────────────────────
    if re.fullmatch(r"(what(?:'s| is)? the )?(current )?time( is it)?( now)?|what time is it", low):
        return Route("get_time_and_date", {}, lambda r: "It's " + r.split("—")[1].strip()[:5] + ".")
    if re.fullmatch(r"(what(?:'s| is)? )?(today'?s? )?(the )?(date|day)( today| is it( today)?)?|what day is (it|today)", low):
        return Route("get_time_and_date", {}, lambda r: "Today is " + r.split("—")[0].strip() + ".")

    # ── Volume / brightness ─────────────────────────────────────────────────
    m = re.fullmatch(rf"(?:set |change |turn )?(?:the )?(?:volume|sound)(?: level)? (?:to |at )?{_NUM}", low) \
        or re.fullmatch(rf"(?:set |turn )?(?:the )?volume (?:up |down )?to {_NUM}", low)
    if m:
        return Route("set_volume", {"level": _num(m.group(1))}, lambda r: r)
    if re.fullmatch(r"(turn |crank )?(the )?(volume|sound|it) up( a bit| a little)?|louder|volume up", low):
        return Route("change_volume", {"delta": 10}, "Volume up.")
    if re.fullmatch(r"(turn )?(the )?(volume|sound|it) down( a bit| a little)?|quieter|volume down", low):
        return Route("change_volume", {"delta": -10}, "Volume down.")
    if re.fullmatch(r"(mute|silence)( the)?( sound| volume| audio| pc| computer)?", low):
        return Route("mute", {"state": "on"}, "Muted.")
    if re.fullmatch(r"unmute( the)?( sound| volume| audio| pc| computer)?", low):
        return Route("mute", {"state": "off"}, "Unmuted.")
    m = re.fullmatch(rf"(?:set |change |turn )?(?:the )?(?:screen )?brightness (?:to |at )?{_NUM}", low)
    if m:
        return Route("set_brightness", {"level": _num(m.group(1))}, lambda r: r)

    # ── Media ───────────────────────────────────────────────────────────────
    if re.fullmatch(r"(pause|resume|play|unpause)( the)?( music| song| track| video| media| playback)?|play pause", low):
        return Route("media_control", {"action": "play_pause"}, "Done.")
    if re.fullmatch(r"(next|skip)( this)?( song| track| one| video)?|skip( it)?", low):
        return Route("media_control", {"action": "next"}, "Skipping.")
    if re.fullmatch(r"(previous|last|go back)( song| track| one| video)?|play( the)? previous( song| track)?", low):
        return Route("media_control", {"action": "previous"}, "Going back.")
    m = re.fullmatch(r"play (.+?) on youtube", low) or re.fullmatch(r"youtube (.+)", low)
    if m:
        return Route("play_youtube", {"query": m.group(1)}, lambda r: r)
    m = re.fullmatch(r"play (.+?) on spotify", low)
    if m:
        return Route("play_spotify", {"query": m.group(1)}, lambda r: r)

    # ── Web ─────────────────────────────────────────────────────────────────
    m = re.fullmatch(r"(?:google|search google for|search for|search the web for|look up) (.+)", low)
    if m and len(m.group(1)) < 60:
        return Route("search_in_browser", {"query": m.group(1), "site": "google"}, lambda r: r)
    m = re.fullmatch(r"search youtube for (.+)", low)
    if m:
        return Route("search_in_browser", {"query": m.group(1), "site": "youtube"}, lambda r: r)

    # ── Open / close ────────────────────────────────────────────────────────
    m = re.fullmatch(r"(?:open|launch|start|run|fire up|bring up)(?: up)? (?:the )?(.+?)(?: app| application| program)?", t, re.I)
    if m:
        target = m.group(1).strip()
        if _DOMAIN.match(target.lower()):
            return Route("open_website", {"url": target}, lambda r: r)
        if target.lower() in ("downloads", "documents", "desktop", "pictures", "music", "videos",
                              "my downloads", "my documents", "downloads folder", "documents folder"):
            name = target.lower().replace("my ", "").replace(" folder", "")
            return Route("open_path", {"path": name}, f"Opening {name}.")
        if len(target.split()) <= 4 and not _NOT_APPS.search(target):
            return Route("open_app", {"name": target}, lambda r: r)
    m = re.fullmatch(r"(?:go to|navigate to|open website) (\S+\.\S+)", low)
    if m:
        return Route("open_website", {"url": m.group(1)}, lambda r: r)
    m = re.fullmatch(r"(?:close|quit|exit|kill) (?:the )?(.+?)(?: app| application| program)?", low)
    if m and len(m.group(1).split()) <= 3 and not _NOT_APPS.search(m.group(1)) and m.group(1) not in ("orion", "jarvis"):
        return Route("close_app", {"name": m.group(1)}, lambda r: r)

    # ── System ──────────────────────────────────────────────────────────────
    if re.fullmatch(r"lock( the| my)? (pc|computer|screen|laptop)|lock it", low):
        return Route("lock_pc", {}, "Locking.")
    if re.fullmatch(r"(take|grab|capture) (a )?screenshot|screenshot", low):
        return Route("take_screenshot", {}, lambda r: r)
    if re.fullmatch(r"(system|pc|computer) (status|stats|health|report)|how(?:'s| is) my (pc|computer|system)( doing)?", low):
        return Route("system_status", {}, _format_status)

    if re.fullmatch(r"undo( that| it| this| the last (change|thing|action)| what you (just )?did)?", low):
        return Route("undo_last_change", {}, lambda r: r)

    # ── Timers & reminders ──────────────────────────────────────────────────
    m = re.fullmatch(r"(?:set |start )?(?:a )?timer (?:for )?(.+)", low) or re.fullmatch(r"(.+?) timer", low)
    if m and re.search(r"\d|one|two|three|five|ten", m.group(1)):
        dur = _words_to_digits(m.group(1))
        if re.search(r"\d", dur):
            return Route("set_timer", {"duration": dur, "label": "Timer"}, lambda r: r.split(" (id")[0] + ".")
    m = re.fullmatch(r"remind me (?:to )?(.+?) (in \d.+|at \d.+|tomorrow.*)", low)
    if m:
        return Route("set_reminder", {"message": m.group(1), "when": _words_to_digits(m.group(2))},
                     lambda r: r.split(" (id")[0] + ".")
    m = re.fullmatch(r"remind me (in (?:\d+(?:\.\d+)?\s*[a-z]+\s*)+|at \d{1,2}(?::\d{2})?\s*(?:am|pm)?|tomorrow(?: at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)?) (?:to )?(.+)", low)
    if m:
        return Route("set_reminder", {"message": m.group(2), "when": _words_to_digits(m.group(1).strip())},
                     lambda r: r.split(" (id")[0] + ".")
    return None


def _words_to_digits(s: str) -> str:
    table = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
             "eight": "8", "nine": "9", "ten": "10", "fifteen": "15", "twenty": "20", "thirty": "30",
             "forty": "40", "forty-five": "45", "sixty": "60", "a": "1", "an": "1", "half an": "0.5"}
    s = re.sub(r"\bhalf an hour\b", "30 minutes", s)
    return re.sub(r"\b(forty-five|half an|fifteen|twenty|thirty|forty|sixty|one|two|three|four|five|six|seven|eight|nine|ten|an|a)\b(?= (?:sec|min|hour|day))",
                  lambda m: table[m.group(1)], s)


def _format_status(raw: str) -> str:
    import json

    try:
        d = json.loads(raw)
    except ValueError:
        return raw
    parts = [f"CPU at {d['cpu']:.0f}%", f"memory {d['ram']:.0f}% used ({d['ram_used_gb']} of {d['ram_total_gb']} GB)",
             f"{d['disk_free_gb']} GB free on the system drive"]
    if "battery" in d:
        parts.append(f"battery {d['battery']}%{' and charging' if d.get('plugged') else ''}")
    top = d.get("top_processes") or []
    tail = f" Heaviest app: {top[0]['name']} ({top[0]['mem_mb']} MB)." if top else ""
    return "All systems nominal: " + ", ".join(parts) + "." + tail
