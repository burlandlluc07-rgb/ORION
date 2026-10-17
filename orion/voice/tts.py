"""Text-to-speech for classic voice mode.

Uses Microsoft Edge's neural voices via ``edge-tts`` (free, no key, very
natural — "en-GB-RyanNeural" is a convincing JARVIS) and decodes MP3 with
``miniaudio``. When the neural voice can't be reached (offline, slow network) it falls back to an installed
Windows voice through the offline speech service, so ORION never goes silent.
Speech is synthesised sentence by sentence so playback starts quickly.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable

from .audio import SPK_RATE, Speaker

log = logging.getLogger("orion.tts")

VOICES = [
    {"id": "en-GB-RyanNeural", "label": "Ryan — British, composed (JARVIS)", "gender": "male"},
    {"id": "en-GB-ThomasNeural", "label": "Thomas — British, warm", "gender": "male"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia — British, crisp (FRIDAY)", "gender": "female"},
    {"id": "en-US-GuyNeural", "label": "Guy — American, confident", "gender": "male"},
    {"id": "en-US-AndrewNeural", "label": "Andrew — American, natural", "gender": "male"},
    {"id": "en-US-AriaNeural", "label": "Aria — American, bright", "gender": "female"},
    {"id": "en-AU-WilliamNeural", "label": "William — Australian", "gender": "male"},
]
EDGE_TIMEOUT = 6.0  # a sentence normally synthesises in well under a second
EDGE_RETRY = 60.0  # after a failure, use the backup voice for this long before trying the neural voice again


def backup_voice_hints(voice: str, rate: str = "+0%") -> tuple[str, str, int]:
    """(culture, gender, SAPI rate) for the offline Windows voice closest to the chosen neural voice."""
    culture = "-".join(voice.split("-")[:2]) if voice.count("-") >= 2 else "en-GB"
    gender = next((v["gender"] for v in VOICES if v["id"] == voice), "")
    if not gender:  # a voice picked outside the list: guess from common Edge voice names
        gender = "female" if re.search(r"Sonia|Aria|Jenny|Libby|Maisie|Natasha|Emma|Ava|Michelle|Clara|Elsa|Denise",
                                       voice) else "male"
    m = re.match(r"([+-]\d+)%", rate or "")
    sapi = max(-10, min(10, round(int(m.group(1)) / 10))) if m else 0
    return culture, gender, sapi


def speakable(text: str) -> str:
    t = re.sub(r"```.*?```", " I've put the code on screen. ", text, flags=re.S)
    t = re.sub(r"https?://\S+", "the link on screen", t)
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    out: list[str] = []
    for p in parts:
        if out and len(out[-1]) < 40:
            out[-1] += " " + p
        else:
            out.append(p)
    return [p for p in out if p.strip()]


class TTS:
    def __init__(self, speaker: Speaker, voice: str = "en-GB-RyanNeural", rate: str = "+8%") -> None:
        self.speaker = speaker
        self.voice = voice
        self.rate = rate
        self._gen = 0
        self.backup: Callable[[str, str, str, int], bytes] | None = None  # offline voice (set by the voice manager)
        self._edge_down_until = 0.0

    async def _synth_edge(self, text: str) -> bytes:
        import edge_tts
        import miniaudio

        comm = edge_tts.Communicate(text, self.voice, rate=self.rate)
        mp3 = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                mp3.extend(chunk["data"])
        if not mp3:
            return b""
        decoded = miniaudio.decode(bytes(mp3), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                   sample_rate=SPK_RATE)
        return decoded.samples.tobytes()

    async def synth(self, text: str) -> bytes:
        """One sentence → 24 kHz PCM: the neural voice, or the offline Windows voice when that's unreachable."""
        if time.monotonic() >= self._edge_down_until:
            try:
                pcm = await asyncio.wait_for(self._synth_edge(text), EDGE_TIMEOUT)
                if pcm:
                    return pcm
                raise RuntimeError("no audio returned")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.backup is None:
                    raise
                log.warning("Neural voice unavailable (%s); using the offline Windows voice", exc or "timed out")
                self._edge_down_until = time.monotonic() + EDGE_RETRY
        if self.backup is None:
            raise RuntimeError("the neural voice is unavailable and there's no offline voice")
        culture, gender, rate = backup_voice_hints(self.voice, self.rate)
        return await asyncio.to_thread(self.backup, text, culture, gender, rate)

    async def speak(self, text: str) -> None:
        text = speakable(text)
        if not text:
            return
        self._gen += 1
        gen = self._gen
        chunks = sentences(text)
        # Pipeline: synthesise the next sentence while the current one plays.
        pending = asyncio.create_task(self.synth(chunks[0]))
        for i in range(len(chunks)):
            try:
                pcm = await pending
            except Exception as exc:
                log.warning("Speech failed: %s", exc)
                return
            if gen != self._gen:
                return
            if i + 1 < len(chunks):
                pending = asyncio.create_task(self.synth(chunks[i + 1]))
            self.speaker.play(pcm)
        await self.speaker.wait_done()

    def stop(self) -> None:
        self._gen += 1
        self.speaker.clear()


_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(`*_-])|\n{2,}")


class SpeechStream:
    """Speak text while it is still being generated: each finished sentence is
    synthesised and queued immediately, so the first words play ~1 s after the
    model starts answering instead of after the whole reply."""

    def __init__(self, tts: TTS) -> None:
        self.tts = tts
        self.gen = tts._gen
        self.buf = ""
        self.q: asyncio.Queue[asyncio.Task | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._run())
        self.spoke = False

    def push(self, delta: str) -> None:
        self.buf += delta
        if self.buf.count("```") % 2:  # inside a code block: wait for it to close
            return
        parts = _BOUNDARY.split(self.buf)
        for sentence in parts[:-1]:
            self._emit(sentence)
        self.buf = parts[-1]

    def _emit(self, text: str) -> None:
        clean = speakable(text)
        if len(clean) > 1 and self.gen == self.tts._gen:
            # Start synthesising right away; playback below keeps sentence order.
            self.q.put_nowait(asyncio.create_task(self.tts.synth(clean)))

    def finish(self) -> None:
        self._emit(self.buf)
        self.buf = ""
        self.q.put_nowait(None)

    async def _run(self) -> None:
        while True:
            job = await self.q.get()
            if job is None:
                break
            if self.gen != self.tts._gen:  # interrupted: drain without playing
                job.cancel()
                continue
            try:
                pcm = await job
            except Exception as exc:
                log.warning("Streaming TTS failed: %s", exc)
                continue
            if self.gen == self.tts._gen and pcm:
                self.tts.speaker.play(pcm)
                self.spoke = True
        if self.gen == self.tts._gen:
            await self.tts.speaker.wait_done()

    async def wait(self) -> None:
        await self.task
