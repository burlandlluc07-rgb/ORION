import numpy as np
import pytest

from orion.voice import vad
from orion.voice.audio import FRAME, MIC_RATE


def tone(seconds: float, amp: float = 0.3, freq: float = 220.0) -> list[bytes]:
    """Voiced, speech-like audio (a harmonic-rich buzz) cut into 20 ms frames."""
    t = np.arange(int(MIC_RATE * seconds)) / MIC_RATE
    wave = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 12)) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    pcm = (wave / np.max(np.abs(wave)) * amp * 32767).astype(np.int16).tobytes()
    n = FRAME * 2
    return [pcm[i:i + n] for i in range(0, len(pcm) - n + 1, n)]


def silence(seconds: float) -> list[bytes]:
    rng = np.random.default_rng(1)
    pcm = (rng.normal(0, 30, int(MIC_RATE * seconds))).astype(np.int16).tobytes()
    n = FRAME * 2
    return [pcm[i:i + n] for i in range(0, len(pcm) - n + 1, n)]


def run(ep: vad.Endpointer, frames: list[bytes]) -> tuple[str, int]:
    for i, f in enumerate(frames):
        st = ep.feed(f)
        if st in ("done", "timeout"):
            return st, i
    return "speech" if ep.started else "waiting", len(frames)


def test_short_command_ends_quickly():
    frames = silence(0.5) + tone(0.8) + silence(2)
    st, i = run(vad.Endpointer(), frames)
    assert st == "done"
    end_of_speech = len(silence(0.5)) + len(tone(0.8))
    assert (i - end_of_speech) * vad.FRAME_S < 0.65  # ~0.5 s after a short command, not a fixed 0.9 s


def test_long_sentence_gets_a_longer_pause():
    ep = vad.Endpointer()
    frames = tone(2.5) + silence(0.7) + tone(0.6) + silence(2)
    st, i = run(ep, frames)
    assert st == "done" and i > len(tone(2.5)) + len(silence(0.7))  # the 0.7 s breath didn't end it
    assert ep.speech_seconds > 2.5


def test_nobody_speaks_times_out():
    st, _ = run(vad.Endpointer(start_timeout=1), silence(2))
    assert st == "timeout"


def test_last_utterance_finds_the_final_stretch():
    frames = tone(1.0) + silence(1.5) + tone(1.2) + silence(0.3)
    seg = vad.last_utterance(frames)
    secs = len(seg) / 2 / MIC_RATE
    assert 1.2 <= secs <= 1.2 + 0.6
    assert vad.last_utterance(silence(1)) == b""


def test_echo_guard_needs_speech_clearly_louder_than_the_echo():
    g = vad.EchoGuard(threshold=0.35)
    assert not any(g.feed(f, playing=True) for f in tone(1.0, amp=0.04))  # ORION's own voice, quietly
    g2 = vad.EchoGuard(threshold=0.35)
    assert any(g2.feed(f, playing=True) for f in tone(1.0, amp=0.6, freq=180))  # the user talking over it


def test_chime_is_short_and_soft():
    pcm = np.frombuffer(vad.chime("listen"), dtype=np.int16)
    assert 0.1 < len(pcm) / 24000 < 0.2 and np.max(np.abs(pcm)) < 0.2 * 32767


@pytest.mark.parametrize("voice,expect", [("en-GB-RyanNeural", ("en-GB", "male", 1)),
                                          ("en-US-AriaNeural", ("en-US", "female", 1)),
                                          ("fr-FR-DeniseNeural", ("fr-FR", "female", 1))])
def test_backup_voice_hints(voice, expect):
    from orion.voice.tts import backup_voice_hints

    assert backup_voice_hints(voice, "+8%") == expect
