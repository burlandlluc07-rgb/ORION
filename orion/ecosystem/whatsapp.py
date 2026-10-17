"""WhatsApp through Meta's official WhatsApp Business Cloud API.

Sending is a plain HTTPS call, so it runs in the background: no WhatsApp window, no mouse, no focus.
Every message is a draft until the user approves that exact message: ``whatsapp_send`` always asks
(recipient and full text on the approval card), even when approval prompts are turned off in Settings,
and "allow for this session" never covers it, because a sent message can't be taken back.

What the Cloud API allows (Meta's rules, not ORION's):

* Messages come from the WhatsApp Business number set up in Meta, not from the user's personal WhatsApp.
* Free text only reaches someone who messaged that business number in the last 24 hours. Outside that
  window only a pre-approved message template is delivered (``template=``; ``whatsapp_templates`` lists them).
* A successful call means WhatsApp *accepted* the message. Delivery and read receipts arrive by webhook,
  which ORION doesn't run, so delivery is reported as unconfirmed.

Personal WhatsApp: the Cloud API can't send from a personal account, so ``whatsapp_draft`` opens the user's own
WhatsApp (the desktop app, or WhatsApp Web) on the chat with the message already typed, and the user presses
Send. It opens a visible window (the desktop gate in ``Agent.run_tool`` covers it) but never types or clicks,
and it needs no Meta setup.

``whatsapp_send_personal`` goes one step further for the personal account: it asks first, like every send
(recipient and exact text on the approval card, or a spoken yes/no for voice requests), and only after a yes
opens WhatsApp Desktop on the chat with the message typed in and presses Enter there. That one Enter is a
foreground step, so it is pressed only while WhatsApp is the active window.

Settings live in ``whatsapp.json`` in ORION's data folder; the access token goes into the OS credential
vault (Windows Credential Manager) and is never logged or shown. Phone numbers are masked in labels,
results and logs. ``dry_run`` builds and approves the exact request without contacting Meta.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable

import httpx

from ..tools import platform
from ..tools.registry import Registry, Tool, ToolError, schema_for

log = logging.getLogger("orion.whatsapp")

GRAPH = "https://graph.facebook.com"
API_VERSION = "v21.0"
KEYRING_SERVICE = "ORION"
SECRET_KEY = "whatsapp:token"
SOURCE = "account:whatsapp"
MAX_TEXT = 4096  # the Cloud API's limit for a text message body

# Cloud API error codes worth explaining in plain words (developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes)
ERRORS = {
    190: "the WhatsApp access token has expired or is invalid. Temporary tokens from the API Setup page last "
         "24 hours; create a permanent System User token and paste it in Accounts → WhatsApp.",
    131047: "more than 24 hours have passed since this person last messaged your WhatsApp Business number, so "
            "WhatsApp only allows a pre-approved template. Send with template=… (whatsapp_templates lists them).",
    131030: "this number isn't on the test number's allowed list. While the Meta app is in development, add the "
            "recipient under WhatsApp → API Setup → To (up to 5 numbers).",
    131026: "WhatsApp couldn't deliver to this number: it may not use WhatsApp, or the number is wrong.",
    132001: "that template doesn't exist in that language, or isn't approved yet. whatsapp_templates lists them.",
    132000: "the template's number of {{placeholders}} doesn't match the values given.",
    131056: "too many messages to this person in a short time; wait a little before sending again.",
    130429: "the Cloud API rate limit was hit; try again shortly.",
}


def normalize_number(raw: str) -> str:
    """Digits in international format, without + or spaces (what the Cloud API expects)."""
    s = str(raw or "").strip()
    if s.startswith("00"):
        s = s[2:]
    digits = re.sub(r"[\s().\-+]", "", s)
    if not digits.isdigit():
        raise ToolError("That isn't a phone number.")
    if digits.startswith("0"):
        raise ToolError("Give the number with its country code, e.g. +34 612 345 678.")
    if not 8 <= len(digits) <= 15:
        raise ToolError("That phone number has the wrong length; include the country code.")
    return digits


def desktop_app_installed() -> bool:
    """WhatsApp Desktop registers the whatsapp: link type; without it, wa.me links open WhatsApp Web."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "whatsapp"))
        return True
    except OSError:
        return False


def _foreground_title() -> str:
    if sys.platform != "win32":
        return ""
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def press_send_in_whatsapp(text: str, timeout: float = 20.0) -> str:
    """Wait for WhatsApp Desktop to show the opened chat, then press Enter in it, and only in it.

    Returns how the send was checked: "cleared" when the typed message left the compose box, "pressed" when
    Enter went to WhatsApp but the box couldn't be read. Raises ToolError when WhatsApp never came to the front.
    """
    if sys.platform != "win32":
        raise ToolError("Sending from your own WhatsApp needs WhatsApp Desktop on Windows.")
    from pywinauto import Desktop, keyboard  # type: ignore

    deadline = time.monotonic() + timeout
    win = None
    while time.monotonic() < deadline:
        wins = [w for w in Desktop(backend="uia").windows() if w.window_text().strip().lower().startswith("whatsapp")
                and w.is_visible()]
        if wins:
            win = wins[0]
            break
        time.sleep(0.3)
    if win is None:
        raise ToolError("WhatsApp didn't open, so nothing was sent.")

    def compose_has_text() -> bool | None:
        try:
            for c in win.descendants(control_type="Edit") + win.descendants(control_type="Document"):
                if text[:40] and text[:40] in (c.window_text() or c.legacy_properties().get("Value", "")):
                    return True
            return False
        except Exception:
            return None

    readable = compose_has_text()
    while time.monotonic() < deadline and readable is False:  # the chat and draft are still loading
        time.sleep(0.3)
        readable = compose_has_text()
    time.sleep(0.6 if readable else 2.5)  # can't see the box: give the chat time to load before pressing Enter
    if win.is_minimized():
        win.restore()
    win.set_focus()
    time.sleep(0.3)
    if not _foreground_title().lower().startswith("whatsapp"):  # never press Enter in someone else's window
        raise ToolError("WhatsApp wasn't the active window, so ORION didn't press Send. The message is typed in "
                        "WhatsApp; press Send yourself.")
    keyboard.send_keys("{ENTER}")
    time.sleep(1.0)
    state = compose_has_text()
    if state is True:
        raise ToolError("ORION pressed Send but the message is still in the box, so it may not have gone. Check "
                        "WhatsApp.")
    return "cleared" if state is False else "pressed"


def mask(number: str) -> str:
    """Enough to recognise a number on screen, not enough to leak it: ``+•••••••5678``."""
    d = re.sub(r"\D", "", str(number or ""))
    return f"+{'•' * max(len(d) - 4, 0)}{d[-4:]}" if d else ""


class WhatsApp:
    def __init__(self, root: Path, registry: Registry, on_change: Callable[[], None] = lambda: None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.path = root / "whatsapp.json"
        self.root = root
        self.registry = registry
        self.on_change = on_change
        self.transport = transport  # tests pass an httpx.MockTransport; None = the real network
        self._lock = threading.Lock()
        self.data = self._read()

    # ── persistence ────────────────────────────────────────────────────────
    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        except ValueError:
            return {}

    def _write(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), "utf-8")

    def _set_token(self, token: str) -> None:
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, SECRET_KEY, token)
            return
        except Exception:
            pass
        f = self.root / ".whatsapp_key"
        f.write_text(token, "utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass

    def _token(self) -> str:
        try:
            import keyring

            tok = keyring.get_password(KEYRING_SERVICE, SECRET_KEY)
            if tok:
                return tok
        except Exception:
            pass
        f = self.root / ".whatsapp_key"
        return f.read_text("utf-8").strip() if f.exists() else ""

    def _clear_token(self) -> None:
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, SECRET_KEY)
        except Exception:
            pass
        (self.root / ".whatsapp_key").unlink(missing_ok=True)

    # ── public config API ──────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self.data.get("phone_number_id") or self.data.get("dry_run"))

    def public(self) -> dict:
        """What the settings page shows. Never the token, and numbers only masked."""
        d = self.data
        return {
            "configured": self.configured,
            "dry_run": bool(d.get("dry_run")),
            "phone_number_id": d.get("phone_number_id", ""),
            "business_account_id": d.get("business_account_id", ""),
            "verified_name": d.get("verified_name", ""),
            "display_number": mask(d.get("display_number", "")),
            "has_token": bool(d.get("phone_number_id")) and bool(self._token()),
            "contacts": [{"name": n, "number": mask(v)} for n, v in sorted(d.get("contacts", {}).items())],
        }

    def configure(self, cfg: dict) -> dict:
        """Save the Cloud API settings. A real setup is checked against Meta before it is saved."""
        dry_run = bool(cfg.get("dry_run"))
        pid = re.sub(r"\D", "", str(cfg.get("phone_number_id", "")))
        waba = re.sub(r"\D", "", str(cfg.get("business_account_id", "")))
        token = str(cfg.get("access_token", "")).strip()
        info: dict = {}
        if not dry_run:
            if not pid:
                raise ToolError("Enter the Phone number ID from Meta's WhatsApp → API Setup page.")
            token = token or self._token()
            if not token:
                raise ToolError("Enter the access token.")
            info = self._call("GET", f"/{pid}", token, params={"fields": "display_phone_number,verified_name"})
        with self._lock:
            self.data.update({"dry_run": dry_run, "phone_number_id": pid, "business_account_id": waba,
                              "verified_name": str(info.get("verified_name", "")),
                              "display_number": re.sub(r"\D", "", str(info.get("display_phone_number", "")))})
            self._write()
        if token and not dry_run:
            self._set_token(token)
        log.info("whatsapp: settings saved (%s)", "dry run" if dry_run else "Cloud API checked")
        self.sync_tools()
        return self.public()

    def remove(self) -> None:
        with self._lock:
            contacts = self.data.get("contacts", {})
            self.data = {"contacts": contacts} if contacts else {}
            self._write()
        self._clear_token()
        self.sync_tools()

    def save_contact(self, name: str, number: str) -> dict:
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
        if not name:
            raise ToolError("Give the contact a name.")
        num = normalize_number(number)
        with self._lock:
            self.data.setdefault("contacts", {})[name] = num
            self._write()
        return self.public()

    def remove_contact(self, name: str) -> dict:
        with self._lock:
            contacts = self.data.get("contacts", {})
            for k in [k for k in contacts if k.lower() == str(name).strip().lower()]:
                del contacts[k]
            self._write()
        return self.public()

    # ── tool registration ──────────────────────────────────────────────────
    def sync_tools(self) -> None:
        self.registry.remove_source(SOURCE)
        self._add(self.whatsapp_draft, "confirm", self.describe_draft)
        self._add(self.whatsapp_send_personal, "danger", self.describe_personal, always_ask=True)
        self._add(self.whatsapp_contacts, "safe")
        if self.configured:
            # Sending can't be undone: always ask, and never "allow for this session" (danger).
            self._add(self.whatsapp_send, "danger", self.describe_send, always_ask=True)
            if self.data.get("business_account_id") and not self.data.get("dry_run"):
                self._add(self.whatsapp_templates, "safe")
        self.on_change()

    def _add(self, fn, risk: str, summarize=None, always_ask: bool = False) -> None:
        schema, _, desc = schema_for(fn)
        self.registry.add(Tool(name=fn.__name__, description=desc, parameters=schema, fn=fn, risk=risk,
                               always_ask=always_ask, category="whatsapp", source=SOURCE, summarize=summarize,
                               kit="whatsapp"))

    # ── recipients ─────────────────────────────────────────────────────────
    def resolve(self, to: str) -> tuple[str, str]:
        """(display name, number) for a saved contact name or a phone number."""
        t = str(to or "").strip()
        contacts = self.data.get("contacts", {})
        for name, num in contacts.items():
            if name.lower() == t.lower():
                return name, num
        if re.fullmatch(r"[+\d\s().\-]{6,}", t):
            num = normalize_number(t)
            named = next((n for n, v in contacts.items() if v == num), "")
            return named, num
        names = ", ".join(sorted(contacts)) or "none saved yet"
        raise ToolError(f"No WhatsApp contact called “{t}”. Saved contacts: {names}. Use a saved name or a phone "
                        "number with its country code; contacts are added in Accounts → WhatsApp.")

    def _who(self, to: str) -> str:
        try:
            name, num = self.resolve(to)
            return f"{name} ({mask(num)})" if name else mask(num)
        except ToolError:
            return f"“{to}” (not a saved contact or valid number)"

    def describe_draft(self, args: dict) -> str:
        who = self._who(str(args["to"])) if args.get("to") else "a chat you pick"
        return f"Open WhatsApp with a message to {who}: “{args.get('text', '')}” (you press Send)"

    def describe_personal(self, args: dict) -> str:
        return (f"Send WhatsApp from your own number to {self._who(str(args.get('to', '')))}: "
                f"“{args.get('text', '')}” (ORION opens WhatsApp and presses Send)")

    def describe_send(self, args: dict) -> str:
        """The approval card's headline: who exactly, and exactly what."""
        who = self._who(str(args.get("to", "")))
        if args.get("template"):
            params = args.get("template_params") or []
            what = f"template “{args['template']}” ({args.get('template_language') or 'en_US'})" + (
                f" with {', '.join(map(str, params))}" if params else "")
        else:
            what = f"“{args.get('text', '')}”"
        dry = " [dry run: nothing will be sent]" if self.data.get("dry_run") else ""
        return f"Send WhatsApp to {who}: {what}{dry}"

    # ── Cloud API ──────────────────────────────────────────────────────────
    def _call(self, method: str, path: str, token: str, *, params: dict | None = None,
              body: dict | None = None) -> dict:
        try:
            with httpx.Client(base_url=f"{GRAPH}/{API_VERSION}", timeout=20, transport=self.transport) as c:
                r = c.request(method, path, params=params, json=body, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise ToolError(f"Couldn't reach WhatsApp (graph.facebook.com): {type(exc).__name__}. "
                            "Nothing was sent.") from exc
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or "error" in data:
            err = data.get("error") or {}
            code = err.get("code")
            plain = ERRORS.get(code) or (err.get("error_data") or {}).get("details") or err.get("message") or \
                f"HTTP {r.status_code}"
            log.warning("whatsapp: Cloud API error %s (HTTP %s)", code, r.status_code)
            raise ToolError(f"WhatsApp refused it: {plain}" + (f" (error {code})" if code else ""))
        return data

    def whatsapp_send(self, to: str, text: str = "", template: str = "", template_language: str = "en_US",
                      template_params: list[str] | None = None) -> str:
        """Send a WhatsApp message from the user's WhatsApp Business number (Cloud API, runs in the background).
        The user always sees the recipient and the exact message and must approve it before it is sent, so write
        the final text, not a summary. Free text only reaches people who messaged the business number in the last
        24 hours; otherwise send an approved template (see whatsapp_templates).

        Args:
            to: A saved WhatsApp contact name (see whatsapp_contacts) or a phone number with country code.
            text: The exact message text. Leave empty when sending a template.
            template: Name of an approved message template, for people outside the 24-hour window.
            template_language: The template's language code, e.g. en_US, es_ES.
            template_params: Values for the template body's {{1}}, {{2}}… placeholders, in order.
        """
        name, num = self.resolve(to)
        who = f"{name} ({mask(num)})" if name else mask(num)
        text = str(text or "").strip()
        body: dict = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": num}
        if template:
            tpl: dict = {"name": template.strip(), "language": {"code": template_language or "en_US"}}
            if template_params:
                tpl["components"] = [{"type": "body", "parameters": [{"type": "text", "text": str(p)}
                                                                     for p in template_params]}]
            body.update({"type": "template", "template": tpl})
        elif text:
            if len(text) > MAX_TEXT:
                raise ToolError(f"The message is {len(text)} characters; WhatsApp allows {MAX_TEXT}.")
            body.update({"type": "text", "text": {"preview_url": False, "body": text}})
        else:
            raise ToolError("Nothing to send: give the message text, or a template name.")
        if self.data.get("dry_run"):
            log.info("whatsapp: dry run to %s, nothing sent", mask(num))
            return f"Dry run: nothing was sent. ORION would have sent a {body['type']} message to {who}."
        pid = self.data.get("phone_number_id")
        token = self._token()
        if not pid or not token:
            raise ToolError("WhatsApp isn't set up: add the Phone number ID and access token in Accounts → WhatsApp.")
        data = self._call("POST", f"/{pid}/messages", token, body=body)
        msg = (data.get("messages") or [{}])[0]
        mid = msg.get("id", "")
        if not mid:
            raise ToolError("WhatsApp answered without a message id, so ORION can't tell whether it was sent.")
        status = msg.get("message_status", "accepted")
        log.info("whatsapp: %s message %s to %s", status, mid[:24], mask(num))
        note = "" if template else (" If they haven't messaged your business number in the last 24 hours, WhatsApp "
                                    "won't deliver free text; use a template instead.")
        return (f"WhatsApp {status} the message to {who} (message id {mid}). Delivery isn't confirmed: ORION "
                f"doesn't receive WhatsApp delivery receipts yet.{note}")

    def whatsapp_draft(self, to: str = "", text: str = "") -> str:
        """Open the user's own (personal) WhatsApp on a chat with the message already typed, for them to check and
        press Send. Use this to message from their personal number. It opens a window but sends nothing itself, so
        tell the user the message is waiting for them to press Send; never say it was sent.

        Args:
            to: A saved WhatsApp contact name (see whatsapp_contacts) or a phone number with country code. Leave
                empty to let the user pick the chat in WhatsApp.
            text: The exact message to type in.
        """
        text = str(text or "").strip()
        if not text:
            raise ToolError("Give the message text.")
        num, who = "", "a chat you pick"
        if str(to or "").strip():
            name, num = self.resolve(to)
            who = f"{name} ({mask(num)})" if name else mask(num)
        app = desktop_app_installed()
        q = urllib.parse.quote(text, safe="")
        if app:
            url = f"whatsapp://send?{'phone=' + num + '&' if num else ''}text={q}"
        else:
            url = f"https://wa.me/{num}?text={q}"
        try:
            platform.open_target(url)
        except OSError as exc:
            raise ToolError(f"Couldn't open WhatsApp: {exc}") from exc
        log.info("whatsapp: opened a draft to %s in %s", mask(num) or "a chosen chat",
                 "WhatsApp Desktop" if app else "the browser")
        where = "WhatsApp" if app else "WhatsApp Web in your browser"
        return (f"Opened {where} with the message to {who} typed in. It is NOT sent yet: check it and press Send"
                + (", after picking the chat." if not num else "."))

    def whatsapp_send_personal(self, to: str, text: str) -> str:
        """Send a WhatsApp message from the user's own (personal) WhatsApp. The user first sees the recipient and
        the exact text and says yes or no; only after a yes does ORION open WhatsApp Desktop on that chat with the
        message typed in and press Send. Write the final message, not a summary.

        Args:
            to: A saved WhatsApp contact name (see whatsapp_contacts) or a phone number with country code.
            text: The exact message to send.
        """
        text = str(text or "").strip()
        if not text:
            raise ToolError("Give the message text.")
        if "\n" in text:
            raise ToolError("Pressing Enter would send only the first line. Write the message on one line, or use "
                            "whatsapp_draft and let the user send it.")
        name, num = self.resolve(to)
        who = f"{name} ({mask(num)})" if name else mask(num)
        if not desktop_app_installed():
            raise ToolError("WhatsApp Desktop isn't installed, so ORION can't press Send for you. Install it from the "
                            "Microsoft Store, or use whatsapp_draft to open WhatsApp Web with the message typed in.")
        try:
            platform.open_target(f"whatsapp://send?phone={num}&text={urllib.parse.quote(text, safe='')}")
        except OSError as exc:
            raise ToolError(f"Couldn't open WhatsApp: {exc}") from exc
        how = press_send_in_whatsapp(text)
        log.info("whatsapp: pressed Send to %s in WhatsApp Desktop (%s)", mask(num), how)
        if how == "cleared":
            return f"Sent from your WhatsApp to {who}: the message left the compose box after ORION pressed Send."
        return (f"ORION pressed Send in WhatsApp for the message to {who}, but couldn't read WhatsApp's window to "
                "confirm it went. Say it was sent but unconfirmed.")

    def whatsapp_contacts(self) -> list[dict]:
        """List the saved WhatsApp contacts (names and masked numbers) that whatsapp_send accepts by name."""
        return self.public()["contacts"]

    def whatsapp_templates(self) -> list[dict]:
        """List the WhatsApp message templates on the user's business account, with language, status and body
        text, for sending to people outside the 24-hour window."""
        waba = self.data.get("business_account_id")
        token = self._token()
        if not waba or not token:
            raise ToolError("Add the WhatsApp Business Account ID and token in Accounts → WhatsApp to list templates.")
        data = self._call("GET", f"/{waba}/message_templates", token,
                          params={"fields": "name,language,status,components", "limit": 100})
        out = []
        for t in data.get("data", []):
            body = next((c.get("text", "") for c in t.get("components", []) if c.get("type") == "BODY"), "")
            out.append({"name": t.get("name"), "language": t.get("language"), "status": t.get("status"),
                        "body": body})
        return out
