
import pytest

from orion.brain.governor import Governor, QuotaExhausted, quota_day
from orion.config import ModelLimits


def test_conversations_and_memory(engine):
    st = engine.store
    c = st.create_conversation("Hello")
    st.add_message(c["id"], "user", "hi")
    st.add_message(c["id"], "assistant", "hello")
    assert [m["role"] for m in st.messages(c["id"])] == ["user", "assistant"]
    st.remember("User's sister is called Maya")
    st.remember("User likes jazz music", "profile")
    st.remember("user likes jazz music")  # duplicate (case-insensitive) is ignored
    assert len(st.list_memories()) == 2
    assert st.recall("what is my sister called?")[0]["text"].startswith("User's sister")
    assert st.core_memories()[0]["text"] == "User likes jazz music"


async def test_governor_falls_back_and_exhausts(config, engine):
    config.settings.model_limits = {
        config.settings.model_fast: ModelLimits(rpm=2, rpd=100),
        config.settings.model_smart: ModelLimits(rpm=1, rpd=1),
    }
    gov = Governor(config, engine.store)
    fast, smart = config.settings.model_fast, config.settings.model_smart
    assert await gov.acquire("fast") == fast
    assert await gov.acquire("fast") == fast
    assert await gov.acquire("fast") == smart  # fast model's RPM is full → fall back
    gov.record(smart)
    with pytest.raises(QuotaExhausted):
        await gov.acquire("smart", exclude={fast})  # smart's daily quota is used up


def test_cool_down_parses_retry_delay(config, engine):
    gov = Governor(config, engine.store)
    assert gov.cool_down("m", "429 RESOURCE_EXHAUSTED ... 'retryDelay': '17s'") == 17.0
    assert gov.cool_down("m", "quota exceeded GenerateRequestsPerDayPerProjectPerModel") >= 60  # until the reset
    assert quota_day()


ERR_DAY = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota. Please "
           "retry in 6h25m32.1s.', 'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': "
           "[{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_input_token_count', "
           "'quotaId': 'GenerateContentInputTokensPerModelPerMinute-FreeTier', 'quotaValue': '250000'}, "
           "{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', "
           "'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]}]}}")


def test_parsers_handle_real_429_shapes():
    from orion.brain.governor import parse_quota, parse_retry_delay

    assert parse_retry_delay(ERR_DAY) == pytest.approx(6 * 3600 + 25 * 60 + 32.1)
    assert parse_retry_delay("'retryDelay': '23s'") == 23
    assert parse_retry_delay("Please retry in 41.5s.") == 41.5
    assert parse_quota(ERR_DAY) == ("day", 20)  # the token quota is skipped
    assert parse_quota("'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaValue': '5'") == ("minute", 5)
    assert parse_quota("something else") == (None, None)


async def test_governor_learns_limits_and_remembers_daily_cooldown(config, engine):
    gov = Governor(config, engine.store)
    smart = config.settings.model_smart
    delay = gov.cool_down(smart, ERR_DAY)
    assert delay > 6 * 3600
    assert gov.limits(smart).rpd == 20
    assert Governor(config, engine.store)._st(smart).cooldown_until > 0  # survives a restart
    assert await gov.acquire("smart") != smart  # falls back to the other model


async def test_spare_models_extend_the_chain(config, engine):
    gov = Governor(config, engine.store)
    gov.set_extra(["gemini-3.5-flash", "gemini-3.1-flash-lite", config.settings.model_fast])
    assert gov.chain("fast")[-2:] == ["gemini-3.1-flash-lite", "gemini-3.5-flash"]
    assert gov.chain("smart")[2] == "gemini-3.5-flash"
    config.settings.extra_models = False
    assert len(gov.chain("fast")) == 2


async def test_patient_acquire_waits_out_a_short_cooldown(config, engine):
    import time

    gov = Governor(config, engine.store)
    for m in gov.chain("smart"):
        gov._st(m).cooldown_until = time.time() + 0.3
    with pytest.raises(QuotaExhausted):
        await gov.acquire("smart", max_wait=0.1)  # interactive: don't wait that long
    assert await gov.acquire("smart", max_wait=5) in gov.chain("smart")  # background: wait it out
