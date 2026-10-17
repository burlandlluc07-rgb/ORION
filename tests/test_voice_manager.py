"""Voice manager routing, without audio hardware: instant commands, transcripts, fallbacks."""

import asyncio
from types import SimpleNamespace

import pytest

from orion.config import VoiceSettings
from orion.voice import manager as vm_mod
from orion.voice.tts import TTS


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, kind, **kw):
        self.events.append((kind, kw))


class FakeSpeech:
    running = True
    listen = True

    def __init__(self, command=None, conf=0.0, dictation=("", 0.0)):
        self.command, self.conf, self.dictation = command, conf, dictation

    def match_command(self, path):
        return self.command, self.conf, "pause the music" if self.command else "something else"

    def dictate(self, path, timeout=20):
        return self.dictation


def make_vm(gemini=None, speech=None):
    vs = VoiceSettings(mode="classic", wake_phrases=["Hey ORION"])
    bus = Bus()

    async def transcribe(wav, hints=None):
        if isinstance(gemini, Exception):
            raise gemini
        await asyncio.sleep(0.05)
        return gemini or ""

    eng = SimpleNamespace(bus=bus, config=SimpleNamespace(settings=SimpleNamespace(voice=vs, user_name="Luc",
                                                                                   assistant_name="ORION")),
                          brain=SimpleNamespace(transcribe=transcribe), vault=SimpleNamespace(root=None))
    vm = object.__new__(vm_mod.VoiceManager)
    vm.e, vm.speech, vm.conv_id, vm.catalog = eng, speech, "c1", {"pause the music": "pause"}
    vm._hint_cache = (float("-inf"), [])
    return vm, bus


PCM = b"\x00\x10" * 16000  # 1 s


async def test_instant_command_skips_gemini():
    vm, bus = make_vm(gemini=RuntimeError("should not be called"), speech=FakeSpeech("pause", 0.93))
    assert await vm._understand(PCM) == ("command", "pause")


async def test_unsure_command_goes_to_gemini_with_a_quick_preview():
    vm, bus = make_vm(gemini="Hey Orion, what's the weather in Paris?",
                      speech=FakeSpeech("pause", 0.5, ("what's the weather in paris", 0.7)))
    assert await vm._understand(PCM) == ("text", "what's the weather in Paris?")
    transcripts = [kw for k, kw in bus.events if k == "voice.transcript"]
    assert transcripts[0]["tentative"] and transcripts[-1]["text"] == "what's the weather in Paris?"


async def test_offline_transcript_when_gemini_is_unreachable():
    vm, _ = make_vm(gemini=ConnectionError("offline"), speech=FakeSpeech(dictation=("open notepad", 0.8)))
    assert await vm._understand(PCM) == ("text", "open notepad")


async def test_without_windows_speech_errors_still_surface():
    vm, _ = make_vm(gemini=ConnectionError("offline"))
    with pytest.raises(ConnectionError):
        await vm._understand(PCM)


def test_hints_include_names():
    vm, _ = make_vm()
    assert vm._hints()[:3] == ["Luc", "ORION", "Hey ORION"]


async def test_wake_with_command_runs_it_directly(monkeypatch):
    vm, bus = make_vm()
    calls = []

    async def activate(preroll=None, first=None):
        calls.append(first)

    vm.activate = activate
    vm.speaker = SimpleNamespace(recently_played=lambda *a: False)
    vm.live = SimpleNamespace(running=False)
    vm._classic_task = None
    await vm._on_wake(("Hey Orion", 0.92, "volume 30"), [])
    await vm._on_wake(("Hey Orion", 0.70, "volume 30"), [])  # not confident: treated as a plain wake
    assert calls == [("command", "volume 30"), None]


async def test_backup_voice_when_the_neural_voice_fails():
    class Broken(TTS):
        async def _synth_edge(self, text):
            raise ConnectionError("offline")

    t = Broken(SimpleNamespace())
    t.backup = lambda text, culture, gender, rate: f"{text}|{culture}|{gender}|{rate}".encode()
    assert await t.synth("Hello") == b"Hello|en-GB|male|1"
    assert t._edge_down_until > 0  # skips the neural voice for a while instead of waiting on it every sentence
    t.backup = None
    with pytest.raises(RuntimeError):
        await t.synth("Hi")
