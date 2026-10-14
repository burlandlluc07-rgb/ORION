import pytest

from orion.tools import productivity as p
from orion.tools.registry import ToolContext, ToolError


def test_todo_lists(engine):
    ctx = ToolContext(engine=engine)
    assert "empty" in p.todo("list", ctx=ctx)
    p.todo("add", "Buy milk", "shopping", ctx=ctx)
    p.todo("add", "Eggs", "Shopping", ctx=ctx)
    out = p.todo("add", "Send invoice", due="in 2 hours", ctx=ctx)
    assert "reminder set" in out and any("Send invoice" in r["name"] for r in engine.store.list_routines())
    assert [i["item"] for i in p.todo("list", list_name="shopping", ctx=ctx)] == ["Buy milk", "Eggs"]
    assert "Ticked off “Eggs”" in p.todo("done", "egg", "shopping", ctx=ctx)
    lst = p.todo("list", list_name="shopping", ctx=ctx)
    assert lst[0]["item"] == "Buy milk" and lst[-1]["done"] is True  # open items first
    assert "Cleared 1" in p.todo("clear_done", list_name="shopping", ctx=ctx)
    assert "Removed" in p.todo("remove", "1", "shopping", ctx=ctx)
    with pytest.raises(ToolError):
        p.todo("done", "nothing like this", ctx=ctx)
    assert {i["list"] for i in p.todo("list", list_name="all", ctx=ctx)} == {"to-do"}


class Resp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


def test_currency_primary_and_fallback(monkeypatch):
    import httpx

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "frankfurter" in url:
            if params["symbols"] == "ZAR" and params["base"] == "USD":
                return Resp(200, {"rates": {"ZAR": 18.0}, "date": "2026-10-14"})
            return Resp(404, {})
        return Resp(200, {"result": "success", "rates": {"XOF": 600.0}, "time_last_update_utc": "Wed, 14 Oct 2026"})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert p.convert_currency(10, "usd", "zar").startswith("10.00 USD = 180.00 ZAR")
    out = p.convert_currency(2, "EUR", "XOF")
    assert "1,200.00 XOF" in out and "er-api" in out
    with pytest.raises(ToolError):
        p.convert_currency(1, "EUR", "QQQ")
