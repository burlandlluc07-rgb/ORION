from orion.ecosystem.connectors import clean_schema, tool_name


def test_tool_name_sanitised():
    assert tool_name("home-assistant", "turn.on light") == "home_assistant__turn_on_light"
    assert len(tool_name("x" * 40, "y" * 40)) == 64


def test_clean_schema_strips_unsupported_keys():
    s = clean_schema({
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "additionalProperties": False,
        "properties": {"a": {"type": ["string", "null"], "additionalProperties": False},
                       "b": {"type": "array", "items": {"$ref": "#/x", "type": "integer"}}},
    })
    assert "$schema" not in s and "additionalProperties" not in s
    assert s["properties"]["a"]["type"] == "string"
    assert s["properties"]["b"]["items"] == {"type": "integer"}
