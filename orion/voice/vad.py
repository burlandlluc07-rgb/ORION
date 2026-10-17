"""Knowing when someone is talking: voice-activity detection, endpointing, segmenting and the listening chime.

* :class:`Endpointer` decides, 20 ms frame by frame, when an utterance starts and when it has finished. It uses
  WebRTC's voice detector (tells speech from fans, typing and music) gated by loudness, and adapts the pause it
  waits for: short commands ("pause the music") end after ~0.5 s, longer sentences get ~0.9 s so a breath mid-
  sentence doesn't cut you off.
* :func:`last_utterance` finds the most recent spoken stretch in a buffer (the wake phrase + whatever followed).
* :class:`EchoGuard` tells the user talking over ORION apart from ORION's own voice coming back through the mic.
"""

from __future__ import annotations

import logging
import math

import numpy as np

from .audio import FRAME, MIC_RATE, SPK_RATE, rms_level

log = logging.getLogger("orion.vad")

FRAME_S = FRAME / MIC_RATE  # 20 ms


class Detector:
    """Per-frame speech yes/no: WebRTC VAD when installed, loudness otherwise, both relative to the noise floor."""

    def __init__(self, aggressiveness: int = 2) -> None:
        try:
            import webrtcvad  # type: ignore

            self.vad = webrtcvad.Vad(aggressiveness)
        except Exception:  # pragma: no cover - the package ships with ORION; this is only a safety net
            self.vad = None
        self.noise = 0.02

    def __call__(self, frame: bytes, learn: bool = True) -> bool:
        lv = rms_level(frame)
        loud = lv > max(0.05, self.noise * 2.5)
        if self.vad is not None and len(frame) == FRAME * 2:
            try:
                speech = loud and self.vad.is_speech(frame, MIC_RATE)
            except Exception:
                speech = loud
        else:
            speech = lv > max(0.08, self.noise * 3)
        if learn and not speech:
            self.noise = 0.95 * self.noise + 0.05 * lv
        return speech


class Endpointer:
    """Feed frames; ``feed`` returns "waiting", "speech", "done" or "timeout". ``pcm`` holds the utterance."""

    def __init__(self, start_timeout: float = 6.0, max_seconds: float = 15.0, short_pause: float = 0.5,
                 long_pause: float = 0.9, detector: Detector | None = None) -> None:
        self.detect = detector or Detector()
        self.start_timeout, self.max_seconds = start_timeout, max_seconds
        self.short_pause, self.long_pause = short_pause, long_pause
        self.pcm = bytearray()
        self.started = False
        self._recent: list[tuple[bytes, bool]] = []
        self._frames = 0
        self._speech_frames = 0
        self._silent_run = 0

    @property
    def speech_seconds(self) -> float:
        return self._speech_frames * FRAME_S

    def pause_needed(self) -> float:
        # Under ~1.2 s of speech it's probably a command: answer fast. Longer: allow for thinking pauses.
        t = min(1.0, max(0.0, (self.speech_seconds - 0.6) / 1.4))
        return self.short_pause + (self.long_pause - self.short_pause) * t

    def feed(self, frame: bytes) -> str:
        self._frames += 1
        speech = self.detect(frame, learn=not self.started)
        if not self.started:
            self._recent = (self._recent + [(frame, speech)])[-12:]
            if sum(s for _, s in self._recent[-5:]) >= 3:  # 60 ms of the last 100 ms: someone's talking
                self.started = True
                for f, s in self._recent:  # keep ~240 ms of lead-in so the first syllable isn't clipped
                    self.pcm.extend(f)
                    self._speech_frames += s
                self._recent = []
                return "speech"
            return "timeout" if self._frames * FRAME_S > self.start_timeout else "waiting"
        self.pcm.extend(frame)
        if speech:
            self._speech_frames += 1
            self._silent_run = 0
        else:
            self._silent_run += 1
        if self._silent_run * FRAME_S >= self.pause_needed() or len(self.pcm) / 2 / MIC_RATE >= self.max_seconds:
            return "done"
        return "speech"


def last_utterance(frames: list[bytes], gap: float = 0.6, detector: Detector | None = None) -> bytes:
    """The final spoken stretch in ``frames`` (pauses shorter than ``gap`` don't split it), with a little lead-in."""
    det = detector or Detector()
    flags = [det(f) for f in frames]
    end = max((i for i, s in enumerate(flags) if s), default=-1)
    if end < 0:
        return b""
    need = max(1, round(gap / FRAME_S))
    start, silent = end, 0
    for i in range(end, -1, -1):
        if flags[i]:
            start, silent = i, 0
        else:
            silent += 1
            if silent >= need:
                break
    start = max(0, start - 10)
    return b"".join(frames[start:min(len(frames), end + 15)])


class EchoGuard:
    """While ORION speaks, its own voice reaches the mic. Learn how loud that echo is and only count speech that's
    clearly louder (the user talking over it), sustained for ~300 ms."""

    def __init__(self, threshold: float = 0.35, hold: float = 0.3) -> None:
        self.detect = Detector(aggressiveness=3)
        self.threshold = threshold
        self.echo = 0.0
        self.need = max(1, round(hold / FRAME_S))
        self._hits: list[bool] = []

    def feed(self, frame: bytes, playing: bool) -> bool:
        lv = rms_level(frame)
        speech = self.detect(frame, learn=not playing)
        if playing:
            loud = lv > max(self.threshold, self.echo * 1.8)
            self.echo = max(lv, self.echo * 0.97) if not loud else self.echo  # follow the echo level, decay slowly
        else:
            loud = lv > max(0.08, self.detect.noise * 3)
            self.echo *= 0.9
        self._hits = (self._hits + [speech and loud])[-round(self.need * 1.4):]
        return sum(self._hits) >= self.need


def chime(kind: str = "listen", rate: int = SPK_RATE) -> bytes:
    """A short, soft two-note chime: rising when ORION starts listening, falling when it stops."""
    notes = {"listen": (660.0, 880.0), "done": (880.0, 587.3)}.get(kind, (660.0, 880.0))
    out = []
    for f in notes:
        n = int(rate * 0.075)
        t = np.arange(n) / rate
        env = np.minimum(1.0, np.minimum(t / 0.008, (n / rate - t) / 0.03))
        out.append(0.18 * np.sin(2 * math.pi * f * t) * env)
    wave = np.concatenate(out)
    return (wave * 32767).astype(np.int16).tobytes()
