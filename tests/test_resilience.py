import pytest
from google.genai import errors

from orion.tools import web
from orion.tools.registry import ToolError


class _Resp:
    def __init__(self, text):
        self.text = text


async def test_generate_text_retries_overload_then_falls_back(engine, monkeypatch):
    calls = []

    async def fake_generate(model, contents, config):
        calls.append(model)
        if model == engine.config.settings.model_smart:
            raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
        return _Resp("seven three eight one")

    engine.brain._client = type("C", (), {"aio": type("A", (), {"models": type("M", (), {"generate_content": staticmethod(fake_generate)})()})()})()
    engine.brain._client_key = engine.config.get_api_key()
    out = await engine.brain.generate_text("hi", tier="smart")
    smart, fast = engine.config.settings.model_smart, engine.config.settings.model_fast
    assert out == "seven three eight one"
    assert calls == [smart, smart, fast]  # one retry on the same model, then the fallback


class FakeDDGS:
    def __init__(self, news=None, text=None):
        self._news, self._text = news, text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def news(self, q, max_results):
        if self._news is None:
            raise Exception("No results found.")
        return self._news

    def text(self, q, max_results):
        if self._text is None:
            raise Exception("No results found.")
        return self._text


def test_news_with_no_hits_falls_back_to_web(monkeypatch):
    monkeypatch.setattr(web, "_ddgs", lambda: FakeDDGS(news=None, text=[{"title": "T", "href": "https://x", "body": "b"}]))
    out = web.web_search("top news today", kind="news")
    assert out == [{"title": "T", "url": "https://x", "snippet": "b"}]


def test_no_results_anywhere_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(web, "_ddgs", lambda: FakeDDGS())
    with pytest.raises(ToolError, match="No results"):
        web.web_search("zxqv nonsense", kind="news")
