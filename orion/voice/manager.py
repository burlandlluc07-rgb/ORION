"""Voice manager: owns the mic/speaker, wake word, and the active voice mode.

States (shown by the UI's reactor orb):
    off · standby · listening · thinking · working · speaking · connecting · error

Modes:
    live     Gemini Live native audio (fastest, most natural).
    classic  Record → Gemini transcription → ORION agent → edge-tts speech.
             Uses the normal text pipeline, so the local fast path applies.

Speed and smarts on top of both (Windows, all offline):
    * Instant commands: "Hey ORION, pause the music" runs straight away, with no transcription and no Gemini call.
    * Smart end-of-speech (WebRTC VAD), follow-up listening without the wake word, talking over ORION to interrupt.
    * Names from your vault as transcription hints, a quick offline transcript preview, and an offline backup voice.

Optional offline speech recognition (offline_stt.py, a model downloaded from Settings): classic-mode speech and
spoken yes/no are transcribed on the PC first, Gemini only when that gives nothing; Windows wake-word near-misses
get a second listen; and the "offline" wake engine finds the wake phrase in its own transcripts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
import time
from typing import TYPE_CHECKING

import numpy as np

from ..tasks.tracker import parse_yes_no
from .audio import FRAME, MIC_RATE, Microphone, Speaker, pcm_to_wav
from .commands import STOP, build_catalog
from .live import LiveSession
from .offline_stt import STORE as STT_STORE
from .offline_stt import match_wake
from .tts import TTS
from .vad import EchoGuard, Endpointer, chime, last_utterance

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.voice")

COMMAND_CONFIDENCE = 0.8  # instant commands need a confident match; anything less goes to Gemini instead
RING_FRAMES = 500  # 10 s of audio kept so the words said with the wake phrase aren't lost
RECHECK_GAP = 1.5  # seconds: one second listen per utterance, however many near-misses Windows reports for it


class WakeWord:
    """openWakeWord "hey jarvis" detector (optional dependency, runs fully offline)."""

    def __init__(self, model_name: str = "hey_jarvis") -> None:
        import openwakeword  # type: ignore
        from openwakeword.model import Model  # type: ignore

        try:
            self.model = Model(wakeword_models=[model_name], inference_framework="onnx")
        except Exception:
            openwakeword.utils.download_models([model_name])
            self.model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self._buf = bytearray()

    def feed(self, pcm: bytes) -> bool:
        self._buf.extend(pcm)
        hit = False
        while len(self._buf) >= 2560:  # 80 ms frames
            frame = np.frombuffer(bytes(self._buf[:2560]), dtype=np.int16)
            del self._buf[:2560]
            scores = self.model.predict(frame)
            if any(v > 0.5 for v in scores.values()):
                hit = True
        if hit:
            self.model.reset()
        return hit


class VoiceManager:
    stt = None  # offline_stt.Recognizer once the downloaded model is loaded
    stt_error: str | None = None
    _recheck = False
    _recheck_at = float("-inf")
    _offline_busy = False
    _stt_task: asyncio.Task | None = None

    def __init__(self, engine: "Engine") -> None:
        self.e = engine
        self.loop = asyncio.get_running_loop()
        vs = engine.config.settings.voice
        self.mic = Microphone(self.loop, lambda lv: engine.bus.emit("audio.level", source="mic", level=lv), vs.input_device)
        self.speaker = Speaker(lambda lv: engine.bus.emit("audio.level", source="out", level=lv), vs.output_device)
        self.tts = TTS(self.speaker, vs.tts_voice, vs.tts_rate)
        self.live = LiveSession(engine, self)
        self.state = "off"
        self.detail = ""
        self.muted = False
        self.barge_in_threshold = 0.35
        self.conv_id: str | None = None
        self.wake: WakeWord | None = None  # openWakeWord engine (optional)
        self.speech = None  # wake.SpeechService: Windows offline recogniser + voice
        self.catalog: dict[str, str] = {}
        self._hits: asyncio.Queue = asyncio.Queue()
        self._wake_task: asyncio.Task | None = None
        self._speech_task: asyncio.Task | None = None
        self._classic_task: asyncio.Task | None = None
        self._next: tuple | None = None  # input that arrived mid-turn (wake word while busy)
        self._hint_cache: tuple[float, list[str]] = (float("-inf"), [])  # monotonic() is < 120 s after boot
        self.available = True
        self.error: str | None = None
        self._approval_id: str | None = None
        self._approval_offset = 0
        self._last_user_text = ""
        engine.approvals.on_voice_request = self.on_approval
        engine.approvals.voice_active = lambda: self.live.running

    # ── State ──────────────────────────────────────────────────────────────
    def set_state(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.e.bus.emit("voice.state", state=state, detail=detail, muted=self.muted, mode=self.mode, conv_id=self.conv_id)

    @property
    def mode(self) -> str:
        return self.e.config.settings.voice.mode

    @property
    def vs(self):
        return self.e.config.settings.voice

    @property
    def listening_for_wake(self) -> bool:
        return self.wake is not None or bool(self.speech and self.speech.listen and self.speech.running)

    def status(self) -> dict:
        sp = self.speech
        return {"state": self.state, "detail": self.detail, "muted": self.muted, "mode": self.mode,
                "wake_word": self.listening_for_wake,
                "wake_phrases": sp.phrases if sp is not None and sp.listen else None,
                "wake_language": sp.language if sp is not None else "",
                "instant_commands": len(self.catalog) if sp is not None and sp.running else 0,
                "offline_voice": bool(sp is not None and sp.running),
                "offline_stt": self._stt_ready, "offline_stt_error": self.stt_error,
                "live_model": self.e.brain.live_model, "error": self.error}

    # ── Lifecycle ──────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self.mode == "off":
            self.set_state("off")
            return
        try:
            self.mic.start()
        except Exception as exc:
            self.available = False
            self.error = f"No microphone available ({exc})"
            self.set_state("unavailable", self.error)
            return
        vs = self.vs
        if vs.wake_word and vs.wake_engine == "openwakeword":
            try:
                self.wake = await asyncio.to_thread(WakeWord, vs.wake_word_model)
            except ImportError:
                self.error = "The openWakeWord engine isn't installed; use the built-in Windows engine instead."
                log.info(self.error)
            except Exception as exc:
                self.error = f"Wake word failed: {exc}"
                log.warning(self.error)
        if not vs.offline_stt:
            self.stt = None  # turned off: free the memory
        elif self.stt is None and STT_STORE.installed() and not (self._stt_task and not self._stt_task.done()):
            self._stt_task = asyncio.create_task(self._load_stt(), name="offline-stt")
        if sys.platform == "win32":
            # The Windows speech service starts in the background (the first run compiles a small helper).
            self._speech_task = asyncio.create_task(self._start_speech(), name="speech-service")
        elif vs.wake_word and vs.wake_engine == "windows":
            self.error = "The built-in wake word engine needs Windows."
        if vs.wake_word and vs.wake_engine == "offline" and not (vs.offline_stt and STT_STORE.installed()):
            self.error = "The offline wake engine needs the offline speech model: download it under Voice."
        self._wake_task = asyncio.create_task(self._wake_loop(), name="wake-word")
        self.set_state("standby")

    async def _load_stt(self) -> None:
        from .offline_stt import load

        try:
            self.stt = await asyncio.to_thread(load, STT_STORE)
            self.stt_error = None
        except Exception as exc:
            self.stt_error = str(exc)
            log.warning("Offline speech recognition unavailable: %s", exc)
        self.set_state(self.state, self.detail)

    @property
    def _stt_ready(self) -> bool:
        return self.stt is not None and self.vs.offline_stt

    async def _transcribe_offline(self, pcm: bytes) -> str:
        try:
            return await asyncio.to_thread(self.stt.transcribe, pcm)  # type: ignore[union-attr]
        except Exception as exc:
            log.warning("Offline transcription failed: %s", exc)
            return ""

    async def _start_speech(self) -> None:
        from .wake import SpeechService

        vs, loop = self.vs, self.loop
        listen = vs.wake_word and vs.wake_engine == "windows"
        if vs.instant_commands:
            self.catalog = await asyncio.to_thread(self._build_catalog)

        def heard(text: str, conf: float, accepted: bool) -> None:
            loop.call_soon_threadsafe(lambda: self.e.bus.emit("voice.heard", phrase=text, confidence=round(conf, 2),
                                                              accepted=accepted))
            if not accepted and self._stt_ready:
                self._recheck = True  # Windows wasn't sure: the offline model gets a second listen

        def hit(phrase: str, conf: float, command: str | None) -> None:
            loop.call_soon_threadsafe(self._hits.put_nowait, (phrase, conf, command))

        def error(msg: str) -> None:
            self.error = msg
            loop.call_soon_threadsafe(lambda: self.set_state(self.state, self.detail))

        try:
            sp = SpeechService(vs.wake_phrases, self.catalog, vs.wake_language, listen, vs.wake_sensitivity,
                               hit, heard, error)
        except ValueError as exc:
            self.error = str(exc)
            return
        self.speech = sp
        ok = await asyncio.to_thread(sp.start)
        if sp.fatal or not ok:
            log.warning("Windows speech unavailable: %s", sp.fatal or "timed out starting")
            return
        self.tts.backup = sp.synthesize  # the offline voice when the neural one can't be reached
        log.info("Speech service ready (%s): wake=%s, %d instant commands", sp.language,
                 sp.phrases if listen else "off", len(self.catalog))
        self.set_state(self.state, self.detail)

    def _build_catalog(self) -> dict[str, str]:
        from ..tools.apps import INDEX

        try:
            INDEX.ready.wait(timeout=10)
            apps = INDEX.names()
        except Exception:
            apps = []
        return build_catalog(apps, smart_home=self.e.registry.get("home_control") is not None)

    async def stop(self) -> None:
        await self.live.stop()
        for t in (self._wake_task, self._classic_task, self._speech_task):
            if t:
                t.cancel()
        # The offline model (self.stt) stays loaded: settings changes restart voice and loading takes seconds.
        self.tts.stop()
        self.tts.backup = None
        self.mic.stop()
        self.speaker.close()
        if self.speech is not None:
            await asyncio.to_thread(self.speech.stop)
        self.speech = None
        self.wake = None
        self.set_state("off")

    async def restart(self) -> None:
        await self.stop()
        vs = self.vs
        self.mic.device, self.speaker.device = vs.input_device, vs.output_device
        self.tts.voice, self.tts.rate = vs.tts_voice, vs.tts_rate
        await self.start()

    # ── Activation ─────────────────────────────────────────────────────────
    def _ensure_conversation(self) -> None:
        if self.conv_id is None or self.e.store.get_conversation(self.conv_id) is None:
            conv = self.e.store.create_conversation(time.strftime("Voice · %d %b %H:%M"))
            self.conv_id = conv["id"]
            self.e.bus.emit("conversation.created", conversation=conv)

    @property
    def busy(self) -> bool:
        return self.live.running or bool(self._classic_task and not self._classic_task.done())

    async def activate(self, preroll: list[bytes] | None = None, first: tuple | None = None) -> None:
        """Start listening now (mic button, hotkey or wake word). ``first`` = input already captured."""
        if not self.available or self.mode == "off":
            return
        self.muted = False
        self.tts.stop()
        self._ensure_conversation()
        if self.mode == "live":
            self.live.start(preroll)
        elif not self.busy:
            if first is None and self.vs.chime:
                self.speaker.play(chime("listen"))
            self._classic_task = asyncio.create_task(self._conversation(first), name="classic-turn")

    async def deactivate(self) -> None:
        await self.live.stop()
        if self._classic_task:
            self._classic_task.cancel()
        self.tts.stop()
        self.set_state("standby")

    async def toggle(self) -> None:
        if self.busy:
            await self.deactivate()
        else:
            await self.activate()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.set_state(self.state, self.detail)

    def interrupt(self) -> None:
        self.tts.stop()
        self.speaker.clear()

    # ── Wake word ──────────────────────────────────────────────────────────
    async def _wake_loop(self) -> None:
        q = self.mic.subscribe()
        ring: list[bytes] = []
        ep: Endpointer | None = None  # the offline wake engine's utterance finder
        try:
            while True:
                frame = await q.get()
                ring.append(frame)
                if len(ring) > RING_FRAMES:
                    del ring[:len(ring) - RING_FRAMES]
                if self.wake is not None:  # openWakeWord: feed every frame (~1 ms each)
                    if not self.muted and not self.busy and self.wake.feed(frame):
                        self.e.bus.emit("voice.wake", phrase=self.vs.wake_word_model)
                        await self.activate(preroll=ring[-10:])
                    continue
                if self._offline_wake_on:
                    if self.muted or self.busy or self.speaker.recently_played():
                        ep = None
                    else:
                        ep = ep or Endpointer(start_timeout=1e9, max_seconds=10)
                        if ep.feed(frame) in ("done", "timeout"):
                            pcm, ep = bytes(ep.pcm), None
                            if 0.4 <= len(pcm) / 2 / MIC_RATE and not self._offline_busy:
                                asyncio.create_task(self._offline_wake(pcm, list(ring)))
                    continue
                hit = None
                while not self._hits.empty():
                    hit = self._hits.get_nowait()
                if hit is not None and not self.muted:
                    self._recheck = False
                    await self._on_wake(hit, ring)
                elif self._recheck:
                    self._recheck = False
                    now = time.monotonic()
                    if not self.muted and not self.busy and now - self._recheck_at > RECHECK_GAP:
                        self._recheck_at = now
                        asyncio.create_task(self._offline_wake(last_utterance(ring), list(ring), recheck=True))
        finally:
            self.mic.unsubscribe(q)

    @property
    def _offline_wake_on(self) -> bool:
        return self.vs.wake_word and self.vs.wake_engine == "offline" and self._stt_ready

    async def _offline_wake(self, pcm: bytes, ring: list[bytes], recheck: bool = False) -> None:
        """Transcribe one short utterance on the PC and wake up if it starts with a wake phrase."""
        from .commands import lookup

        if len(pcm) / 2 / MIC_RATE < 0.4 or self._offline_busy:
            return
        self._offline_busy = True
        try:
            text = await self._transcribe_offline(pcm)
        finally:
            self._offline_busy = False
        phrases = list(self.vs.wake_phrases)
        hit = match_wake(text, phrases)
        if hit is None:
            near = match_wake(text, phrases, min_ratio=0.6)
            if near is not None and not recheck:
                self.e.bus.emit("voice.heard", phrase=near[0], confidence=0.6, accepted=False)
            return
        phrase, rest = hit
        log.info("Wake word (offline%s): %s", " second listen" if recheck else "", phrase)
        self.e.bus.emit("voice.heard", phrase=phrase, confidence=1.0, accepted=True)
        if self.muted or self.busy:
            return
        command = lookup(self.catalog, rest) if rest and self.catalog else None
        await self._on_wake((phrase, 1.0, command, rest), ring)

    async def _on_wake(self, hit: tuple, ring: list[bytes]) -> None:
        """``hit`` = (phrase, confidence, instant command or None[, offline transcript of what followed])."""
        phrase, conf, command = hit[:3]
        said: str | None = hit[3] if len(hit) > 3 else None
        if command and conf < COMMAND_CONFIDENCE:
            command = None  # not sure it was that command: let Gemini hear the whole thing
        log.info("Wake word: %s (%.2f)%s", phrase, conf, f" → {command}" if command else "")
        self.e.bus.emit("voice.wake", phrase=phrase, command=command)
        if self.speaker.recently_played():
            self.interrupt()  # the wake word always cuts ORION off
        if command == STOP:
            self.interrupt()
            if self.live.running:
                await self.live.stop()
            self.e.stop_everything()
            self.set_state("standby")
            return
        # The recogniser reports the wake phrase once the speaker pauses, so whatever followed it ("Hey ORION,
        # what's the weather?") is already in the ring buffer: use it instead of asking again.
        seg = last_utterance(ring)
        extra = len(seg) / 2 / MIC_RATE - (0.42 * len(phrase.split()) + 0.45)
        if command:
            first: tuple | None = ("command", command)
        elif said is not None and self.mode != "live":
            first = ("text", said) if said else None  # already transcribed on the PC: no Gemini call
            if said:
                self._ensure_conversation()
                self.e.bus.emit("voice.transcript", role="user", text=said, conv_id=self.conv_id)
        else:
            first = ("pcm", seg) if extra > 0.6 else None
        if self.mode == "live":
            if command:
                self._ensure_conversation()
                asyncio.create_task(self._instant(command))
            elif not self.live.running:
                frames = _frames(seg) if extra > 0.6 else ring[-10:]
                await self.activate(preroll=frames)
            return
        if self.busy:
            self._next = first or ("listen", None)  # picked up as soon as the current turn wraps up
            return
        await self.activate(first=first)

    async def _instant(self, command: str) -> None:
        self.e.bus.emit("voice.transcript", role="user", text=command, conv_id=self.conv_id)
        await self.e.agent.handle(command, self.conv_id, source="voice")

    # ── Classic mode ───────────────────────────────────────────────────────
    async def _record(self, start_timeout: float = 6, max_seconds: float = 15,
                      preroll: list[bytes] | None = None) -> bytes:
        """Record one utterance, ending it as soon as the speaker has finished (not after a fixed pause)."""
        ep = Endpointer(start_timeout=start_timeout, max_seconds=max_seconds)
        for f in preroll or []:
            if ep.feed(f) == "done":
                return bytes(ep.pcm)
        q = self.mic.subscribe()
        try:
            while True:
                frame = await asyncio.wait_for(q.get(), timeout=2)
                st = ep.feed(frame)
                if st == "speech" and self.state != "listening":
                    self.set_state("listening")
                if st == "timeout":
                    return b""
                if st == "done":
                    break
        finally:
            self.mic.unsubscribe(q)
        return bytes(ep.pcm) if ep.speech_seconds >= 0.2 else b""

    async def _conversation(self, first: tuple | None = None) -> None:
        """One voice exchange, then (optionally) keep listening for a follow-up without the wake word."""
        item, follow = first, False
        try:
            while True:
                if item is None or item[0] in ("preroll", "listen"):
                    await self.speaker.wait_done()
                    if follow:
                        await asyncio.sleep(0.25)  # let the room echo of the reply die down
                    self.set_state("listening", "Listening for a follow-up…" if follow else "")
                    pcm = await self._record(self.vs.follow_up_seconds if follow else 6,
                                             preroll=item[1] if item and item[0] == "preroll" else None)
                    if not pcm:
                        break
                    item = ("pcm", pcm)
                kind, val = item
                item = None
                if kind == "pcm":
                    self.set_state("thinking")
                    understood = await self._understand(val)
                    if understood is None:
                        break
                    kind, val = understood
                if val == STOP:
                    self.interrupt()
                    self.e.stop_everything()
                    break
                self.set_state("working")
                barged = await self._run_turn(val)
                if self._next is not None:
                    item, self._next, follow = self._next, None, False
                elif barged:
                    item, follow = ("preroll", barged), False
                elif self.vs.follow_up:
                    follow = True
                else:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Classic voice turn failed")
            self.set_state("error", str(exc))
            return
        if self.state != "error":
            self.set_state("standby")

    async def _understand(self, pcm: bytes) -> tuple[str, str] | None:
        """Utterance → ("command", canonical) for an instant command, else ("text", transcript)."""
        if len(pcm) < MIC_RATE * 2 * 0.3:
            return None
        if self._stt_ready:  # on the PC first: no quota, no upload
            understood = await self._understand_offline(pcm)
            if understood is not None:
                return understood
        wav = pcm_to_wav(pcm)
        sp = self.speech if self.speech is not None and self.speech.running else None
        dictation: asyncio.Task | None = None
        if sp is not None:
            fd, path = tempfile.mkstemp(prefix="orion-utt-", suffix=".wav")
            with os.fdopen(fd, "wb") as fh:
                fh.write(wav)
            if self.catalog:
                try:
                    command, conf, said = await asyncio.to_thread(sp.match_command, path)
                    if command and conf >= COMMAND_CONFIDENCE:
                        os.unlink(path)
                        self.e.bus.emit("voice.transcript", role="user", text=said, conv_id=self.conv_id)
                        return "command", command
                except Exception as exc:
                    log.debug("Instant command check failed: %s", exc)

            async def dictate() -> tuple[str, float]:
                try:
                    return await asyncio.to_thread(sp.dictate, path)
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

            dictation = asyncio.create_task(dictate())
        gemini = asyncio.create_task(self.e.brain.transcribe(wav, hints=self._hints()))
        text = ""
        if dictation is not None:
            await asyncio.wait({dictation, gemini}, return_when=asyncio.FIRST_COMPLETED)
            if dictation.done() and not gemini.done() and not dictation.exception():
                preview = dictation.result()[0]
                if preview:  # show something straight away; Gemini's version replaces it
                    self.e.bus.emit("voice.transcript", role="user", text=preview, conv_id=self.conv_id,
                                    tentative=True)
        try:
            text = await gemini
        except Exception as exc:
            log.warning("Transcription failed (%s)%s", exc, "; using the offline transcript" if dictation else "")
            if dictation is None:
                raise
        if not text and dictation is not None:
            try:
                offline, conf = await dictation
                text = offline if conf >= 0.4 else ""
            except Exception:
                text = ""
        elif dictation is not None and not dictation.done():
            dictation.add_done_callback(lambda t: t.exception())  # finishes on its own; deletes the temp file
        text = self._strip_wake(text)
        if not text:
            return None
        self.e.bus.emit("voice.transcript", role="user", text=text, conv_id=self.conv_id)
        return "text", text

    async def _understand_offline(self, pcm: bytes) -> tuple[str, str] | None:
        from .commands import lookup

        text = self._strip_wake(await self._transcribe_offline(pcm))
        if not text:
            return None  # nothing heard: Gemini gets a go
        command = lookup(self.catalog, text, list(self.vs.wake_phrases)) if self.catalog else None
        self.e.bus.emit("voice.transcript", role="user", text=text, conv_id=self.conv_id)
        return ("command", command) if command else ("text", text)

    def _strip_wake(self, text: str) -> str:
        t = text.strip()
        for p in sorted(self.vs.wake_phrases, key=len, reverse=True):
            words = r"\W+".join(re.escape(w) for w in p.split())
            m = re.match(rf"^\W*{words}\b[\s,.!?:;-]*", t, re.I)
            if m:
                return t[m.end():].strip()
        return t

    def _hints(self) -> list[str]:
        """Names worth spelling right: you, ORION, the people and projects in your vault."""
        at, cached = self._hint_cache
        if time.monotonic() - at < 120:
            return cached
        s = self.e.config.settings
        names = [n for n in (s.user_name, s.assistant_name) if n] + list(self.vs.wake_phrases)
        try:
            root = self.e.vault.root
            for folder in ("People", "Projects"):
                names += sorted(f.stem for f in (root / folder).glob("*.md"))[:30]
        except Exception:
            pass
        out = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))[:60]
        self._hint_cache = (time.monotonic(), out)
        return out

    async def _run_turn(self, text: str) -> list[bytes] | None:
        """Run the agent; if the user talks over the spoken reply, stop talking and return what they said."""
        captured: list[bytes] = []
        watcher = asyncio.create_task(self._barge_watch(captured)) if self.vs.barge_in else None
        try:
            await self.e.agent.handle(text, self.conv_id, source="voice")
        finally:
            if watcher is not None:
                watcher.cancel()
        return captured or None

    async def _barge_watch(self, captured: list[bytes]) -> None:
        q = self.mic.subscribe()
        guard = EchoGuard(self.barge_in_threshold)
        recent: list[bytes] = []
        triggered = False
        try:
            while True:
                frame = await q.get()
                if triggered:
                    if len(captured) < 750:
                        captured.append(frame)
                    continue
                recent = (recent + [frame])[-25:]
                playing = self.speaker.recently_played(0.3)
                if guard.feed(frame, playing) and playing:
                    triggered = True
                    captured.extend(recent)
                    log.info("Barge-in: the user is talking over the reply")
                    self.interrupt()
                    self.e.bus.emit("voice.barge_in")
                    self.set_state("listening")
        finally:
            self.mic.unsubscribe(q)

    def stream_speech(self):
        """A SpeechStream for a classic-mode voice turn, or None when replies aren't spoken."""
        if not self.available or self.mode != "classic" or not self.vs.speak_replies:
            return None
        from .tts import SpeechStream

        return SpeechStream(self.tts)

    async def finish_stream(self, stream, reply: str) -> None:
        await stream.wait()
        if not stream.spoke and reply.strip() and stream.gen == self.tts._gen:  # streaming failed: one-shot instead
            await self.tts.speak(reply)
        if self.state == "speaking":
            self.set_state("standby")

    async def speak(self, text: str) -> None:
        if not self.available or self.mode == "off":
            return
        prev = self.state
        self.set_state("speaking")
        try:
            await self.tts.speak(text)
        finally:
            if self.state == "speaking":
                self.set_state("working" if prev == "working" and self.busy else "standby")

    # ── Spoken approvals ───────────────────────────────────────────────────
    async def on_approval(self, info: dict) -> None:
        """A risky step needs an OK while you're talking to ORION: ask out loud and accept "yes"/"no"."""
        if not self.available or self.mode == "off" or not self.e.config.settings.voice_approvals:
            return
        self._approval_id = info["id"]
        self._approval_offset = len(self._last_user_text)
        label = info["label"].rstrip(".")
        prompt = f"Before I do that: {label}. Shall I go ahead?"
        if info["risk"] == "danger":
            prompt = f"This one can't easily be undone: {label}. Are you sure?"
        try:
            await asyncio.wait_for(self.speaker.wait_done(), timeout=8)
        except asyncio.TimeoutError:
            pass
        await self.tts.speak(prompt)
        if self.mode != "classic" or self._approval_id != info["id"]:
            return  # Live mode: the answer arrives through the session's transcription
        for attempt in range(2):
            self.set_state("listening", "Say yes or no")
            await asyncio.sleep(0.2)
            pcm = await self._record(start_timeout=6, max_seconds=6)
            if self._approval_id != info["id"]:
                return  # answered in the UI meanwhile
            if len(pcm) < MIC_RATE * 2 * 0.2:
                continue
            answer = parse_yes_no(await self._yes_no_text(pcm))
            if answer is not None:
                self._resolve_approval(answer)
                return
            if attempt == 0:
                await self.tts.speak("Sorry — yes or no?")
        self.set_state("working", "Waiting for approval on screen")

    async def _yes_no_text(self, pcm: bytes) -> str:
        if self._stt_ready:
            text = await self._transcribe_offline(pcm)
            if parse_yes_no(text) is not None:
                return text
        sp = self.speech if self.speech is not None and self.speech.running else None
        if sp is not None:  # "yes"/"no" is easy for the offline recogniser: no Gemini call needed
            fd, path = tempfile.mkstemp(prefix="orion-yn-", suffix=".wav")
            with os.fdopen(fd, "wb") as fh:
                fh.write(pcm_to_wav(pcm))
            try:
                text, conf = await asyncio.to_thread(sp.dictate, path, 10)
                if conf >= 0.5 and parse_yes_no(text) is not None:
                    return text
            except Exception:
                pass
            finally:
                os.unlink(path)
        return await self.e.brain.transcribe(pcm_to_wav(pcm))

    def check_spoken_approval(self, transcript: str) -> None:
        if len(transcript) < self._approval_offset:
            self._approval_offset = 0  # a new turn started
        self._last_user_text = transcript
        if not self._approval_id or self._approval_id not in self.e.approvals.pending:
            self._approval_id = None
            return
        answer = parse_yes_no(transcript[self._approval_offset:])
        if answer is not None:
            self._resolve_approval(answer)

    def _resolve_approval(self, approve: bool) -> None:
        aid, self._approval_id = self._approval_id, None
        if aid and self.e.approvals.respond(aid, "approve" if approve else "deny"):
            self.e.bus.emit("voice.approval", id=aid, approved=approve)
            if not approve:
                self.tts.stop()

    # ── Live transcript persistence ────────────────────────────────────────
    async def commit_turn(self, user_text: str, model_text: str, task) -> None:
        cid = self.conv_id
        if not cid:
            return
        if user_text.strip():
            m = self.e.store.add_message(cid, "user", user_text.strip(), {"source": "voice"})
            self.e.bus.emit("message", message=m)
        if model_text.strip():
            meta = {"model": self.e.brain.live_model, "source": "voice"}
            if task is not None:
                meta["task_id"] = task.id
            m = self.e.store.add_message(cid, "assistant", model_text.strip(), meta)
            self.e.bus.emit("message", message=m)
        if task is not None:
            self.e.tracker.finish(task)



def _frames(pcm: bytes) -> list[bytes]:
    n = FRAME * 2
    return [pcm[i:i + n] for i in range(0, len(pcm), n)]
