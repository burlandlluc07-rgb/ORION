
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
    assert gov.cool_down("m", "quota exceeded GenerateRequestsPerDayPerProjectPerModel") >= 3600
    assert quota_day()
