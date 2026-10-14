"""Accounts: email (IMAP/SMTP) and calendars (iCal/ICS links).

Deliberately setup-light — no Google Cloud project or OAuth app needed:

* Email uses the standard IMAP/SMTP protocols with an *app password*
  (Gmail, iCloud, Yahoo, Outlook, Fastmail, most custom domains).
* Calendars are read from each calendar's private iCal address
  (Google: Settings → calendar → "Secret address in iCal format";
  Outlook: Settings → Calendar → Shared calendars → Publish → ICS).
  Recurring events are expanded properly.
* New events open pre-filled in Google/Outlook web (one click to save) or
  as an .ics file in the default calendar app.

Tools are only registered while an account is configured, so they cost no
prompt tokens otherwise. Passwords live in the OS credential vault.
"""

from __future__ import annotations

import datetime as dt
import email
import email.utils
import imaplib
import json
import logging
import os
import re
import smtplib
import ssl
import tempfile
import threading
import time
import urllib.parse
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

import httpx

from ..tools.platform import open_target
from ..tools.registry import Registry, Tool, ToolError, schema_for

log = logging.getLogger("orion.accounts")

PRESETS: dict[str, dict] = {
    "gmail": {"label": "Gmail", "imap_host": "imap.gmail.com", "imap_port": 993, "smtp_host": "smtp.gmail.com",
              "smtp_port": 465, "help": "Turn on 2-Step Verification, then create an app password at "
                                       "myaccount.google.com/apppasswords."},
    "outlook": {"label": "Outlook / Hotmail", "imap_host": "outlook.office365.com", "imap_port": 993,
                "smtp_host": "smtp-mail.outlook.com", "smtp_port": 587,
                "help": "Use an app password from account.microsoft.com → Security. Some Microsoft accounts no "
                        "longer allow IMAP passwords; if sign-in fails, use an MCP connector instead."},
    "icloud": {"label": "iCloud Mail", "imap_host": "imap.mail.me.com", "imap_port": 993,
               "smtp_host": "smtp.mail.me.com", "smtp_port": 587,
               "help": "Create an app-specific password at account.apple.com → Sign-In and Security."},
    "yahoo": {"label": "Yahoo Mail", "imap_host": "imap.mail.yahoo.com", "imap_port": 993,
              "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465,
              "help": "Generate an app password in Yahoo Account Security."},
    "custom": {"label": "Other (IMAP/SMTP)", "imap_host": "", "imap_port": 993, "smtp_host": "", "smtp_port": 587,
               "help": "Use your provider's IMAP and SMTP settings."},
}
KEYRING_SERVICE = "ORION"
SPECIAL = {"\\archive", "\\trash", "\\junk", "\\sent", "\\all", "\\drafts", "\\flagged"}


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_text(msg: email.message.Message, limit: int = 20000) -> str:
    plain, html = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain is None and html is not None:
        from ..tools.web import html_to_text

        plain = html_to_text(html)[1]
    text = re.sub(r"\n{3,}", "\n\n", (plain or "").strip())
    return text[:limit]


def _attachments(msg: email.message.Message) -> list[str]:
    return [_decode(p.get_filename()) for p in msg.walk() if p.get_filename()]


class Accounts:
    def __init__(self, root: Path, registry: Registry, on_change=lambda: None) -> None:
        self.path = root / "accounts.json"
        self.root = root
        self.registry = registry
        self.on_change = on_change
        self._lock = threading.Lock()
        self._ics_cache: dict[str, tuple[float, bytes]] = {}
        self.data = self._read()

    # ── persistence ────────────────────────────────────────────────────────
    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        except ValueError:
            return {}

    def _write(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), "utf-8")

    def _secret_key(self, address: str) -> str:
        return f"email:{address.lower()}"

    def _set_password(self, address: str, password: str) -> None:
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, self._secret_key(address), password)
            return
        except Exception:
            pass
        f = self.root / ".email_key"
        f.write_text(password, "utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass

    def _password(self, address: str) -> str:
        try:
            import keyring

            pw = keyring.get_password(KEYRING_SERVICE, self._secret_key(address))
            if pw:
                return pw
        except Exception:
            pass
        f = self.root / ".email_key"
        return f.read_text("utf-8") if f.exists() else ""

    # ── public config API ──────────────────────────────────────────────────
    def public(self) -> dict:
        em = self.data.get("email")
        return {
            "email": {k: v for k, v in em.items()} if em else None,
            "calendars": [{"name": c["name"], "host": urllib.parse.urlparse(c["url"]).netloc}
                          for c in self.data.get("calendars", [])],
            "add_events_via": self.data.get("add_events_via", "google"),
            "presets": {k: {"label": v["label"], "help": v["help"]} for k, v in PRESETS.items()},
        }

    def configure_email(self, cfg: dict) -> dict:
        preset = PRESETS.get(cfg.get("preset", "custom"), PRESETS["custom"])
        address = str(cfg.get("address", "")).strip()
        if "@" not in address:
            raise ToolError("Enter a full email address.")
        em = {
            "preset": cfg.get("preset", "custom"),
            "address": address,
            "name": str(cfg.get("name", "")).strip(),
            "imap_host": cfg.get("imap_host") or preset["imap_host"],
            "imap_port": int(cfg.get("imap_port") or preset["imap_port"]),
            "smtp_host": cfg.get("smtp_host") or preset["smtp_host"],
            "smtp_port": int(cfg.get("smtp_port") or preset["smtp_port"]),
        }
        if not em["imap_host"] or not em["smtp_host"]:
            raise ToolError("IMAP and SMTP servers are required.")
        password = str(cfg.get("password", "")) or self._password(address)
        if not password:
            raise ToolError("Enter the app password.")
        self._imap_login(em, password).logout()  # verify before saving
        self._set_password(address, password)
        with self._lock:
            self.data["email"] = em
            self._write()
        self.sync_tools()
        return self.public()

    def remove_email(self) -> None:
        with self._lock:
            self.data.pop("email", None)
            self._write()
        self.sync_tools()

    def configure_calendars(self, calendars: list[dict], add_events_via: str | None = None) -> dict:
        cleaned = []
        for c in calendars:
            url = str(c.get("url", "")).strip().replace("webcal://", "https://")
            if not url:
                continue
            if not url.startswith(("https://", "http://")):
                raise ToolError(f"{url[:40]}… is not an iCal link.")
            name = str(c.get("name") or urllib.parse.urlparse(url).netloc).strip()[:40]
            self._fetch_ics(url, force=True)  # verify it parses
            cleaned.append({"name": name, "url": url})
        with self._lock:
            self.data["calendars"] = cleaned
            if add_events_via in ("google", "outlook", "ics"):
                self.data["add_events_via"] = add_events_via
            self._write()
        self.sync_tools()
        return self.public()

    # ── tool registration ──────────────────────────────────────────────────
    def sync_tools(self) -> None:
        self.registry.remove_source("account:email")
        self.registry.remove_source("account:calendar")
        if self.data.get("email"):
            for fn, risk in ((self.email_inbox, "safe"), (self.email_search, "safe"), (self.email_read, "safe"),
                             (self.email_send, "confirm"), (self.email_forward, "confirm"),
                             (self.email_save_attachments, "confirm"), (self.email_mark, "safe"),
                             (self.email_move, "confirm"), (self.email_folders, "safe"),
                             (self.email_find_contact, "safe")):
                self._register(fn, risk, "account:email", "email")
        self._register(self.calendar_add_event, "confirm", "account:calendar", "calendar")
        if self.data.get("calendars"):
            self._register(self.calendar_upcoming, "safe", "account:calendar", "calendar")
        self.on_change()

    def _register(self, fn, risk: str, source: str, category: str) -> None:
        schema, _, desc = schema_for(fn)
        summ = None
        if fn.__name__ == "email_send":
            summ = lambda a: (f"Send email to {a.get('to')}: “{a.get('subject')}”"  # noqa: E731
                              + (f" + {len(a['attachments'])} attachment(s)" if a.get("attachments") else ""))
        elif fn.__name__ == "email_forward":
            summ = lambda a: f"Forward email {a.get('id')} to {a.get('to')}"  # noqa: E731
        elif fn.__name__ == "email_move":
            summ = lambda a: f"Move email {a.get('id')} to {a.get('destination')}"  # noqa: E731
        elif fn.__name__ == "email_save_attachments":
            summ = lambda a: f"Save attachments of email {a.get('id')} to {a.get('save_to') or 'Downloads'}"  # noqa: E731
        elif fn.__name__ == "calendar_add_event":
            summ = lambda a: f"Add “{a.get('title')}” on {a.get('start')}"  # noqa: E731
        self.registry.add(Tool(name=fn.__name__, description=desc, parameters=schema, fn=fn, risk=risk,  # type: ignore[arg-type]
                               category=category, source=source, summarize=summ,
                               kit="email" if category == "email" else None))

    # ── email ──────────────────────────────────────────────────────────────
    def _imap_login(self, em: dict, password: str | None = None) -> imaplib.IMAP4_SSL:
        try:
            imap = imaplib.IMAP4_SSL(em["imap_host"], em["imap_port"], ssl_context=ssl.create_default_context(), timeout=20)
            imap.login(em["address"], password if password is not None else self._password(em["address"]))
            return imap
        except imaplib.IMAP4.error as exc:
            raise ToolError(f"Email sign-in failed ({exc}). Check the address and app password.") from exc
        except OSError as exc:
            raise ToolError(f"Couldn't reach {em['imap_host']}: {exc}") from exc

    def _em(self) -> dict:
        em = self.data.get("email")
        if not em:
            raise ToolError("No email account is set up. Add one in Ecosystem → Accounts.")
        return em

    def _summaries(self, imap: imaplib.IMAP4_SSL, uids: list[bytes]) -> list[dict]:
        out = []
        for uid in uids:
            typ, data = imap.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[TEXT]<0.600>)")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            flags = data[0][0].decode(errors="replace")
            header_bytes, text_bytes = b"", b""
            for item in data:
                if isinstance(item, tuple):
                    if b"HEADER" in item[0]:
                        header_bytes = item[1]
                    elif b"TEXT" in item[0]:
                        text_bytes = item[1]
            h = email.message_from_bytes(header_bytes)
            snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|=\r?\n|--+[\w=_.-]+", " ", text_bytes.decode("utf-8", "replace")))
            out.append({"id": uid.decode(), "from": _decode(h.get("From")), "subject": _decode(h.get("Subject")),
                        "date": h.get("Date", ""), "unread": "\\Seen" not in flags, "snippet": snippet.strip()[:180]})
        return out

    def email_inbox(self, limit: int = 10, unread_only: bool = False, folder: str = "INBOX") -> list[dict]:
        """List the newest emails in the user's inbox (sender, subject, date, unread flag, short snippet).

        Args:
            limit: How many (max 30).
            unread_only: Only unread messages.
            folder: Mailbox folder name.
        """
        imap = self._imap_login(self._em())
        try:
            imap.select(f'"{folder}"', readonly=True)
            typ, data = imap.uid("search", None, "UNSEEN" if unread_only else "ALL")
            uids = (data[0].split() if typ == "OK" and data and data[0] else [])[-max(1, min(limit, 30)):]
            return list(reversed(self._summaries(imap, uids))) or [{"note": "No messages."}]
        finally:
            imap.logout()

    def email_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search the user's email (sender, subject or text), newest first.

        Args:
            query: Words, a name or an address. Gmail search syntax works for Gmail accounts.
            limit: Max results.
        """
        em = self._em()
        imap = self._imap_login(em)
        try:
            imap.select("INBOX" if "gmail" not in em["imap_host"] else '"[Gmail]/All Mail"', readonly=True)
            q = query.replace('"', "")
            if "gmail" in em["imap_host"]:
                typ, data = imap.uid("search", "X-GM-RAW", f'"{q}"')
            else:
                imap.literal = q.encode()  # type: ignore[attr-defined]
                typ, data = imap.uid("search", "CHARSET", "UTF-8", "TEXT")
            uids = (data[0].split() if typ == "OK" and data and data[0] else [])[-max(1, min(limit, 30)):]
            return list(reversed(self._summaries(imap, uids))) or [{"note": "No matching emails."}]
        finally:
            imap.logout()

    def _locate(self, imap: imaplib.IMAP4_SSL, id: str, folder: str, writable: bool = False) -> tuple[str, email.message.Message]:
        """Select the folder holding this email (Gmail search ids live in All Mail) and fetch it."""
        em = self._em()
        for box in dict.fromkeys([folder, "[Gmail]/All Mail" if "gmail" in em["imap_host"] else folder]):
            typ, _ = imap.select(f'"{box}"', readonly=not writable)
            if typ != "OK":
                continue
            typ, data = imap.uid("fetch", id.encode(), "(BODY.PEEK[])")
            if typ == "OK" and data and isinstance(data[0], tuple):
                return box, email.message_from_bytes(data[0][1])
        raise ToolError(f"Email {id} not found in {folder}. Use the id and folder from email_inbox/email_search.")

    def email_read(self, id: str, folder: str = "INBOX") -> dict:
        """Read one email in full (from email_inbox / email_search results). Doesn't mark it as read.

        Args:
            id: The email id.
            folder: Folder it is in (Gmail search results live in "[Gmail]/All Mail").
        """
        imap = self._imap_login(self._em())
        try:
            box, msg = self._locate(imap, id, folder)
            return {"from": _decode(msg.get("From")), "to": _decode(msg.get("To")), "cc": _decode(msg.get("Cc")),
                    "date": msg.get("Date", ""), "subject": _decode(msg.get("Subject")),
                    "message_id": msg.get("Message-ID", ""), "folder": box,
                    "attachments": _attachments(msg), "body": _body_text(msg)}
        finally:
            imap.logout()

    def _raw(self, id: str, folder: str) -> email.message.Message:
        imap = self._imap_login(self._em())
        try:
            return self._locate(imap, id, folder)[1]
        finally:
            imap.logout()

    @staticmethod
    def _attach(msg: EmailMessage, paths: list[str]) -> None:
        import mimetypes

        from ..tools.platform import resolve_path

        total = 0
        for raw in paths:
            fp = resolve_path(raw)
            if not fp.is_file():
                raise ToolError(f"Attachment not found: {fp}")
            total += fp.stat().st_size
            if total > 24 * 2**20:
                raise ToolError("Attachments are over 24 MB; most mail servers refuse that. Share a link instead.")
            ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
            main, sub = ctype.split("/", 1)
            msg.add_attachment(fp.read_bytes(), maintype=main, subtype=sub, filename=fp.name)

    def _smtp_send(self, msg: EmailMessage) -> None:
        em = self._em()
        password = self._password(em["address"])
        try:
            ctx = ssl.create_default_context()
            if em["smtp_port"] == 465:
                with smtplib.SMTP_SSL(em["smtp_host"], 465, context=ctx, timeout=60) as s:
                    s.login(em["address"], password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(em["smtp_host"], em["smtp_port"], timeout=60) as s:
                    s.starttls(context=ctx)
                    s.login(em["address"], password)
                    s.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise ToolError(f"Sending failed: {exc}") from exc

    def _new_message(self, to: str, subject: str, cc: str = "") -> EmailMessage:
        em = self._em()
        msg = EmailMessage()
        msg["From"] = email.utils.formataddr((em.get("name") or "", em["address"]))
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid()
        return msg

    def email_send(self, to: str, subject: str, body: str, cc: str = "", reply_to_id: str = "", reply_all: bool = False,
                   attachments: list[str] | None = None, folder: str = "INBOX") -> str:
        """Send an email from the user's account, optionally with file attachments, or as a reply. Always show the
        user what you'll send first if they haven't dictated it exactly. Use email_find_contact to get an address
        from a name.

        Args:
            to: Recipient address(es), comma-separated. For replies, the original sender.
            subject: Subject line (replies get "Re:" automatically).
            body: Plain-text body. Sign off with the user's name if known.
            cc: Optional CC addresses.
            reply_to_id: Email id to reply to (threads the reply under the original).
            reply_all: With reply_to_id, also copy everyone who was on the original.
            attachments: File paths to attach, e.g. ["documents/Invoice.pdf"].
            folder: Folder of the email being replied to.
        """
        em = self._em()
        if reply_to_id:
            orig = self._raw(reply_to_id, folder)
            if reply_all:
                me = em["address"].lower()
                seen = {a.lower() for _, a in email.utils.getaddresses([to, cc])}
                extra = [email.utils.formataddr((n, a)) for n, a in email.utils.getaddresses(
                    [orig.get("To", ""), orig.get("Cc", "")]) if a and a.lower() != me and a.lower() not in seen]
                cc = ", ".join([c for c in [cc] if c] + extra)
            orig_subject = _decode(orig.get("Subject"))
            if not subject.lower().startswith("re:"):
                subject = "Re: " + (orig_subject or subject)
        msg = self._new_message(to, subject, cc)
        if reply_to_id and orig.get("Message-ID"):
            msg["In-Reply-To"] = orig["Message-ID"]
            msg["References"] = " ".join(x for x in (orig.get("References", ""), orig["Message-ID"]) if x).strip()
        msg.set_content(body)
        self._attach(msg, attachments or [])
        self._smtp_send(msg)
        extra_note = f" with {len(attachments)} attachment(s)" if attachments else ""
        return f"Email sent to {to}{' (cc ' + cc + ')' if cc else ''}{extra_note}."

    def email_forward(self, id: str, to: str, note: str = "", folder: str = "INBOX") -> str:
        """Forward an email (with its attachments) to someone, with an optional note on top.

        Args:
            id: The email id.
            to: Recipient address(es).
            note: Optional message above the forwarded email.
            folder: Folder the email is in.
        """
        orig = self._raw(id, folder)
        subject = _decode(orig.get("Subject"))
        msg = self._new_message(to, subject if subject.lower().startswith(("fwd:", "fw:")) else f"Fwd: {subject}")
        header = "\n".join([
            "---------- Forwarded message ----------",
            f"From: {_decode(orig.get('From'))}", f"Date: {orig.get('Date', '')}",
            f"Subject: {subject}", f"To: {_decode(orig.get('To'))}",
        ])
        msg.set_content(f"{note.strip()}\n\n{header}\n\n{_body_text(orig, 100_000)}".lstrip())
        for part in orig.walk():
            if part.get_filename():
                data = part.get_payload(decode=True) or b""
                main, sub = (part.get_content_type() or "application/octet-stream").split("/", 1)
                msg.add_attachment(data, maintype=main, subtype=sub, filename=_decode(part.get_filename()))
        self._smtp_send(msg)
        return f"Forwarded “{subject}” to {to}."

    def email_save_attachments(self, id: str, save_to: str = "downloads", only: str = "", folder: str = "INBOX") -> str:
        """Save an email's attachments to a folder on the PC.

        Args:
            id: The email id.
            save_to: Destination folder.
            only: Optional part of a file name to save just that attachment.
            folder: Folder the email is in.
        """
        from ..tools.files import _check_writable, _human, _record
        from ..tools.platform import resolve_path

        orig = self._raw(id, folder)
        dest = resolve_path(save_to)
        _check_writable(dest)
        dest.mkdir(parents=True, exist_ok=True)
        saved = []
        for part in orig.walk():
            name = _decode(part.get_filename()) if part.get_filename() else ""
            if not name or (only and only.lower() not in name.lower()):
                continue
            safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(". ") or "attachment"
            target = dest / safe
            n = 1
            while target.exists():
                target = dest / f"{Path(safe).stem} ({n}){Path(safe).suffix}"
                n += 1
            target.write_bytes(part.get_payload(decode=True) or b"")
            _record({"op": "create", "path": str(target)})
            saved.append(f"{target.name} ({_human(target.stat().st_size)})")
        if not saved:
            raise ToolError("That email has no attachments" + (f" matching “{only}”." if only else "."))
        return f"Saved to {dest}: " + ", ".join(saved)

    def email_mark(self, id: str, action: Literal["read", "unread", "star", "unstar"], folder: str = "INBOX") -> str:
        """Mark an email as read/unread or star/unstar (flag) it.

        Args:
            id: The email id.
            action: What to do.
            folder: Folder the email is in.
        """
        flag, op = {"read": ("\\Seen", "+FLAGS"), "unread": ("\\Seen", "-FLAGS"),
                    "star": ("\\Flagged", "+FLAGS"), "unstar": ("\\Flagged", "-FLAGS")}[action]
        imap = self._imap_login(self._em())
        try:
            self._locate(imap, id, folder, writable=True)
            typ, _ = imap.uid("store", id.encode(), op, f"({flag})")
            if typ != "OK":
                raise ToolError("The mail server refused the change.")
            return f"Marked as {action}."
        finally:
            imap.logout()

    def _folders(self, imap: imaplib.IMAP4_SSL) -> list[tuple[str, set[str]]]:
        typ, data = imap.list()
        out = []
        for line in data or []:
            if not line:
                continue
            text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            m = re.match(r'^\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', text)
            if m:
                name = m.group("name").strip().strip('"')
                out.append((name, {f.lower() for f in m.group("flags").split()}))
        return out

    def email_folders(self) -> list[dict]:
        """List the mailbox's folders (labels on Gmail) and which are Archive, Trash, Spam and Sent."""
        imap = self._imap_login(self._em())
        try:
            return [{"name": n, "special": next((f.lstrip("\\") for f in flags if f in SPECIAL), "")}
                    for n, flags in self._folders(imap) if "\\noselect" not in flags]
        finally:
            imap.logout()

    def _special(self, imap: imaplib.IMAP4_SSL, kind: str) -> str:
        folders = self._folders(imap)
        for name, flags in folders:
            if f"\\{kind}" in flags:
                return name
        guesses = {"archive": ["Archive", "Archives", "[Gmail]/All Mail"], "trash": ["Trash", "Deleted Items",
                   "Deleted Messages", "[Gmail]/Trash", "Bin", "[Gmail]/Bin"],
                   "junk": ["Junk", "Spam", "Junk Email", "[Gmail]/Spam"]}[kind]
        names = {n.lower(): n for n, _ in folders}
        for g in guesses:
            if g.lower() in names:
                return names[g.lower()]
        raise ToolError(f"This mailbox has no {kind} folder. Folders: " + ", ".join(n for n, _ in folders[:20]))

    def email_move(self, id: str, destination: str, folder: str = "INBOX") -> str:
        """Archive an email, move it to Trash or Spam, or file it into another folder.

        Args:
            id: The email id.
            destination: "archive", "trash", "spam", "inbox" or an exact folder name from email_folders.
            folder: Folder the email is in now.
        """
        imap = self._imap_login(self._em())
        try:
            key = destination.strip().lower()
            target = {"archive": lambda: self._special(imap, "archive"), "trash": lambda: self._special(imap, "trash"),
                      "bin": lambda: self._special(imap, "trash"), "delete": lambda: self._special(imap, "trash"),
                      "spam": lambda: self._special(imap, "junk"), "junk": lambda: self._special(imap, "junk"),
                      "inbox": lambda: "INBOX"}.get(key, lambda: destination.strip())()
            if key not in ("archive", "trash", "bin", "delete", "spam", "junk", "inbox") and \
                    target not in [n for n, _ in self._folders(imap)]:
                raise ToolError(f"No folder named “{destination}”. Use email_folders to see them.")
            box, msg = self._locate(imap, id, folder, writable=True)
            if box == target:
                return f"It's already in {target}."
            uid = id.encode()
            if b"MOVE" in getattr(imap, "capabilities", ()):
                typ, _ = imap.uid("MOVE", uid, f'"{target}"')
            else:
                typ, _ = imap.uid("COPY", uid, f'"{target}"')
                if typ == "OK":
                    imap.uid("store", uid, "+FLAGS", "(\\Deleted)")
                    imap.expunge()
            if typ != "OK":
                raise ToolError(f"The mail server refused to move it to {target}.")
            return f"Moved “{_decode(msg.get('Subject'))}” to {target}."
        finally:
            imap.logout()

    def email_find_contact(self, name: str, limit: int = 8) -> list[dict] | str:
        """Find someone's email address from mail you've exchanged (searches senders and recipients by name).

        Args:
            name: Part of the person's name or address, e.g. "Sarah" or "acme.com".
            limit: Max people to return.
        """
        em = self._em()
        imap = self._imap_login(em)
        q = name.replace('"', "").strip()
        found: dict[str, dict] = {}
        try:
            boxes = ["INBOX"]
            try:
                sent = next((n for n, f in self._folders(imap) if "\\sent" in f), None)
                if sent:
                    boxes.append(sent)
            except Exception:
                pass
            for box in boxes:
                if imap.select(f'"{box}"', readonly=True)[0] != "OK":
                    continue
                for field in ("FROM", "TO", "CC"):
                    imap.literal = q.encode()  # type: ignore[attr-defined]
                    typ, data = imap.uid("search", "CHARSET", "UTF-8", field)
                    uids = (data[0].split() if typ == "OK" and data and data[0] else [])[-40:]
                    for uid in uids:
                        typ, d = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO CC DATE)])")
                        if typ != "OK" or not d or not isinstance(d[0], tuple):
                            continue
                        h = email.message_from_bytes(d[0][1])
                        for disp, addr in email.utils.getaddresses([_decode(h.get(x)) for x in ("From", "To", "Cc")]):
                            if not addr or addr.lower() == em["address"].lower():
                                continue
                            if q.lower() not in f"{disp} {addr}".lower():
                                continue
                            c = found.setdefault(addr.lower(), {"name": disp, "email": addr, "emails": 0})
                            c["emails"] += 1
                            c["name"] = c["name"] or disp
        finally:
            imap.logout()
        people = sorted(found.values(), key=lambda c: -c["emails"])[:limit]
        return people or f"No one matching “{name}” in your recent mail."

    # ── calendar ───────────────────────────────────────────────────────────
    def _fetch_ics(self, url: str, force: bool = False) -> bytes:
        now = time.time()
        hit = self._ics_cache.get(url)
        if hit and not force and now - hit[0] < 300:
            return hit[1]
        try:
            r = httpx.get(url, timeout=20, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"Couldn't download the calendar ({exc}).") from exc
        if b"BEGIN:VCALENDAR" not in r.content[:2000]:
            raise ToolError("That link didn't return an iCal calendar. Use the private/secret iCal (.ics) address.")
        self._ics_cache[url] = (now, r.content)
        return r.content

    def events_between(self, start: dt.datetime, end: dt.datetime, query: str = "") -> list[dict]:
        import icalendar
        import recurring_ical_events

        local_tz = dt.datetime.now().astimezone().tzinfo
        out = []
        for cal in self.data.get("calendars", []):
            try:
                ical = icalendar.Calendar.from_ical(self._fetch_ics(cal["url"]))
                events = recurring_ical_events.of(ical).between(start, end)
            except ToolError:
                raise
            except Exception as exc:
                log.warning("Calendar %s failed: %s", cal["name"], exc)
                continue
            for ev in events:
                s, e = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
                all_day = not isinstance(s, dt.datetime)
                if not all_day:
                    s = s.astimezone(local_tz) if s.tzinfo else s
                    e = (e.astimezone(local_tz) if getattr(e, "tzinfo", None) else e) if e else None
                title = str(ev.get("SUMMARY", "(no title)"))
                if query and query.lower() not in (title + str(ev.get("LOCATION", ""))).lower():
                    continue
                out.append({
                    "title": title, "calendar": cal["name"], "all_day": all_day,
                    "start": s.strftime("%a %d %b") if all_day else s.strftime("%a %d %b %H:%M"),
                    "end": (e.strftime("%H:%M") if e and not all_day else ""),
                    "location": str(ev.get("LOCATION", "")), "_sort": s.isoformat() if all_day else s.replace(tzinfo=None).isoformat(),
                })
        out.sort(key=lambda x: x["_sort"])
        for o in out:
            o.pop("_sort")
        return out

    def calendar_upcoming(self, days: int = 1, query: str = "") -> list[dict]:
        """List the user's calendar events from now on (today = days 1). Includes recurring events.

        Args:
            days: How many days ahead to look (1-31).
            query: Optional text to filter titles/locations.
        """
        now = dt.datetime.now().astimezone()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = today + dt.timedelta(days=max(1, min(days, 31)))
        return self.events_between(now, end, query) or [{"note": "Nothing scheduled in that range."}]

    def calendar_add_event(self, title: str, start: str, end: str = "", details: str = "", location: str = "") -> str:
        """Create a calendar event: opens it pre-filled so the user can save with one click.

        Args:
            title: Event title.
            start: Start as ISO "2026-10-20T15:00" (or a date "2026-10-20" for all-day).
            end: Optional end, same format. Defaults to 1 hour after start.
            details: Optional description.
            location: Optional location.
        """
        try:
            s = dt.datetime.fromisoformat(start)
        except ValueError as exc:
            raise ToolError("start must be ISO format like 2026-10-20T15:00") from exc
        all_day = len(start.strip()) <= 10
        e = dt.datetime.fromisoformat(end) if end else (s + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1)))
        via = self.data.get("add_events_via", "google")
        if via == "google":
            fmt = "%Y%m%d" if all_day else "%Y%m%dT%H%M%S"
            q = urllib.parse.urlencode({"action": "TEMPLATE", "text": title, "dates": f"{s:{fmt}}/{e:{fmt}}",
                                        "details": details, "location": location})
            open_target(f"https://calendar.google.com/calendar/render?{q}")
        elif via == "outlook":
            q = urllib.parse.urlencode({"path": "/calendar/action/compose", "rru": "addevent", "subject": title,
                                        "startdt": s.isoformat(), "enddt": e.isoformat(), "body": details,
                                        "location": location, "allday": str(all_day).lower()})
            open_target(f"https://outlook.live.com/calendar/0/deeplink/compose?{q}")
        else:
            import icalendar

            cal = icalendar.Calendar()
            cal.add("prodid", "-//ORION//EN")
            cal.add("version", "2.0")
            ev = icalendar.Event()
            ev.add("summary", title)
            ev.add("dtstart", s.date() if all_day else s)
            ev.add("dtend", e.date() if all_day else e)
            ev.add("description", details)
            ev.add("location", location)
            ev.add("uid", f"{time.time()}@orion")
            cal.add_component(ev)
            path = Path(tempfile.gettempdir()) / f"orion-event-{int(time.time())}.ics"
            path.write_bytes(cal.to_ical())
            open_target(str(path))
        return f"Opened “{title}” ({s:%a %d %b{'' if all_day else ' %H:%M'}}) pre-filled — the user just needs to click Save."

