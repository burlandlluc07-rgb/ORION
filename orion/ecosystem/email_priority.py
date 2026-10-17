"""Email importance: score messages from their headers and opening text, with the user's own rules.

Read-only by design: scoring only looks at mail, it never flags, labels, moves or marks anything.
The score is a transparent sum of signals so every result carries a short "why", and the model can
re-rank further from the content when it presents the list.

Signals: the user's own rules (stored locally in ``email_rules.json``), people the user has written to
(from the Sent folder), mail addressed directly to the user vs. copied or bulk, newsletters and
automated senders, urgency and deadline wording (English, Spanish, Catalan), sender-set priority,
Gmail's own "important" marker, stars and recency.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import json
import re
from pathlib import Path

HIGH, MEDIUM = 65, 40
BASE = 40

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


# ── scoring ────────────────────────────────────────────────────────────────
def score(msg: dict, me: str, known: set[str], rules: list[dict], now: dt.datetime | None = None) -> dict:
    """Score one email 0-100 with reasons.

    ``msg`` holds raw header values (from, to, cc, subject, date, list_unsubscribe, list_id, precedence,
    auto_submitted, x_priority, importance), ``text`` (opening body text), ``flags`` and ``gmail_important``.
    """
    me = me.lower()
    sender = address_of(msg.get("from", ""))
    sender_name = email.utils.parseaddr(msg.get("from", ""))[0]
    subject = msg.get("subject", "")
    text = f"{subject}\n{msg.get('text', '')[:1500]}"
    pts = BASE
    reasons: list[str] = []

    def add(n: int, why: str) -> None:
        nonlocal pts
        pts += n
        reasons.append(why)

    rule = next((r for r in rules if rule_matches(r, sender, sender_name, subject)), None)
    if rule:
        add(50 if rule["level"] == "important" else -45,
            f"your rule: “{rule['match']}” is {'always important' if rule['level'] == 'important' else 'low priority'}")

    bulk = bool(msg.get("list_unsubscribe") or msg.get("list_id")) or \
        (msg.get("precedence", "").strip().lower() in ("bulk", "list", "junk"))
    automated = bool(AUTOMATED_SENDER.search(sender)) or \
        (msg.get("auto_submitted", "no").strip().lower() not in ("", "no"))
    if sender and sender in known:
        add(20, "someone you've written to")
    if bulk:
        add(-25, "newsletter or mailing list")
    elif automated:
        add(-15, "automated sender")

    to, cc = addresses(msg.get("to", "")), addresses(msg.get("cc", ""))
    if me in to:
        if len(to) <= 3 and not bulk:
            add(8, "sent directly to you")
    elif me in cc:
        add(-5, "you're only copied")
    elif not bulk:
        add(-5, "not addressed to you directly")

    # Marketing mail is full of "today only" and "urgent": urgency counts only outside bulk mail.
    if not bulk and (m := URGENT.search(text)):
        add(15, f"says “{m.group(0).lower()}”")
    if not bulk and (m := DEADLINE.search(text)):
        add(10, f"mentions a date or deadline (“{m.group(0).strip()}”)")
    if m := ACTION.search(text):
        add(10, f"about {m.group(0).lower()}")
    prio = msg.get("x_priority", "").strip()[:1]
    if prio in ("1", "2") or msg.get("importance", "").strip().lower() == "high":
        add(10, "the sender marked it high priority")
    if msg.get("gmail_important"):
        add(10, "Gmail marked it important")
    flags = msg.get("flags") or set()
    if "\\Flagged" in flags:
        add(15, "you starred it")

    try:
        when = email.utils.parsedate_to_datetime(msg.get("date", ""))
        now = now or dt.datetime.now(dt.timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        age = now - when
        if age <= dt.timedelta(days=1):
            add(5, "arrived in the last day")
        elif age > dt.timedelta(days=14):
            add(-5, "over two weeks old")
    except (TypeError, ValueError, IndexError):
        pass

    total = max(0, min(100, pts))
    return {"score": total, "importance": "high" if total >= HIGH else "medium" if total >= MEDIUM else "low",
            "reasons": reasons or ["nothing stands out"]}
