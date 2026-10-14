from typing import Literal

from orion.tools.registry import ToolContext, schema_for


def sample(path: str, count: int = 3, mode: Literal["a", "b"] = "a", tags: list[str] | None = None,
           ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Do a thing.

    Args:
        path: Where to do it.
        count: How many
            times (continued line).
        mode: Which mode.
    """
    return ""


def test_schema_from_signature():
    schema, ctx_param, summary = schema_for(sample)
    assert summary == "Do a thing."
    assert ctx_param == "ctx"
    props = schema["properties"]
    assert set(props) == {"path", "count", "mode", "tags"}
    assert props["path"] == {"type": "string", "description": "Where to do it."}
    assert props["count"]["type"] == "integer" and props["count"]["default"] == 3
    assert "continued line" in props["count"]["description"]
    assert props["mode"]["enum"] == ["a", "b"]
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}
    assert schema["required"] == ["path"]


def test_core_tools_have_valid_schemas(engine):
    names = set()
    for t in engine.registry.all():
        assert t.description and len(t.description) > 8, t.name
        assert t.parameters["type"] == "object"
        assert t.risk in ("safe", "confirm", "danger")
        names.add(t.name)
    # Destructive tools must never be "safe".
    for risky in ("delete_path", "run_command", "run_python", "power", "kill_process"):
        assert engine.registry.get(risky).risk == "danger"
    assert {"open_app", "web_search", "write_file", "set_timer", "look_at_screen"} <= names
