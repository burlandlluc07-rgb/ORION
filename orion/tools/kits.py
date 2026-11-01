"""Toolkits: groups of specialised tools that are only sent to Gemini when a request needs them.

Every tool declaration costs prompt tokens on every request, and function-calling accuracy drops as
the list grows. Core tools (apps, files, web, system basics…) are always available; a toolkit is
loaded when the request mentions its subject, or when the model calls ``open_toolkit`` mid-task, and
stays loaded for the rest of the conversation.
"""

from __future__ import annotations

import re

KITS: dict[str, str] = {
    "documents": "Word, Excel and PowerPoint files, PDFs (create, merge, split, forms), images (resize, convert), "
                 "zip archives, downloads and searching inside files",
    "browser": "drive a real web browser: open sites, read pages, click, type, fill forms, log-in sessions",
    "email": "the user's mailbox: inbox, sort by importance, search, read, send, reply, forward, attachments, "
             "archive, folders, contacts",
    "windows": "Windows settings and maintenance: Wi-Fi, Bluetooth, dark mode, wallpaper, install/uninstall apps, "
               "startup apps, network and speed test, Windows Update, window layouts, cleanup, power plans",
    "home": "smart-home devices, scenes, locks and alarms",
    "whatsapp": "WhatsApp messages: a typed-in draft in the user's own WhatsApp (whatsapp_draft), sends from the user's "
                "own number or a business number that the user approves first, reading recent personal chats "
                "(whatsapp_read, when linked), plus WhatsApp contacts and templates",
}

_PATTERNS: dict[str, re.Pattern] = {
    "documents": re.compile(
        r"\b(word|docx?|documents?|spreadsheets?|excel|xlsx|csv|sheets?|workbook|presentations?|powerpoint|pptx|"
        r"slides?|deck|pdfs?|reports?|invoices?|tables?|charts?|graphs?|resumes?|cv|letters?|images?|photos?|pictures?|"
        r"jpe?g|png|webp|heic|resize|convert|compress|zip|unzip|archive|extract|download|form fields?|merge|"
        r"split|watermark|contents? of)\b", re.I),
    "browser": re.compile(
        r"\b(browser|website|web ?site|web ?page|sign ?(in|up)|log ?in|fill (in|out)|forms?|checkout|cart|"
        r"book (a|me|my)|reserve|order|buy|purchase|navigate|click|tabs?|amazon|ebay|booking|airbnb|"
        r"go to \S+\.\w{2,})\b", re.I),
    "email": re.compile(r"\b(e-?mails?|mail|inbox|unread|reply|forward|attachments?|cc|newsletter|sender)\b", re.I),
    "windows": re.compile(
        r"\b(wi-?fi|wireless|bluetooth|dark mode|light mode|theme|wallpaper|background|install|uninstall|"
        r"winget|update|upgrade|startup|boot|network|internet speed|speed ?test|ping|ip address|router|"
        r"snap|arrange|side by side|half of the screen|left half|right half|recycle bin|temp files|clean ?up|"
        r"junk|power plan|battery saver|performance mode)\b", re.I),
    "whatsapp": re.compile(r"\bwhats ?app\b", re.I),
    "home": re.compile(r"\b(lights?|lamps?|scenes?|thermostat|heating|locks?|alarm|smart plug|bulbs?|home)\b", re.I),
}


def select_kits(text: str) -> set[str]:
    """Toolkits a request obviously needs, judged from its wording."""
    return {name for name, pat in _PATTERNS.items() if pat.search(text or "")}


def open_toolkit_declaration(available: list[str]) -> dict:
    lines = "; ".join(f"{k} — {KITS[k]}" for k in available)
    return {
        "name": "open_toolkit",
        "description": "Load a toolkit of extra tools when the task needs one you don't have yet. Toolkits: " + lines,
        "parameters_json_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": available}},
            "required": ["name"],
        },
    }
