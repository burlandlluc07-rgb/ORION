from orion.tools import context


def test_only_pointing_requests_get_context(monkeypatch):
    monkeypatch.setattr(context, "snapshot", lambda selection=True: {
        "app": "notepad", "title": "notes.txt - Notepad", "selection": "The quick brown fox", "clipboard": ""})
    ctx = context.context_for("summarise this for me")
    assert "notes.txt - Notepad (notepad)" in ctx and "The quick brown fox" in ctx
    assert context.context_for("translate the selected text into French")
    assert context.context_for("what's the weather tomorrow") == ""


def test_describe_skips_duplicates_and_empty():
    assert context.describe({"title": "", "selection": "", "clipboard": ""}) == ""
    d = context.describe({"title": "Doc", "app": "", "selection": "abc", "clipboard": "abc"})
    assert "Clipboard" not in d and "Active window: Doc" in d


def test_tool_works_without_windows(monkeypatch):
    monkeypatch.setattr(context, "_clipboard", lambda: "copied words")
    assert "copied words" in context.current_context()
