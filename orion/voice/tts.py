"""Text-to-speech for classic voice mode.

Uses Microsoft Edge's neural voices via ``edge-tts`` (free, no key, very
natural — "en-GB-RyanNeural" is a convincing JARVIS) and decodes MP3 with
``miniaudio``. Falls back to the offline OS voice (pyttsx3/SAPI) when offline.
Speech is synthesised sentence by sentence so playback starts quickly.
"""

from __future__ import annotations

import asyncio
import logging
import re

from .audio import SPK_RATE, Speaker

log = logging.getLogger("orion.tts")

VOICES = [
    {"id": "en-GB-RyanNeural", "label": "Ryan — British, composed (JARVIS)"},
    {"id": "en-GB-ThomasNeural", "label": "Thomas — British, warm"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia — British, crisp (FRIDAY)"},
    {"id": "en-US-GuyNeural", "label": "Guy — American, confident"},
    {"id": "en-US-AndrewNeural", "label": "Andrew — American, natural"},
    {"id": "en-US-AriaNeural", "label": "Aria — American, bright"},
    {"id": "en-AU-WilliamNeural", "label": "William — Australian"},
]


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

    def _speak_offline(self, text: str) -> None:
        import pyttsx3  # type: ignore

        eng = pyttsx3.init()
        eng.say(text)
        eng.runAndWait()

    async def speak(self, text: str) -> None:
        text = speakable(text)
        if not text:
            return
        self._gen += 1
        gen = self._gen
        chunks = sentences(text)
        # Pipeline: synthesise the next sentence while the current one plays.
        pending = asyncio.create_task(self._synth_edge(chunks[0]))
        for i in range(len(chunks)):
            try:
                pcm = await pending
            except Exception as exc:
                log.warning("edge-tts failed (%s); using offline voice", exc)
                try:
                    await asyncio.to_thread(self._speak_offline, " ".join(chunks[i:]))
                except Exception:
                    log.warning("No offline TTS available")
                return
            if gen != self._gen:
                return
            if i + 1 < len(chunks):
                pending = asyncio.create_task(self._synth_edge(chunks[i + 1]))
            self.speaker.play(pcm)
        await self.speaker.wait_done()

    def stop(self) -> None:
        self._gen += 1
        self.speaker.clear()
