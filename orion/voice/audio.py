"""Low-latency audio I/O on top of sounddevice (PortAudio).

* :class:`Microphone` — 16 kHz mono int16 frames, fanned out to any number of
  async consumers (wake word, Live session, recorder) and a level meter.
* :class:`Speaker` — 24 kHz mono int16 playback from a ring buffer that can be
  flushed instantly when the user interrupts (barge-in).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

import numpy as np

log = logging.getLogger("orion.audio")

MIC_RATE = 16_000
SPK_RATE = 24_000
FRAME = 320  # 20 ms at 16 kHz


def rms_level(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if a.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(a * a))) / 32768.0
    return min(1.0, rms * 6.0)  # perceptual-ish scaling for the UI


def list_devices() -> dict:
    try:
        import sounddevice as sd

        devs = sd.query_devices()
        default_in, default_out = sd.default.device
        return {
            "inputs": [{"id": i, "name": d["name"], "default": i == default_in}
                       for i, d in enumerate(devs) if d["max_input_channels"] > 0],
            "outputs": [{"id": i, "name": d["name"], "default": i == default_out}
                        for i, d in enumerate(devs) if d["max_output_channels"] > 0],
        }
    except Exception as exc:
        return {"inputs": [], "outputs": [], "error": str(exc)}


class Microphone:
    def __init__(self, loop: asyncio.AbstractEventLoop, on_level: Callable[[float], None], device: int | None = None) -> None:
        self.loop = loop
        self.on_level = on_level
        self.device = device
        self._stream = None
        self._subs: set[asyncio.Queue] = set()
        self._last_level = 0.0
        self.running = False

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=400)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _push(self, data: bytes) -> None:
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(data)

    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        data = bytes(indata)
        now = time.monotonic()
        if now - self._last_level > 0.06:
            self._last_level = now
            self.on_level(rms_level(data))
        self.loop.call_soon_threadsafe(self._push, data)

    def start(self) -> None:
        if self.running:
            return
        import sounddevice as sd

        self._stream = sd.RawInputStream(samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=FRAME * 2,
                                         device=self.device, callback=self._callback)
        self._stream.start()
        self.running = True
        log.info("Microphone started")

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self.running = False


class Speaker:
    def __init__(self, on_level: Callable[[float], None], device: int | None = None, rate: int = SPK_RATE) -> None:
        self.on_level = on_level
        self.device = device
        self.rate = rate
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream = None
        self._last_level = 0.0
        self._idle_since = time.monotonic()
        self.done = threading.Event()
        self.done.set()

    def _callback(self, outdata, frames, time_info, status) -> None:
        need = frames * 2
        with self._lock:
            chunk = bytes(self._buf[:need])
            del self._buf[:need]
            empty = not self._buf
        if len(chunk) < need:
            chunk += b"\x00" * (need - len(chunk))
        outdata[:] = chunk
        now = time.monotonic()
        if now - self._last_level > 0.06:
            self._last_level = now
            self.on_level(rms_level(chunk))
        if empty and not self.done.is_set():
            self._idle_since = now
            self.done.set()

    def _ensure(self) -> None:
        if self._stream is None:
            import sounddevice as sd

            self._stream = sd.RawOutputStream(samplerate=self.rate, channels=1, dtype="int16", blocksize=480,
                                              device=self.device, callback=self._callback)
            self._stream.start()

    def play(self, pcm: bytes) -> None:
        self._ensure()
        with self._lock:
            self._buf.extend(pcm)
        self.done.clear()

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
        self.done.set()

    @property
    def playing(self) -> bool:
        return not self.done.is_set()

    def recently_played(self, within: float = 0.4) -> bool:
        return self.playing or time.monotonic() - self._idle_since < within

    async def wait_done(self) -> None:
        while self.playing:
            await asyncio.sleep(0.05)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None


def pcm_to_wav(pcm: bytes, rate: int = MIC_RATE) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
