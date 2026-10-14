"""Free-tier governor.

Keeps ORION inside Gemini's free quotas *before* Google starts returning 429s:

* tracks requests per minute (sliding window) and per day (persisted; the
  quota day resets at midnight Pacific time, like Google's),
* picks the first model in a tier's chain that has headroom,
* waits briefly for a free RPM slot instead of failing,
* cools a model down when the API still answers 429, honouring retryDelay,
* learns each model's real limits from Google's 429 details (free-tier numbers
  change and are no longer published), and remembers long cooldowns across restarts,
* spreads work over extra discovered models, since each has its own free quota.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import re
import time
from dataclasses import dataclass

from ..config import Config, ModelLimits
from ..memory.store import Store

MAX_SLOT_WAIT = 6.0  # seconds we're willing to wait for an RPM slot before trying another model


def quota_day() -> str:
    try:
        from zoneinfo import ZoneInfo

        now = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        now = dt.datetime.utcnow() - dt.timedelta(hours=8)
    return now.strftime("%Y-%m-%d")


def seconds_to_reset() -> float:
    """Seconds until the free-tier daily quota resets (midnight Pacific)."""
    try:
        from zoneinfo import ZoneInfo

        now = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        now = dt.datetime.utcnow() - dt.timedelta(hours=8)
    midnight = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60.0, (midnight - now).total_seconds())


def parse_retry_delay(text: str) -> float | None:
    """retryDelay '23s' / 'retry in 23.1s' / Go-style 'retry in 6h25m32s'."""
    m = re.search(r"retryDelay[\"':\s]*([\d.]+)s", text)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in ((?:[\d.]+h)?(?:[\d.]+m(?!s))?(?:[\d.]+s)?)", text, re.I)
    if m and m.group(1):
        total = 0.0
        for val, unit in re.findall(r"([\d.]+)([hms])", m.group(1)):
            total += float(val) * {"h": 3600, "m": 60, "s": 1}[unit]
        return total or None
    return None


def parse_quota(text: str) -> tuple[str | None, int | None]:
    """→ ("day" | "minute" | None, request limit) from a 429 QuotaFailure. Token quotas are ignored."""
    for qid, val in re.findall(r"quotaId[\"':\s]*([\w-]+).*?quotaValue[\"':\s]*(\d+)", text, re.S):
        if "Request" not in qid:
            continue
        kind = "day" if "PerDay" in qid else "minute" if "PerMinute" in qid else None
        if kind:
            return kind, int(val)
    if "PerDay" in text or "per day" in text.lower():
        return "day", None
    return None, None


class QuotaExhausted(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class ModelState:
    window: collections.deque
    cooldown_until: float = 0.0


class Governor:
    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store
        self._state: dict[str, ModelState] = {}
        self._lock = asyncio.Lock()
        self.extra: list[str] = []  # discovered spare models (own quotas), used after the configured ones
        now = time.time()
        for model, until in (store.kv_get("model_cooldowns", {}) or {}).items():
            if until > now:
                self._st(model).cooldown_until = until

    def limits(self, model: str) -> ModelLimits:
        learned = (self.store.kv_get("learned_limits", {}) or {}).get(model, {})
        lim = self.config.settings.model_limits.get(model)
        if lim is None:
            lim = ModelLimits(rpm=15, rpd=500) if "lite" in model else ModelLimits(rpm=5, rpd=20)
        if learned:
            lim = ModelLimits(rpm=learned.get("rpm", lim.rpm), rpd=learned.get("rpd", lim.rpd))
        return lim

    def set_extra(self, models: list[str]) -> None:
        self.extra = [m for m in models if m not in (self.config.settings.model_fast, self.config.settings.model_smart)]

    def _st(self, model: str) -> ModelState:
        return self._state.setdefault(model, ModelState(collections.deque()))

    def chain(self, tier: str) -> list[str]:
        s = self.config.settings
        order = [s.model_smart, s.model_fast] if tier == "smart" else [s.model_fast, s.model_smart]
        if s.extra_models:
            lite = [m for m in self.extra if "lite" in m]
            full = [m for m in self.extra if "lite" not in m]
            order += (full + lite) if tier == "smart" else (lite + full)
        return list(dict.fromkeys(m for m in order if m))

    def used_today(self, model: str) -> int:
        return self.store.usage_for(quota_day()).get(model, {}).get("requests", 0)

    def _rpm_wait(self, model: str, now: float) -> float:
        st, lim = self._st(model), self.limits(model)
        while st.window and now - st.window[0] >= 60:
            st.window.popleft()
        if len(st.window) < lim.rpm:
            return 0.0
        return 60 - (now - st.window[0]) + 0.05

    async def acquire(self, tier: str, exclude: set[str] | None = None, max_wait: float = MAX_SLOT_WAIT) -> str:
        """Reserve a request slot and return the model to use.

        Interactive requests wait at most a few seconds; background work (missions, routines,
        learning) passes a larger ``max_wait`` and patiently waits out per-minute limits."""
        exclude = exclude or set()
        deadline = time.time() + max_wait
        while True:
            async with self._lock:
                now = time.time()
                best: tuple[float, str] | None = None
                soonest_cool = None
                daily_out = 0
                for model in self.chain(tier):
                    if model in exclude:
                        continue
                    st, lim = self._st(model), self.limits(model)
                    if st.cooldown_until > now:
                        soonest_cool = min(soonest_cool or st.cooldown_until, st.cooldown_until)
                        continue
                    if self.used_today(model) >= lim.rpd:
                        daily_out += 1
                        continue
                    wait = self._rpm_wait(model, now)
                    if wait == 0:
                        st.window.append(now)
                        return model
                    if best is None or wait < best[0]:
                        best = (wait, model)
                candidates = [w for w in ((best[0] if best else None), (soonest_cool - now if soonest_cool else None)) if w]
                retry = min(candidates) if candidates else None
                if retry is None or now + retry > deadline:
                    raise QuotaExhausted(self._exhausted_message(retry, daily_out), retry)
            await asyncio.sleep(min(retry, 5.0) + 0.05)  # re-check: a cooldown may end or a slot may free up

    @staticmethod
    def _exhausted_message(retry: float | None, daily_out: int) -> str:
        if retry is not None and retry < 120:
            return f"Gemini free-tier limit reached for now; try again in {int(retry) + 1}s."
        if retry is not None:
            hours, mins = divmod(int(retry) // 60, 60)
            when = f"{hours}h {mins}m" if hours else f"{mins + 1} min"
            return f"Gemini's free daily quota is used up; it comes back in about {when}."
        if daily_out:
            return "Gemini's free daily quota is used up. Quotas reset at midnight Pacific time."
        return "Gemini is busy right now (every model failed or is rate-limited). Try again in a minute."

    def record(self, model: str, tokens: int = 0) -> None:
        self.store.add_usage(model, quota_day(), tokens)

    def cool_down(self, model: str, error_text: str) -> float:
        """Handle a 429: learn the real limit if Google says it, and rest the model accordingly."""
        delay = parse_retry_delay(error_text)
        kind, value = parse_quota(error_text)
        if kind and value is not None:
            learned = self.store.kv_get("learned_limits", {}) or {}
            learned.setdefault(model, {})["rpd" if kind == "day" else "rpm"] = value
            self.store.kv_set("learned_limits", learned)
        if kind == "day":
            delay = max(delay or 0, 600.0) if delay else seconds_to_reset()
        delay = delay or 30.0
        until = time.time() + delay
        self._st(model).cooldown_until = until
        if delay > 300:  # survive a restart: don't hammer a model that's out for the day
            saved = {m: u for m, u in (self.store.kv_get("model_cooldowns", {}) or {}).items() if u > time.time()}
            saved[model] = until
            self.store.kv_set("model_cooldowns", saved)
        return delay

    def snapshot(self) -> list[dict]:
        now = time.time()
        out = []
        for model in dict.fromkeys(self.chain("fast") + self.chain("smart")):
            if model in self.extra and not self._st(model).window and not self.used_today(model) \
                    and self._st(model).cooldown_until <= now:
                continue  # unused spare models stay out of the usage panel
            st, lim = self._st(model), self.limits(model)
            recent = sum(1 for t in st.window if now - t < 60)
            out.append({
                "model": model, "rpm_used": recent, "rpm": lim.rpm, "rpd_used": self.used_today(model),
                "rpd": lim.rpd, "cooldown": max(0, round(st.cooldown_until - now)),
            })
        return out
