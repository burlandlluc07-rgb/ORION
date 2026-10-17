"""Email importance: score messages from their headers and opening text, and adapt to the user.

Read-only by design: scoring only looks at mail, it never flags, labels, moves or marks anything.
The score is a transparent sum of signals so every result carries a short "why", and the model can
re-rank further from the content when it presents the list.

Signals: the user's own rules (stored locally in ``email_rules.json``), how often the user writes to the
sender (from the Sent folder), mail addressed directly to the user vs. copied or bulk, newsletters and
automated senders, urgency and deadline wording (English, Spanish, Catalan), sender-set priority,
Gmail's own "important" marker, stars and recency.

Adaptable: rules are strong defaults, not absolutes (an urgent email from a "low priority" sender can
still reach medium). When the user says a ranked email mattered more or less than ORION thought,
:class:`Learning` nudges that sender, their organisation's domain and the signals that fired, within
fixed bounds, and keeps it on this PC in ``email_learning.json``. Every learned adjustment shows up in
the reasons, and can be forgotten.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import json
import math
import re
import threading
from collections.abc import Mapping
from pathlib import Path

HIGH, MEDIUM = 65, 40
BASE = 40
# Strong defaults: an "important" rule alone reaches high, and an urgent email from someone you write to can still
# climb out of low past a "low priority" rule.
RULE_POINTS = {"important": 50, "low": -40}
# One piece of feedback moves a sender this much, their domain less; the totals are capped.
SENDER_STEP, SENDER_CAP = 10, 40
DOMAIN_STEP, DOMAIN_CAP = 4, 20
# Signal weights learn slowly and stay within half to one-and-a-half of their default.
SIGNAL_STEP, SIGNAL_MIN, SIGNAL_MAX = 0.1, 0.5, 1.5
# Shared mail providers say nothing about who the sender is, so their domain is never learned.
FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "outlook.es", "outlook.fr", "hotmail.com", "hotmail.es",
    "hotmail.co.uk", "hotmail.fr", "live.com", "live.co.uk", "msn.com", "yahoo.com", "yahoo.es", "yahoo.co.uk",
    "yahoo.fr", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "protonmail.ch", "pm.me", "gmx.com", "gmx.es", "gmx.net", "gmx.de", "web.de", "mail.com", "mail.ru",
    "yandex.com", "yandex.ru", "zoho.com", "qq.com", "163.com", "telefonica.net", "movistar.es", "orange.fr",
}


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].lower() if "@" in address else ""


def same_org(domain: str, other: str) -> bool:
    """acme.com and mail.acme.com are the same organisation."""
    return bool(domain and other) and (domain == other or domain.endswith("." + other) or other.endswith("." + domain))


def learnable_domain(domain: str, me: str = "") -> bool:
    """Shared providers and the user's own organisation say nothing about who sent an email."""
    return bool(domain) and domain not in FREEMAIL and not same_org(domain, domain_of(me))

URGENT = re.compile(
    r"\b(urgent|urgently|asap|immediately|important|action required|action needed|response required|"
    r"time[- ]sensitive|final notice|last chance to respond|overdue|past due|deadline|"
    r"urgente|importante|inmediato|cuanto antes|lo antes posible|plazo|vencid[oa]|"
    r"immediatament|com més aviat|termini)\b", re.I)
DEADLINE = re.compile(
    r"\b(today|tonight|tomorrow|by (monday|tuesday|wednesday|thursday|friday|saturday|sunday|eod|end of (the )?day|"
    r"noon|tonight|\d{1,2}(:\d{2})?\s*(am|pm)?)|end of (the )?(day|week)|eod|this week|due (on|by|date)|due \w+day|"
    r"expires?|expiring|"
    r"hoy|mañana|esta semana|antes del?|fecha l[ií]mite|avui|demà|aquesta setmana|abans de)\b"
    r"|\b\d{1,2}/\d{1,2}(/\d{2,4})?\b|\b\d{1,2}[.-]\d{1,2}[.-]\d{2,4}\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}\b"
    r"|\b\d{1,2} (jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", re.I)
ACTION = re.compile(
    r"\b(invoice|payment|pay now|bill|receipt due|contract|sign|signature|interview|offer letter|"
    r"security alert|suspicious|sign-in attempt|password reset|verify your|account (locked|suspended)|"
    r"factura|pago|contrato|firma|entrevista|alerta de seguridad|pagament|contracte|signatura)\b", re.I)
AUTOMATED_SENDER = re.compile(r"(^|[._+-])(no-?reply|do-?not-?reply|notifications?|mailer(-daemon)?|bounce|"
                              r"newsletter|marketing|promo(tions)?|news|info|hello|team)([._+-]|@)", re.I)


def address_of(value: str) -> str:
    return (email.utils.parseaddr(value or "")[1] or "").lower()


def addresses(*values: str) -> set[str]:
    return {a.lower() for _, a in email.utils.getaddresses([v for v in values if v]) if a}


# ── the user's rules ───────────────────────────────────────────────────────
class Rules:
    """"Emails from X are always important" — kept on this PC in ``email_rules.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else []
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict) and r.get("match") and r.get("level") in ("important", "low")]

    def save(self, rules: list[dict]) -> None:
        self.path.write_text(json.dumps(rules, indent=2, ensure_ascii=False), "utf-8")

    def add(self, match: str, level: str) -> dict:
        match = match.strip().lower()
        rules = [r for r in self.load() if r["match"] != match]
        rule = {"match": match, "level": level}
        rules.append(rule)
        self.save(rules)
        return rule

    def remove(self, match: str) -> bool:
        match = match.strip().lower()
        rules = self.load()
        kept = [r for r in rules if r["match"] != match]
        if len(kept) == len(rules):
            return False
        self.save(kept)
        return True


def rule_matches(rule: dict, sender: str, sender_name: str, subject: str) -> bool:
    """An address matches exactly, "@acme.com" or "acme.com" matches the sender's domain (and subdomains),
    anything else is a word looked for in the sender's name and the subject."""
    m = rule["match"].lower()
    domain = sender.rpartition("@")[2]
    if "@" in m and not m.startswith("@"):
        return sender == m
    d = m.lstrip("@")
    if "." in d and " " not in d:
        return domain == d or domain.endswith("." + d)
    return re.search(rf"\b{re.escape(m)}\b", f"{sender_name} {subject}".lower()) is not None


# ── learning from feedback ─────────────────────────────────────────────────
class Learning:
    """What the user's feedback taught: per-sender and per-domain points, and per-signal weights."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        bounds = {"senders": (-SENDER_CAP, SENDER_CAP), "domains": (-DOMAIN_CAP, DOMAIN_CAP),
                  "signals": (SIGNAL_MIN, SIGNAL_MAX)}
        out: dict = {}
        for key, (lo, hi) in bounds.items():
            part = data.get(key)
            out[key] = {}
            for k, v in (part.items() if isinstance(part, dict) else ()):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                try:
                    v = float(v)
                except OverflowError:
                    continue
                if math.isfinite(v) and (key != "signals" or k in SIGNALS):
                    out[key][str(k).lower()] = max(lo, min(hi, v))
        return out

    def _save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")

    def feedback(self, sender: str, signals: list[str], verdict: str, me: str = "") -> dict:
        """Learn from "this email was more / less important than ranked". Returns what changed."""
        verdict = str(verdict).strip().lower()
        if verdict not in ("more", "less"):
            raise ValueError("verdict must be 'more' or 'less'")
        sign = 1 if verdict == "more" else -1
        sender = sender.strip().lower()
        domain = domain_of(sender)
        with self._lock:
            data = self.load()
            changed: dict = {}
            if sender:
                v = data["senders"].get(sender, 0) + sign * SENDER_STEP
                data["senders"][sender] = changed["sender"] = max(-SENDER_CAP, min(SENDER_CAP, v))
            if learnable_domain(domain, me):
                v = data["domains"].get(domain, 0) + sign * DOMAIN_STEP
                data["domains"][domain] = changed["domain"] = max(-DOMAIN_CAP, min(DOMAIN_CAP, v))
            for key in signals:
                pts = SIGNALS.get(key)
                if pts is None:
                    continue
                # "More important" strengthens the signals that pushed it up and weakens the ones that held
                # it down; "less important" does the opposite.
                direction = sign if pts > 0 else -sign
                w = data["signals"].get(key, 1.0) + direction * SIGNAL_STEP
                data["signals"][key] = round(max(SIGNAL_MIN, min(SIGNAL_MAX, w)), 2)
                changed.setdefault("signals", {})[key] = data["signals"][key]
            for part in ("senders", "domains"):
                data[part] = {k: v for k, v in data[part].items() if v}
            data["signals"] = {k: v for k, v in data["signals"].items() if v != 1.0}
            self._save(data)
        return changed

    def forget(self, match: str = "") -> int:
        """Forget what was learned about an address or a domain (with its subdomains and their senders), or
        everything when ``match`` is empty."""
        match = match.strip().lower().lstrip("@")
        with self._lock:
            data = self.load()
            if not match:
                n = sum(len(v) for v in data.values())
                self._save({"senders": {}, "domains": {}, "signals": {}})
                return n
            n = 0
            for part in ("senders", "domains"):
                for k in list(data[part]):
                    d = k if part == "domains" else domain_of(k)
                    if k == match or (d and (d == match or d.endswith("." + match))):
                        del data[part][k]
                        n += 1
            self._save(data)
            return n


# ── scoring ────────────────────────────────────────────────────────────────
# Default points of each signal; learned weights scale these.
SIGNALS: dict[str, int] = {
    "contact": 15, "frequent_contact": 10, "bulk": -25, "automated": -15, "direct": 8, "cc_only": -5,
    "not_to_you": -5, "urgent": 15, "deadline": 10, "action": 10, "sender_priority": 10, "gmail_important": 10,
    "starred": 15, "recent": 5, "old": -5,
}


def _contact_count(known: Mapping[str, int] | set[str], sender: str) -> int:
    if isinstance(known, Mapping):
        return int(known.get(sender, 0))
    return 1 if sender in known else 0


def score(msg: dict, me: str, known: Mapping[str, int] | set[str], rules: list[dict],
          now: dt.datetime | None = None, learned: dict | None = None) -> dict:
    """Score one email 0-100 with reasons and the signals that fired.

    ``msg`` holds raw header values (from, to, cc, subject, date, list_unsubscribe, list_id, precedence,
    auto_submitted, x_priority, importance), ``text`` (opening body text), ``flags`` and ``gmail_important``.
    ``known`` maps addresses the user has written to onto how many emails they sent them.
    ``learned`` is :meth:`Learning.load` output.
    """
    me = me.lower()
    learned = learned or {}
    weights = learned.get("signals", {})
    sender = address_of(msg.get("from", ""))
    domain = domain_of(sender)
    sender_name = email.utils.parseaddr(msg.get("from", ""))[0]
    subject = msg.get("subject", "")
    text = f"{subject}\n{msg.get('text', '')[:1500]}"
    pts = float(BASE)
    reasons: list[str] = []
    fired: list[str] = []

    def add(n: float, why: str) -> None:
        nonlocal pts
        pts += n
        reasons.append(why)

    def signal(key: str, why: str) -> None:
        w = weights.get(key, 1.0)
        fired.append(key)
        add(SIGNALS[key] * w, why if w == 1.0 else f"{why} ({'counts more' if w > 1 else 'counts less'} for you)")

    rule = next((r for r in rules if rule_matches(r, sender, sender_name, subject)), None)
    if rule:
        add(RULE_POINTS[rule["level"]],
            f"your rule: “{rule['match']}” is {'important' if rule['level'] == 'important' else 'low priority'}")
    if v := learned.get("senders", {}).get(sender):
        add(v, f"you've said emails from {sender} matter {'more' if v > 0 else 'less'}")
    if learnable_domain(domain, me) and (v := learned.get("domains", {}).get(domain)):
        add(v, f"you've said {domain} emails matter {'more' if v > 0 else 'less'}")

    bulk = bool(msg.get("list_unsubscribe") or msg.get("list_id")) or \
        (msg.get("precedence", "").strip().lower() in ("bulk", "list", "junk"))
    automated = bool(AUTOMATED_SENDER.search(sender)) or \
        (msg.get("auto_submitted", "no").strip().lower() not in ("", "no"))
    sent = _contact_count(known, sender) if sender else 0
    if sent:
        signal("contact", "someone you've written to")
        if sent >= 5:
            signal("frequent_contact", "you email them often")
    if bulk:
        signal("bulk", "newsletter or mailing list")
    elif automated:
        signal("automated", "automated sender")

    to, cc = addresses(msg.get("to", "")), addresses(msg.get("cc", ""))
    if me in to:
        if len(to) <= 3 and not bulk:
            signal("direct", "sent directly to you")
    elif me in cc:
        signal("cc_only", "you're only copied")
    elif not bulk:
        signal("not_to_you", "not addressed to you directly")

    # Marketing mail is full of "today only" and "urgent": urgency counts only outside bulk mail.
    if not bulk and (m := URGENT.search(text)):
        signal("urgent", f"says “{m.group(0).lower()}”")
    if not bulk and (m := DEADLINE.search(text)):
        signal("deadline", f"mentions a date or deadline (“{m.group(0).strip()}”)")
    if m := ACTION.search(text):
        signal("action", f"about {m.group(0).lower()}")
    prio = msg.get("x_priority", "").strip()[:1]
    if prio in ("1", "2") or msg.get("importance", "").strip().lower() == "high":
        signal("sender_priority", "the sender marked it high priority")
    if msg.get("gmail_important"):
        signal("gmail_important", "Gmail marked it important")
    flags = msg.get("flags") or set()
    if "\\Flagged" in flags:
        signal("starred", "you starred it")

    try:
        when = email.utils.parsedate_to_datetime(msg.get("date", ""))
        now = now or dt.datetime.now(dt.timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        age = now - when
        if age <= dt.timedelta(days=1):
            signal("recent", "arrived in the last day")
        elif age > dt.timedelta(days=14):
            signal("old", "over two weeks old")
    except (TypeError, ValueError, IndexError):
        pass

    total = max(0, min(100, round(pts)))
    # ``raw`` keeps the order among emails that all hit 0 or 100.
    return {"score": total, "importance": "high" if total >= HIGH else "medium" if total >= MEDIUM else "low",
            "reasons": reasons or ["nothing stands out"], "signals": fired, "raw": pts}
