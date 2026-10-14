"""In-process async event bus.

The engine publishes everything the UI needs to show (tokens, tool steps,
approvals, audio levels, vitals) as small JSON-able dicts. The WebSocket
server simply forwards the stream to every connected window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger("orion.events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, maxsize: int = 1000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def emit(self, type_: str, **data: Any) -> None:
        """Publish an event. Safe to call from any thread."""
        event = {"type": type_, "ts": time.time(), **data}
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            loop.call_soon_threadsafe(self._fanout, event)
        else:
            self._fanout(event)

    def _fanout(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A slow client drops high-frequency events rather than stalling the engine.
                if event["type"] not in ("audio.level", "vitals"):
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except Exception:
                        pass
