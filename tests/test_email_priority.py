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
    assert "are important" in acc.email_importance_rules("add", "github.com", "important")
    acc.email_importance_rules("add", "Sarah@Acme.com", "low")
    assert {r["match"] for r in acc.email_importance_rules("list")["rules"]} == {"github.com", "sarah@acme.com"}
    assert (acc.root / "email_rules.json").exists()
    out = acc.email_by_importance()
    assert out["emails"][0]["id"] == "3" and any("your rule" in r for r in out["emails"][0]["reasons"])
    assert "Removed" in acc.email_importance_rules("remove", "sarah@acme.com")
    assert "no rule" in acc.email_importance_rules("remove", "sarah@acme.com")
    with pytest.raises(acc_mod.ToolError):
        acc.email_importance_rules("add", "  ")


def test_tools_are_safe_email_kit_tools(mailbox, engine):
    for name in ("email_by_importance", "email_importance_rules", "email_importance_feedback"):
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


# ── adapting to the user's feedback ────────────────────────────────────────
def test_feedback_moves_a_sender_and_adapts_the_order(mailbox):
    acc, store, original = mailbox
    before = [e["id"] for e in acc.email_by_importance()["emails"]]
    assert before.index("4") > before.index("2")
    out = acc.email_importance_feedback("4", "more")
    assert "bob@random.org will rank higher" in out and "random.org emails" in out and "now scores" in out
    for _ in range(6):  # repeated feedback is capped, never runs away
        acc.email_importance_feedback("4", "more")
    learned = acc.email_learning.load()
    assert learned["senders"]["bob@random.org"] == ep.SENDER_CAP
    assert learned["domains"]["random.org"] == ep.DOMAIN_CAP
    top = acc.email_by_importance()["emails"][0]
    assert top["id"] == "4" and any("you've said emails from bob@random.org matter more" in r for r in top["reasons"])
    assert (acc.root / "email_learning.json").exists()
    assert store == original  # learning only writes ORION's own file


def test_less_feedback_and_signal_weights(mailbox):
    acc, _, _ = mailbox
    acc.email_importance_feedback("2", "less")
    learned = acc.email_learning.load()
    assert learned["senders"]["sarah@acme.com"] == -ep.SENDER_STEP
    assert learned["signals"]["urgent"] == 0.9 and learned["signals"]["contact"] == 0.9
    s = ep.score(msg(text="this is urgent"), ME, set(), [], now=NOW, learned=learned)
    assert "says “urgent” (counts less for you)" in s["reasons"]
    # "less" on an automated email makes the automated penalty stronger, not weaker
    acc.email_importance_feedback("3", "less")
    assert acc.email_learning.load()["signals"]["automated"] == 1.1


def test_signal_weights_stay_bounded(tmp_path):
    lr = ep.Learning(tmp_path / "l.json")
    for _ in range(20):
        lr.feedback("a@x.com", ["urgent", "bulk"], "less")
    sig = lr.load()["signals"]
    assert sig["urgent"] == ep.SIGNAL_MIN and sig["bulk"] == ep.SIGNAL_MAX
    for _ in range(40):
        lr.feedback("a@x.com", ["urgent"], "more")
    assert lr.load()["signals"]["urgent"] == ep.SIGNAL_MAX
    lr.feedback("a@x.com", ["not-a-signal"], "more")  # unknown signals are ignored
    assert "not-a-signal" not in lr.load()["signals"]


def test_shared_mail_providers_are_never_learned_as_a_domain(tmp_path):
    lr = ep.Learning(tmp_path / "l.json")
    changed = lr.feedback("friend@gmail.com", [], "more")
    assert "domain" not in changed and lr.load()["domains"] == {}
    # a stale gmail.com entry (hand-edited file) is still ignored when scoring
    s = ep.score(msg(**{"from": "x@gmail.com"}), ME, set(), [], now=NOW,
                 learned={"senders": {}, "domains": {"gmail.com": 20}, "signals": {}})
    assert not any("gmail.com emails" in r for r in s["reasons"])


def test_forget(mailbox):
    acc, _, _ = mailbox
    acc.email_importance_feedback("4", "more")
    acc.email_importance_feedback("2", "less")
    listed = acc.email_importance_rules("list")
    assert listed["learned_senders"] == {"bob@random.org": 10, "sarah@acme.com": -10}
    assert "Forgot" in acc.email_importance_rules("forget", "random.org")  # the domain and its senders
    learned = acc.email_learning.load()
    assert "bob@random.org" not in learned["senders"] and "random.org" not in learned["domains"]
    assert "sarah@acme.com" in learned["senders"]
    assert "hadn't learned" in acc.email_importance_rules("forget", "nobody@nowhere.com")
    assert "Forgot" in acc.email_importance_rules("forget")
    assert acc.email_importance_rules("list") == "No importance rules yet, and nothing learned from feedback."


def test_a_low_rule_no_longer_buries_urgent_mail(mailbox):
    acc, _, _ = mailbox
    acc.email_importance_rules("add", "acme.com", "low")
    sarah = next(e for e in acc.email_by_importance()["emails"] if e["id"] == "2")
    assert sarah["importance"] == "medium"  # urgent + deadline + a contact still lifts it out of low
    assert any("your rule" in r for r in sarah["reasons"])
    calm = ep.score(msg(**{"from": "x@acme.com"}), ME, set(), [{"match": "acme.com", "level": "low"}], now=NOW)
    assert calm["importance"] == "low"


def test_people_you_email_often_rank_higher():
    once = ep.score(msg(), ME, {"ann@corp.com": 1}, [], now=NOW)
    often = ep.score(msg(), ME, {"ann@corp.com": 7}, [], now=NOW)
    assert "you email them often" in often["reasons"] and often["score"] > once["score"]


def test_feedback_errors_and_damaged_file(mailbox):
    acc, _, _ = mailbox
    with pytest.raises(acc_mod.ToolError):
        acc.email_importance_feedback("1:*", "more")  # only a single email id
    with pytest.raises(acc_mod.ToolError):
        acc.email_importance_feedback("999", "more")
    with pytest.raises(acc_mod.ToolError):
        acc.email_importance_feedback("1", "more", folder="Nope")
    acc.email_learning.path.write_text("{not json", "utf-8")
    assert acc.email_learning.load() == {"senders": {}, "domains": {}, "signals": {}}
    assert acc.email_by_importance()["emails"]  # ranking still works
    acc.email_importance_feedback("4", "more")
    assert acc.email_learning.load()["senders"] == {"bob@random.org": 10}


def test_review_fixes_for_learning(tmp_path, mailbox):
    lr = ep.Learning(tmp_path / "l.json")
    # the user's own organisation is never learned as a domain, nor applied when scoring
    assert "domain" not in lr.feedback("facilities@acme.com", [], "less", me="me@acme.com")
    assert "domain" not in lr.feedback("it@mail.acme.com", [], "less", me="me@acme.com")
    own = {"senders": {}, "domains": {"acme.com": -20}, "signals": {}}
    assert not any("acme.com emails" in r for r in
                   ep.score(msg(**{"from": "boss@acme.com"}), "me@acme.com", set(), [], now=NOW, learned=own)["reasons"])
    # verdicts are checked, never silently read as "less"
    with pytest.raises(ValueError):
        lr.feedback("a@x.com", [], "higher")
    assert lr.feedback("a@x.com", [], " More ")["sender"] == ep.SENDER_STEP
    acc, _, _ = mailbox
    with pytest.raises(acc_mod.ToolError):
        acc.email_importance_feedback("4", "higher")
    # a sender with no @ is learned once, not again as a "domain"
    assert "domain" not in lr.feedback("MAILER-DAEMON", [], "less")
    # forgetting a domain covers its subdomains and their senders
    lr.feedback("bob@mail.corp.com", [], "less")
    lr.feedback("ann@corp.com", [], "less")
    lr.forget("corp.com")
    learned = lr.load()
    assert not [k for k in learned["senders"] if "corp.com" in k] and not [k for k in learned["domains"] if "corp" in k]
    # regional shared providers are not learned either
    assert "domain" not in lr.feedback("x@gmx.net", [], "less")


def test_damaged_numbers_in_the_learning_file_are_ignored_or_clamped(tmp_path):
    p = tmp_path / "l.json"
    p.write_text('{"senders": {"a@x.com": NaN, "b@x.com": 1000, "c@x.com": true, "d@x.com": 1e400},'
                 ' "domains": {"x.com": -Infinity, "y.com": -99},'
                 ' "signals": {"recent": -3, "urgent": 9, "made_up": 1.2}}', "utf-8")
    learned = ep.Learning(p).load()
    assert learned == {"senders": {"b@x.com": ep.SENDER_CAP}, "domains": {"y.com": -ep.DOMAIN_CAP},
                       "signals": {"recent": ep.SIGNAL_MIN, "urgent": ep.SIGNAL_MAX}}
    s = ep.score(msg(**{"from": "a@x.com"}), ME, set(), [], now=NOW, learned=learned)
    assert 0 <= s["score"] <= 100


def test_an_important_rule_alone_reaches_high_even_for_list_mail():
    gh = msg(**{"from": "GitHub <notifications@github.com>", "cc": ME, "to": "repo@noreply.github.com",
                "list_id": "<repo.github.com>"})
    s = ep.score(gh, ME, set(), [{"match": "github.com", "level": "important"}], now=NOW)
    assert s["importance"] == "high"


def test_ranked_entries_carry_their_folder(mailbox):
    acc, _, _ = mailbox
    assert {e["folder"] for e in acc.email_by_importance()["emails"]} == {"INBOX"}
