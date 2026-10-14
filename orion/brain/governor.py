"""Free-tier governor.

Keeps ORION inside Gemini's free quotas *before* Google starts returning 429s:

* tracks requests per minute (sliding window) and per day (persisted; the
  quota day resets at midnight Pacific time, like Google's),
* picks the first model in a tier's chain that has headroom,
* waits briefly for a free RPM slot instead of failing,
* cools a model down when the API still answers 429, honouring retryDelay.
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

    def limits(self, model: str) -> ModelLimits:
        lim = self.config.settings.model_limits.get(model)
        if lim:
            return lim
        return ModelLimits(rpm=15, rpd=1000) if "lite" in model else ModelLimits(rpm=5, rpd=250)

    def _st(self, model: str) -> ModelState:
        return self._state.setdefault(model, ModelState(collections.deque()))

    def chain(self, tier: str) -> list[str]:
        s = self.config.settings
        order = [s.model_smart, s.model_fast] if tier == "smart" else [s.model_fast, s.model_smart]
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
                for model in self.chain(tier):
                    if model in exclude:
                        continue
                    st, lim = self._st(model), self.limits(model)
                    if st.cooldown_until > now:
                        soonest_cool = min(soonest_cool or st.cooldown_until, st.cooldown_until)
                        continue
                    if self.used_today(model) >= lim.rpd:
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
                    raise QuotaExhausted(
                        "Gemini free-tier limit reached for now" + (f"; try again in {int(retry) + 1}s." if retry else
                                                                   " for today. Quotas reset at midnight Pacific time."),
                        retry,
                    )
            await asyncio.sleep(min(retry, 5.0) + 0.05)  # re-check: a cooldown may end or a slot may free up

    def record(self, model: str, tokens: int = 0) -> None:
        self.store.add_usage(model, quota_day(), tokens)

    def cool_down(self, model: str, error_text: str) -> float:
        m = re.search(r"retry(?:Delay| in)[\"':\s]*([\d.]+)s", error_text, re.I)
        delay = float(m.group(1)) if m else 30.0
        if "PerDay" in error_text or "per day" in error_text.lower():
            delay = max(delay, 3600.0)
        self._st(model).cooldown_until = time.time() + delay
        return delay

    def snapshot(self) -> list[dict]:
        now = time.time()
        out = []
        for model in dict.fromkeys(self.chain("fast") + self.chain("smart")):
            st, lim = self._st(model), self.limits(model)
            recent = sum(1 for t in st.window if now - t < 60)
            out.append({
                "model": model, "rpm_used": recent, "rpm": lim.rpm, "rpd_used": self.used_today(model),
                "rpd": lim.rpd, "cooldown": max(0, round(st.cooldown_until - now)),
            })
        return out
