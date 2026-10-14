import datetime as dt

import pytest

from orion.ecosystem import accounts as acc_mod
from orion.tools.registry import ToolContext, ToolError

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:standup@test
DTSTART:%(d)sT090000
DTEND:%(d)sT091500
RRULE:FREQ=DAILY;COUNT=5
SUMMARY:Team standup
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
UID:holiday@test
DTSTART;VALUE=DATE:%(d2)s
DTEND;VALUE=DATE:%(d3)s
SUMMARY:Holiday
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def ics(monkeypatch):
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    body = ICS % {b"d": tomorrow.strftime("%Y%m%d").encode(), b"d2": (tomorrow + dt.timedelta(days=1)).strftime("%Y%m%d").encode(),
                  b"d3": (tomorrow + dt.timedelta(days=2)).strftime("%Y%m%d").encode()}
    monkeypatch.setattr(acc_mod.Accounts, "_fetch_ics", lambda self, url, force=False: body)
    return body


async def test_calendar_tools_registered_and_expand_recurrence(engine, ics):
    assert engine.registry.get("calendar_upcoming") is None  # not configured → no tokens spent
    assert engine.registry.get("email_inbox") is None
    engine.accounts.configure_calendars([{"name": "Work", "url": "https://example.com/private.ics"}])
    tool = engine.registry.get("calendar_upcoming")
    assert tool is not None and tool.source == "account:calendar"
    import json

    events = json.loads(await tool.run({"days": 4}, ToolContext(engine=engine)))
    standups = [e for e in events if e["title"] == "Team standup"]
    assert len(standups) == 3 and standups[0]["start"].endswith("09:00") and standups[0]["location"] == "Zoom"
    assert any(e["title"] == "Holiday" and e["all_day"] for e in events)
    filtered = engine.accounts.calendar_upcoming(days=4, query="holiday")
    assert [e["title"] for e in filtered] == ["Holiday"]


def test_rejects_non_ical_links(engine):
    with pytest.raises(ToolError):
        engine.accounts.configure_calendars([{"url": "ftp://nope"}])


def test_add_event_opens_prefilled_google(engine, monkeypatch):
    opened = []
    monkeypatch.setattr(acc_mod, "open_target", opened.append)
    out = engine.accounts.calendar_add_event("Dentist", "2026-11-03T15:30", details="Bring card")
    assert "Dentist" in out
    assert opened[0].startswith("https://calendar.google.com/calendar/render?")
    assert "20261103T153000%2F20261103T163000" in opened[0]
    assert engine.registry.get("calendar_add_event").risk == "confirm"


def test_email_config_validates_before_saving(engine, monkeypatch):
    def fail(self, em, password=None):
        raise ToolError("Email sign-in failed")

    monkeypatch.setattr(acc_mod.Accounts, "_imap_login", fail)
    with pytest.raises(ToolError):
        engine.accounts.configure_email({"preset": "gmail", "address": "me@gmail.com", "password": "x"})
    assert engine.accounts.public()["email"] is None

    class FakeImap:
        def logout(self):
            pass

    monkeypatch.setattr(acc_mod.Accounts, "_imap_login", lambda self, em, password=None: FakeImap())
    monkeypatch.setattr(acc_mod.Accounts, "_set_password", lambda self, a, p: None)
    pub = engine.accounts.configure_email({"preset": "gmail", "address": "me@gmail.com", "password": "app-pass"})
    assert pub["email"]["imap_host"] == "imap.gmail.com" and "password" not in pub["email"]
    assert engine.registry.get("email_send").risk == "confirm"
    assert engine.registry.get("email_inbox").risk == "safe"
    engine.accounts.remove_email()
    assert engine.registry.get("email_inbox") is None


def test_body_text_prefers_plain_and_falls_back_to_html():
    import email

    msg = email.message_from_string("Content-Type: text/html\n\n<p>Hello <b>there</b></p><script>x</script>")
    assert acc_mod._body_text(msg) == "Hello there"
