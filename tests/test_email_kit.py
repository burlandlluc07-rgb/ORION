"""Email toolkit against an in-memory IMAP server and a captured SMTP."""

import email
from email.message import EmailMessage

import pytest

import orion.tools.files as files
import orion.tools.platform as platform
from orion.ecosystem import accounts as acc_mod
from orion.tools.registry import ToolError

ME = "me@example.com"


def make_msg(frm, to, subject, body, cc="", attach=None, msgid=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    if cc:
        m["Cc"] = cc
    m["Date"] = "Mon, 12 Oct 2026 10:00:00 +0000"
    m["Message-ID"] = msgid or f"<{subject.replace(' ', '')}@test>"
    m.set_content(body)
    if attach:
        m.add_attachment(attach[1], maintype="application", subtype="pdf", filename=attach[0])
    return m.as_bytes()


class FakeImap:
    def __init__(self, store, move=True):
        self.store = store
        self.box = None
        self.capabilities = ("IMAP4REV1", "MOVE") if move else ("IMAP4REV1",)
        self.literal = None

    def select(self, box, readonly=True):
        box = box.strip('"')
        if box not in self.store:
            return "NO", [b""]
        self.box = box
        return "OK", [str(len(self.store[box])).encode()]

    def list(self):
        flags = {"INBOX": "", "Archive": "\\Archive", "Trash": "\\Trash", "Sent": "\\Sent", "Junk": "\\Junk",
                 "Projects": ""}
        return "OK", [f'({f} \\HasNoChildren) "/" "{n}"'.encode() for n, f in flags.items() if n in self.store]

    def uid(self, cmd, *args):
        msgs = self.store[self.box]
        cmd = cmd.upper()
        if cmd == "SEARCH":
            needle = (self.literal or b"").decode().lower()
            field = args[-1].upper()
            self.literal = None
            hits = []
            for uid, m in msgs.items():
                h = email.message_from_bytes(m["raw"])
                hay = {"FROM": h.get("From", ""), "TO": h.get("To", ""), "CC": h.get("Cc", ""), "ALL": "x"}.get(field, "")
                if field == "UNSEEN":
                    if "\\Seen" not in m["flags"]:
                        hits.append(str(uid).encode())
                elif field == "ALL" or needle in hay.lower():
                    hits.append(str(uid).encode())
            return "OK", [b" ".join(hits)]
        if cmd == "FETCH" and b"," in args[0]:  # several uids in one round trip
            out = []
            for u in args[0].split(b","):
                out += [x for x in self.uid("FETCH", u, *args[1:])[1] if x]
            return "OK", out
        uid = int(args[0])
        if uid not in msgs:
            return "OK", [None]
        if cmd == "FETCH":
            labels = f" X-GM-LABELS ({' '.join(msgs[uid].get('labels', ()))})" if "X-GM-LABELS" in args[-1] else ""
            return "OK", [(f"{uid} (UID {uid} FLAGS ({' '.join(msgs[uid]['flags'])}){labels} BODY[] {{1}}".encode(),
                           msgs[uid]["raw"]), b")"]
        if cmd == "STORE":
            flag = args[2].strip("()")
            (msgs[uid]["flags"].add if args[1] == "+FLAGS" else msgs[uid]["flags"].discard)(flag)
            return "OK", [b""]
        if cmd in ("MOVE", "COPY"):
            target = args[1].strip('"')
            dest = self.store[target]
            dest[max(dest, default=0) + 1] = {"raw": msgs[uid]["raw"], "flags": set(msgs[uid]["flags"])}
            if cmd == "MOVE":
                del msgs[uid]
            return "OK", [b""]
        raise AssertionError(cmd)

    def expunge(self):
        for uid in [u for u, m in self.store[self.box].items() if "\\Deleted" in m["flags"]]:
            del self.store[self.box][uid]
        return "OK", [b""]

    def logout(self):
        pass


@pytest.fixture
def mail(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "home", lambda: tmp_path)
    monkeypatch.setattr(files, "home", lambda: tmp_path)
    files.set_data_dir(tmp_path / ".orion")
    store = {
        "INBOX": {
            1: {"raw": make_msg("Sarah Lane <sarah@acme.com>", ME, "Q3 numbers", "See attached.",
                                cc="Tom <tom@acme.com>", attach=("q3.pdf", b"%PDF-1.4 fake")), "flags": set()},
            2: {"raw": make_msg("News <news@shop.com>", ME, "Big sale", "Buy now"), "flags": set()},
        },
        "Archive": {}, "Trash": {}, "Sent": {
            1: {"raw": make_msg(ME, "Sarah Lane <sarah@acme.com>", "Lunch?", "Friday?"), "flags": {"\\Seen"}}},
        "Junk": {}, "Projects": {},
    }
    state = {"move": True, "sent": []}
    engine.accounts.data["email"] = {"address": ME, "name": "Me", "imap_host": "imap.example.com", "imap_port": 993,
                                     "smtp_host": "smtp.example.com", "smtp_port": 587}
    monkeypatch.setattr(acc_mod.Accounts, "_imap_login", lambda self, em, password=None: FakeImap(store, state["move"]))
    monkeypatch.setattr(acc_mod.Accounts, "_password", lambda self, a: "pw")
    monkeypatch.setattr(acc_mod.Accounts, "_smtp_send", lambda self, msg: state["sent"].append(msg))
    engine.accounts.sync_tools()
    return engine.accounts, store, state


def test_email_tools_are_the_email_toolkit(mail, engine):
    names = engine.registry.kit_tools("email")
    assert {"email_forward", "email_move", "email_save_attachments", "email_find_contact", "email_mark",
            "email_folders"} <= set(names)
    assert engine.registry.get("email_move").risk == "confirm"


def test_reply_all_threads_and_copies_everyone(mail):
    acc, _, state = mail
    out = acc.email_send("sarah@acme.com", "", "Thanks!", reply_to_id="1", reply_all=True)
    msg = state["sent"][-1]
    assert msg["Subject"] == "Re: Q3 numbers" and msg["In-Reply-To"] == "<Q3numbers@test>"
    assert "tom@acme.com" in msg["Cc"] and ME not in msg["Cc"] and "cc" in out


def test_send_with_attachment(mail, tmp_path):
    acc, _, state = mail
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "Invoice.pdf").write_bytes(b"%PDF-1.4 invoice")
    acc.email_send("bob@x.com", "Invoice", "Attached.", attachments=["documents/Invoice.pdf"])
    att = [p for p in state["sent"][-1].walk() if p.get_filename()]
    assert att[0].get_filename() == "Invoice.pdf" and att[0].get_payload(decode=True) == b"%PDF-1.4 invoice"
    with pytest.raises(ToolError):
        acc.email_send("bob@x.com", "x", "y", attachments=["documents/missing.pdf"])


def test_forward_keeps_attachments(mail):
    acc, _, state = mail
    acc.email_forward("1", "boss@acme.com", note="FYI")
    msg = state["sent"][-1]
    assert msg["Subject"] == "Fwd: Q3 numbers"
    body = msg.get_body(("plain",)).get_content()
    assert body.startswith("FYI") and "Forwarded message" in body and "See attached." in body
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["q3.pdf"]


def test_save_attachments(mail, tmp_path):
    acc, _, _ = mail
    out = acc.email_save_attachments("1")
    assert (tmp_path / "Downloads" / "q3.pdf").read_bytes() == b"%PDF-1.4 fake" and "q3.pdf" in out
    acc.email_save_attachments("1")
    assert (tmp_path / "Downloads" / "q3 (1).pdf").exists()  # never overwrites
    with pytest.raises(ToolError):
        acc.email_save_attachments("2")


def test_mark_move_archive_trash(mail):
    acc, store, state = mail
    acc.email_mark("2", "read")
    assert "\\Seen" in store["INBOX"][2]["flags"]
    acc.email_mark("2", "unread")
    assert "\\Seen" not in store["INBOX"][2]["flags"]
    assert "Archive" in acc.email_move("2", "archive")
    assert 2 not in store["INBOX"] and len(store["Archive"]) == 1
    state["move"] = False  # servers without MOVE: copy + delete + expunge
    assert "Trash" in acc.email_move("1", "trash")
    assert store["INBOX"] == {} and len(store["Trash"]) == 1
    with pytest.raises(ToolError):
        acc.email_move("1", "Nonexistent folder", folder="Trash")
    acc.email_move("1", "Projects", folder="Trash")
    assert len(store["Projects"]) == 1


def test_folders_and_contacts(mail):
    acc, _, _ = mail
    folders = {f["name"]: f["special"] for f in acc.email_folders()}
    assert folders["Trash"] == "trash" and folders["Archive"] == "archive" and folders["INBOX"] == ""
    people = acc.email_find_contact("sarah")
    assert people[0]["email"] == "sarah@acme.com" and people[0]["name"] == "Sarah Lane" and people[0]["emails"] >= 2
    assert "No one" in acc.email_find_contact("zzz")
