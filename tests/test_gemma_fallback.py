"""Gemma as the last free fallback: after every Flash model, before the backup brain."""

import types as pytypes

import pytest
from google.genai import errors, types

from orion.brain.governor import Governor, QuotaExhausted

GEMMA = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]


def fake_models(*names, actions=("generateContent",)):
    async def listing():
        for n in names:
            yield pytypes.SimpleNamespace(name=f"models/{n}", supported_actions=list(actions))

    async def lister(config=None):
        return listing()

    return lister


def use_client(engine, **models):
    engine.brain._client = pytypes.SimpleNamespace(aio=pytypes.SimpleNamespace(models=pytypes.SimpleNamespace(**models)))
    engine.brain._client_key = engine.config.get_api_key()


def limited(model):
    return errors.ClientError(429, {"error": {"code": 429, "message": f"quota {model}", "status": "RESOURCE_EXHAUSTED"}})


async def test_discover_adds_the_newest_big_gemma_models_after_flash(engine):
    use_client(engine, list=fake_models(
        "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemma-3-27b-it", "gemma-3n-e4b-it", "gemma-4-e4b-it",
        *GEMMA, "embedding-gemma-300m"))
    info = await engine.brain.discover()
    assert info["spare_models"] == ["gemini-3.8-flash", "gemini-3.5-flash-lite", *GEMMA]


async def test_gemma_comes_last_in_both_tiers(config, engine):
    gov = Governor(config, engine.store)
    gov.set_extra(["gemini-3.5-flash", "gemini-3.1-flash-lite", *GEMMA])
    assert gov.chain("fast")[-4:] == ["gemini-3.1-flash-lite", "gemini-3.5-flash", *GEMMA]
    assert gov.chain("smart")[-4:] == ["gemini-3.5-flash", "gemini-3.1-flash-lite", *GEMMA]
    assert gov.limits(GEMMA[0]).rpd > gov.limits(config.settings.model_smart).rpd
    config.settings.extra_models = False
    assert not set(GEMMA) & set(gov.chain("fast"))


async def test_gemma_answers_when_every_flash_model_is_out(engine):
    engine.brain.governor.set_extra(GEMMA)
    asked: list[str] = []

    async def generate_content(model, contents, config):
        asked.append(model)
        if model.startswith("gemini"):
            raise limited(model)
        assert config.thinking_config is None  # Gemma has no thinking switch
        return pytypes.SimpleNamespace(text="from gemma")

    use_client(engine, generate_content=generate_content)
    assert await engine.brain.generate_text("hi") == "from gemma"
    assert asked[-1] == GEMMA[0] and all(m.startswith("gemini") for m in asked[:-1])


async def test_gemma_refusing_a_request_moves_on_instead_of_failing(engine):
    engine.brain.governor.set_extra(GEMMA)
    asked: list[str] = []

    async def generate_content(model, contents, config):
        asked.append(model)
        if model == GEMMA[0]:
            raise errors.ClientError(400, {"error": {"code": 400, "message": "JSON mode is not enabled", "status": "INVALID_ARGUMENT"}})
        if model.startswith("gemini"):
            raise limited(model)
        return pytypes.SimpleNamespace(text="{}")

    use_client(engine, generate_content=generate_content)
    assert await engine.brain.generate_text("hi", json_mode=True) == "{}"
    assert asked[-2:] == GEMMA


async def test_voice_transcription_never_goes_to_gemma(engine):
    engine.brain.governor.set_extra(GEMMA)
    asked: list[str] = []

    async def generate_content(model, contents, config):
        asked.append(model)
        raise limited(model)

    use_client(engine, generate_content=generate_content)
    with pytest.raises((QuotaExhausted, errors.APIError)):
        await engine.brain.transcribe(b"RIFF....WAVE")
    assert asked and not set(GEMMA) & set(asked)


async def test_streaming_with_tools_falls_back_to_gemma(engine):
    engine.brain.governor.set_extra(GEMMA)

    async def chunks():
        yield pytypes.SimpleNamespace(
            usage_metadata=None,
            candidates=[pytypes.SimpleNamespace(content=types.Content(role="model", parts=[types.Part(text="hi")]))])

    async def generate_content_stream(model, contents, config):
        if model.startswith("gemini"):
            raise limited(model)
        assert config.tools  # tools still go to Gemma
        return chunks()

    use_client(engine, generate_content_stream=generate_content_stream)
    tools = [{"name": "noop", "description": "does nothing", "parameters": {"type": "object", "properties": {}}}]
    out = [x async for x in engine.brain.stream(
        [types.Content(role="user", parts=[types.Part(text="hi")])], tools=tools)]
    assert ("model", GEMMA[0]) in out
    assert [v.text for k, v in out if k == "part"] == ["hi"]
