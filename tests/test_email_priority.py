"""Sorting email by importance: scoring signals, the user's own rules, and the read-only guarantee."""

import copy
import datetime as dt
from email.message import EmailMessage

import pytest

from orion.ecosystem import accounts as acc_mod
from orion.ecosystem import email_priority as ep
from orion.tasks.routing import capabilities_for

from .test_email_kit import ME, FakeImap

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)


def raw(frm, subject, body, to=ME, cc="", date="Sat, 17 Oct 2026 09:00:00 +0000", **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, date
    if cc:
        m["Cc"] = cc
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m.as_bytes()


@pytest.fixture
def mailbox(engine, monkeypatch):
    store = {
        "INBOX": {
            1: {"raw": raw("Shop <news@shop.com>", "50% off everything", "Huge sale today only!",
                           List_Unsubscribe="<mailto:unsub@shop.com>"), "flags": set()},
            2: {"raw": raw("Sarah Lane <sarah@acme.com>", "Contract to sign by Friday",
                           "Hi, can you sign the contract by Friday? It's urgent."), "flags": set()},
            3: {"raw": raw("GitHub <notifications@github.com>", "[repo] New comment", "Someone commented."),
                "flags": set()},
            4: {"raw": raw("Bob Stranger <bob@random.org>", "Hello", "Just saying hi."), "flags": set()},
            5: {"raw": raw("Old friend <pal@old.net>", "Photos", "Here are the photos.", cc=ME, to="x@y.com"),
                "flags": {"\\Seen"}},
        },
        "Sent": {1: {"raw": raw(ME, "Lunch?", "Friday?", to="Sarah Lane <sarah@acme.com>"), "flags": {"\\Seen"}}},
        "Archive": {}, "Trash": {},
    }
    original = copy.deepcopy(store)
    engine.accounts.data["email"] = {"address": ME, "name": "Me", "imap_host": "imap.example.com", "imap_port": 993,
                                     "smtp_host": "smtp.example.com", "smtp_port": 587}
    monkeypatch.setattr(acc_mod.Accounts, "_imap_login", lambda self, em, password=None: FakeImap(store))
    monkeypatch.setattr(acc_mod.Accounts, "_password", lambda self, a: "pw")
    engine.accounts.sync_tools()
    return engine.accounts, store, original


def test_ranks_real_people_with_deadlines_above_newsletters(mailbox):
    acc, store, original = mailbox
    out = acc.email_by_importance()
    ids = [e["id"] for e in out["emails"]]
    assert out["looked_at"] == 4  # unread only: the read one is skipped
    assert ids[0] == "2" and ids[-1] == "1"
    top = out["emails"][0]
    assert top["importance"] == "high"
    assert "someone you've written to" in top["reasons"] and any("urgent" in r for r in top["reasons"])
    assert any("newsletter" in r for r in out["emails"][-1]["reasons"]) and out["emails"][-1]["importance"] == "low"
    assert store == original  # read-only: no flags, moves or deletions


def test_all_mail_and_limit(mailbox):
    acc, _, _ = mailbox
    out = acc.email_by_importance(unread_only=False, limit=2)
    assert out["looked_at"] == 5 and len(out["emails"]) == 2
    assert sum(out["counts"].values()) == 5


def test_user_rules_change_the_order_and_persist(mailbox, engine):
    acc, _, _ = mailbox
    assert "always important" in acc.email_importance_rules("add", "github.com", "important")
    acc.email_importance_rules("add", "Sarah@Acme.com", "low")
    assert {r["match"] for r in acc.email_importance_rules("list")} == {"github.com", "sarah@acme.com"}
    assert (acc.root / "email_rules.json").exists()
    out = acc.email_by_importance()
    assert out["emails"][0]["id"] == "3" and any("your rule" in r for r in out["emails"][0]["reasons"])
    assert "Removed" in acc.email_importance_rules("remove", "sarah@acme.com")
    assert "no rule" in acc.email_importance_rules("remove", "sarah@acme.com")
    with pytest.raises(acc_mod.ToolError):
        acc.email_importance_rules("add", "  ")


def test_tools_are_safe_email_kit_tools(mailbox, engine):
    for name in ("email_by_importance", "email_importance_rules"):
        tool = engine.registry.get(name)
        assert tool.risk == "safe" and tool.kit == "email"
        caps = capabilities_for(tool)
        assert caps.desktop == "none" and not caps.changes_external
    assert "email_by_importance" in engine.registry.kit_tools("email")


def test_gmail_important_label_and_star_count(engine, monkeypatch):
    store = {"INBOX": {1: {"raw": raw("A <a@x.com>", "Note", "fyi"), "flags": {"\\Flagged"}, "labels": ["\\Important"]},
                       2: {"raw": raw("B <b@x.com>", "Note", "fyi"), "flags": set()}}}
    engine.accounts.data["email"] = {"address": ME, "imap_host": "imap.gmail.com", "imap_port": 993,
                                     "smtp_host": "smtp.gmail.com", "smtp_port": 465}
    monkeypatch.setattr(acc_mod.Accounts, "_imap_login", lambda self, em, password=None: FakeImap(store))
    out = engine.accounts.email_by_importance()
    first = out["emails"][0]
    assert first["id"] == "1" and "Gmail marked it important" in first["reasons"] and "you starred it" in first["reasons"]


def msg(**kw):
    base = {"from": "Ann <ann@corp.com>", "to": ME, "subject": "", "text": "", "date": "Sat, 17 Oct 2026 10:00:00 +0000"}
    return base | kw


@pytest.mark.parametrize("kw,expect", [
    ({"subject": "Factura pendiente", "text": "El pago vence mañana"}, "about factura"),
    ({"subject": "Reunió", "text": "Ho necessito avui, és urgent"}, "says “urgent”"),
    ({"text": "Please reply by Friday"}, "mentions a date or deadline (“by Friday”)"),
    ({"x_priority": "1 (Highest)"}, "the sender marked it high priority"),
    ({"auto_submitted": "auto-generated"}, "automated sender"),
    ({"to": "a@b.com, c@d.com", "cc": ME}, "you're only copied"),
])
def test_signals(kw, expect):
    s = ep.score(msg(**kw), ME, set(), [], now=NOW)
    assert expect in s["reasons"]


def test_no_false_dates_from_versions_or_prices():
    s = ep.score(msg(text="Release 2.6 costs 12.50 euros"), ME, set(), [], now=NOW)
    assert not any("date" in r for r in s["reasons"])


def test_rule_matching():
    r = lambda m: {"match": m, "level": "important"}
    assert ep.rule_matches(r("boss@acme.com"), "boss@acme.com", "", "")
    assert not ep.rule_matches(r("boss@acme.com"), "bob@acme.com", "", "")
    assert ep.rule_matches(r("acme.com"), "bob@mail.acme.com", "", "")
    assert ep.rule_matches(r("@acme.com"), "bob@acme.com", "", "")
    assert not ep.rule_matches(r("acme.com"), "bob@notacme.com", "", "")
    assert ep.rule_matches(r("invoice"), "x@y.com", "", "Your invoice is ready")
    assert not ep.rule_matches(r("invoice"), "x@y.com", "", "Invoices")


def test_score_bounds_and_age():
    old = ep.score(msg(date="Mon, 01 Jun 2026 10:00:00 +0000"), ME, set(), [], now=NOW)
    assert "over two weeks old" in old["reasons"]
    many = ep.score(msg(subject="URGENT deadline today invoice", x_priority="1"), ME, {"ann@corp.com"},
                    [{"match": "corp.com", "level": "important"}], now=NOW)
    assert many["score"] == 100 and many["importance"] == "high"
    assert ep.score(msg(date="garbage"), ME, set(), [], now=NOW)["score"] >= 0


def test_parses_a_real_servers_split_header_and_text_response(engine):
    """Real IMAP servers return one literal per requested part, several messages in one response."""
    class Server:
        def uid(self, cmd, uids, items):
            assert cmd == "fetch" and uids == b"7,9" and "X-GM-LABELS" in items
            return "OK", [
                (b'1 (UID 7 FLAGS (\\Seen \\Flagged) X-GM-LABELS ("\\\\Important") BODY[HEADER.FIELDS (FROM)] {40}',
                 b"From: Ann <ann@corp.com>\r\nSubject: Hi\r\nList-Id: <l.corp.com>\r\n\r\n"),
                (b" BODY[TEXT]<0> {12}", b"Hello there"),
                b")",
                (b"2 (UID 9 FLAGS () BODY[TEXT]<0> {5}", b"Body2"),
                (b" BODY[HEADER.FIELDS (FROM)] {30}", b"From: Bob <bob@x.com>\r\nSubject: Yo\r\n\r\n"),
                b")",
            ]

    a, b = engine.accounts._priority_fetch(Server(), [b"7", b"9"], gmail=True)
    assert (a["id"], a["from"], a["text"], a["list_id"]) == ("7", "Ann <ann@corp.com>", "Hello there", "<l.corp.com>")
    assert a["flags"] == {"\\Seen", "\\Flagged"} and a["gmail_important"]
    assert (b["id"], b["subject"], b["text"], b["gmail_important"]) == ("9", "Yo", "Body2", False)
