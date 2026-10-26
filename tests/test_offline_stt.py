"""Offline speech recognition: wake phrases in transcripts, the opt-in model download, and how voice uses it."""

import hashlib
import io
import os
import tarfile
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from orion.voice import offline_stt
from orion.voice.offline_stt import ModelSpec, ModelStore, match_wake

from .test_voice_manager import PCM, FakeSpeech, make_vm

PHRASES = ["Hey ORION", "Hey Jarvis", "Friday"]


# ── Wake phrases ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,want", [
    ("Hey Orion, what's the weather like today?", ("Hey ORION", "what's the weather like today?")),
    ("Hey Orion.", ("Hey ORION", "")),
    ("hey orien what time is it", ("Hey ORION", "what time is it")),  # small mishearing still counts
    ("Okay, hey Orion, pause the music.", ("Hey ORION", "pause the music.")),  # one filler word in front
    ("Hey Jarvis — set a timer for five minutes", ("Hey Jarvis", "set a timer for five minutes")),
    ("Friday, open my email.", ("Friday", "open my email.")),
])
def test_wake_phrase_at_the_start(text, want):
    assert match_wake(text, PHRASES) == want


@pytest.mark.parametrize("text", [
    "I watched a documentary about the Orion constellation last night.",  # a mention isn't a wake
    "Let us order a pizza and watch a movie tonight.",
    "Hey Oscar, how are you?",  # the name itself has to be close
    "Hey, Ryan.",
    "",
])
def test_no_wake(text):
    assert match_wake(text, PHRASES) is None


# ── Download, checksum and unpacking ───────────────────────────────────────
def _archive(files: dict[str, bytes], extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name, data in {**{f"model-dir/{k}": v for k, v in files.items()}, **(extra or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


FILES = {"encoder.int8.onnx": b"E" * 3000, "decoder.int8.onnx": b"D" * 200, "joiner.int8.onnx": b"J" * 100,
         "tokens.txt": b"a 0\nb 1\n"}


def _spec(blob: bytes, sha: str | None = None) -> ModelSpec:
    return ModelSpec(id="test-model", label="Test", url="https://example.test/model.tar.bz2",
                     sha256=sha or hashlib.sha256(blob).hexdigest(), download_bytes=len(blob),
                     files={k: len(v) for k, v in FILES.items()}, ram_mb=1, languages="test")


@pytest.fixture
def serve(monkeypatch):
    """Serve ``blob`` for the model URL through httpx, honouring Range requests like GitHub does."""
    seen: list[dict] = []
    real = httpx.Client

    def install(blob: bytes):
        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(dict(req.headers))
            rng = req.headers.get("range")
            if rng:
                start = int(rng.split("=")[1].rstrip("-"))
                return httpx.Response(206, content=blob[start:])
            return httpx.Response(200, content=blob)

        monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        return seen

    return install


def test_download_verifies_and_installs_only_the_model_files(tmp_path, serve):
    blob = _archive(FILES, extra={"../../evil.txt": b"x", "model-dir/README.md": b"hi"})
    serve(blob)
    store = ModelStore(tmp_path / "models", _spec(blob))
    events = []
    store.on_change = events.append
    assert not store.installed()
    store.start_download()
    store.wait(10)
    assert store.installed() and store.error is None and store.state == "idle"
    assert sorted(p.name for p in store.folder.iterdir()) == sorted([*FILES, "model.json"])
    assert not (tmp_path / "evil.txt").exists() and not list((tmp_path / "models").glob("*.part"))
    assert events[0]["state"] == "downloading" and events[-1]["installed"]


def test_checksum_mismatch_installs_nothing(tmp_path, serve):
    blob = _archive(FILES)
    serve(blob)
    store = ModelStore(tmp_path, _spec(blob, sha="0" * 64))
    store.start_download()
    store.wait(10)
    assert store.state == "error" and "checksum" in store.error
    assert not store.installed() and not store.folder.exists() and not list(tmp_path.glob("*.part"))


def test_archive_missing_a_file_is_rejected(tmp_path, serve):
    blob = _archive({k: v for k, v in FILES.items() if k != "tokens.txt"})
    serve(blob)
    store = ModelStore(tmp_path, _spec(blob))
    store.start_download()
    store.wait(10)
    assert store.state == "error" and "tokens.txt" in store.error and not store.installed()


def test_resumes_a_partial_download(tmp_path, serve):
    blob = _archive(FILES)
    seen = serve(blob)
    store = ModelStore(tmp_path, _spec(blob))
    (tmp_path / "test-model.tar.bz2.part").write_bytes(blob[:100])
    store.start_download()
    store.wait(10)
    assert store.installed() and seen[0].get("range") == "bytes=100-"


def test_not_enough_disk_space(tmp_path, serve, monkeypatch):
    blob = _archive(FILES)
    serve(blob)
    monkeypatch.setattr(offline_stt.shutil, "disk_usage", lambda p: SimpleNamespace(free=10))
    store = ModelStore(tmp_path, _spec(blob))
    store.start_download()
    store.wait(10)
    assert store.state == "error" and "disk space" in store.error


def test_remove(tmp_path, serve):
    blob = _archive(FILES)
    serve(blob)
    store = ModelStore(tmp_path, _spec(blob))
    store.start_download()
    store.wait(10)
    store.remove()
    assert not store.installed() and not store.folder.exists()


# ── Voice uses it first, Gemini second ─────────────────────────────────────
class FakeSTT:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def transcribe(self, pcm, rate=16000):
        self.calls += 1
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


def offline_vm(text, gemini=None, speech=None):
    vm, bus = make_vm(gemini=gemini, speech=speech)
    vm.e.config.settings.voice.offline_stt = True
    vm.stt = FakeSTT(text)
    return vm, bus


async def test_offline_transcript_skips_gemini():
    vm, bus = offline_vm("Hey Orion, what's the weather in Paris?", gemini=RuntimeError("must not be called"))
    assert await vm._understand(PCM) == ("text", "what's the weather in Paris?")
    assert [kw["text"] for k, kw in bus.events if k == "voice.transcript"] == ["what's the weather in Paris?"]


async def test_offline_transcript_can_be_an_instant_command():
    vm, _ = offline_vm("Pause the music.", gemini=RuntimeError("must not be called"))
    assert await vm._understand(PCM) == ("command", "pause")


async def test_gemini_is_the_fallback_when_offline_hears_nothing():
    vm, _ = offline_vm("", gemini="open notepad")
    assert await vm._understand(PCM) == ("text", "open notepad")
    vm, _ = offline_vm(RuntimeError("model crashed"), gemini="open notepad")
    assert await vm._understand(PCM) == ("text", "open notepad")


async def test_turned_off_means_not_used():
    vm, _ = offline_vm("from the offline model", gemini="from gemini")
    vm.e.config.settings.voice.offline_stt = False
    assert await vm._understand(PCM) == ("text", "from gemini") and vm.stt.calls == 0


async def test_spoken_yes_no_is_decided_offline():
    vm, _ = offline_vm("Yes, go ahead.", gemini=RuntimeError("must not be called"),
                       speech=FakeSpeech(dictation=("", 0.0)))
    assert await vm._yes_no_text(PCM) == "Yes, go ahead."
    vm, _ = offline_vm("hmm", gemini="no")  # unclear offline: Gemini gets a go
    assert await vm._yes_no_text(PCM) == "no"


async def test_offline_wake_passes_on_what_followed():
    vm, bus = offline_vm("Hey Orion, what's on my calendar?")
    vm.e.config.settings.voice.wake_phrases = ["Hey ORION"]
    got = []

    async def on_wake(hit, ring):
        got.append(hit)

    vm._on_wake, vm.muted, vm.live, vm._classic_task = on_wake, False, SimpleNamespace(running=False), None
    await vm._offline_wake(PCM, [])
    assert got == [("Hey ORION", 1.0, None, "what's on my calendar?")]
    assert ("voice.heard", {"phrase": "Hey ORION", "confidence": 1.0, "accepted": True}) in bus.events
    vm.stt = FakeSTT("I read about Orion")
    await vm._offline_wake(PCM, [])
    assert len(got) == 1


async def test_wake_with_an_offline_transcript_needs_no_gemini():
    vm, bus = make_vm()
    calls = []

    async def activate(preroll=None, first=None):
        calls.append(first)

    vm.activate = activate
    vm.speaker = SimpleNamespace(recently_played=lambda *a: False)
    vm.live = SimpleNamespace(running=False)
    vm._classic_task = None
    vm._ensure_conversation = lambda: None
    await vm._on_wake(("Hey ORION", 1.0, None, "what's the weather?"), [])
    await vm._on_wake(("Hey ORION", 1.0, None, ""), [])  # just the wake phrase: listen for the request
    assert calls == [("text", "what's the weather?"), None]


# ── API: nothing downloads without consent ─────────────────────────────────
async def test_download_needs_consent(engine, monkeypatch, tmp_path):
    from orion.server.api import create_app
    from fastapi.testclient import TestClient

    store = ModelStore(tmp_path)
    started = []
    monkeypatch.setattr(store, "start_download", lambda: started.append(True))
    monkeypatch.setattr(offline_stt, "STORE", store)
    monkeypatch.setattr(offline_stt, "runtime_available", lambda: True)
    client, h = TestClient(create_app(engine, token="t")), {"x-orion-token": "t"}
    st = client.get("/api/offline-speech", headers=h).json()
    assert st["installed"] is False and st["download_bytes"] > 400_000_000 and st["enabled"] is False
    assert client.post("/api/offline-speech/download", headers=h, json={}).status_code == 400
    assert not started
    assert client.post("/api/offline-speech/download", headers=h, json={"consent": True}).status_code == 200
    assert started == [True]


# ── The real model (only when it's on this machine) ────────────────────────
MODEL_DIR = os.environ.get("ORION_STT_MODEL_DIR")


@pytest.mark.skipif(not MODEL_DIR, reason="set ORION_STT_MODEL_DIR to a folder holding the downloaded model")
def test_real_model_transcribes():
    store = ModelStore(Path(MODEL_DIR))
    rec = offline_stt.load(store)
    wav = store.folder / "test_wavs" / "en.wav"
    if not wav.exists():
        pytest.skip("no sample recording next to the model")
    with wave.open(str(wav), "rb") as w:
        text = rec.transcribe(w.readframes(w.getnframes()), w.getframerate())
    assert "country" in text.lower()
