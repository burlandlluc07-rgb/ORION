"""Voice manager: owns the mic/speaker, wake word, and the active voice mode.

States (shown by the UI's reactor orb):
    off · standby · listening · thinking · working · speaking · connecting · error

Modes:
    live     Gemini Live native audio (fastest, most natural).
    classic  Record → Gemini transcription → ORION agent → edge-tts speech.
             Uses the normal text pipeline, so the local fast path applies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import numpy as np

from ..tasks.tracker import parse_yes_no
from .audio import MIC_RATE, Microphone, Speaker, pcm_to_wav, rms_level
from .live import LiveSession
from .tts import TTS

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.voice")


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
        self.wake: WakeWord | None = None
        self._wake_task: asyncio.Task | None = None
        self._classic_task: asyncio.Task | None = None
        self.available = True
        self.error: str | None = None
        self._approval_id: str | None = None
        self._approval_offset = 0
        self._last_user_text = ""
        engine.approvals.on_voice_request = self.on_approval

    # ── State ──────────────────────────────────────────────────────────────
    def set_state(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.e.bus.emit("voice.state", state=state, detail=detail, muted=self.muted, mode=self.mode, conv_id=self.conv_id)

    @property
    def mode(self) -> str:
        return self.e.config.settings.voice.mode

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "muted": self.muted, "mode": self.mode,
                "wake_word": self.wake is not None,
                "wake_phrases": getattr(self.wake, "phrases", None), "wake_language": getattr(self.wake, "language", ""), "live_model": self.e.brain.live_model, "error": self.error}

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
        vs = self.e.config.settings.voice
        if vs.wake_word:
            try:
                if vs.wake_engine == "openwakeword":
                    self.wake = await asyncio.to_thread(WakeWord, vs.wake_word_model)
                else:
                    await self._start_windows_wake()
                self._wake_task = asyncio.create_task(self._wake_loop(), name="wake-word")
            except ImportError:
                self.error = "The openWakeWord engine isn't installed; use the built-in Windows engine instead."
                log.info(self.error)
            except Exception as exc:
                self.error = f"Wake word failed: {exc}"
                log.warning(self.error)
        self.set_state("standby")

    async def _start_windows_wake(self) -> None:
        import sys

        from .wake import WindowsWake

        if sys.platform != "win32":
            raise RuntimeError("the built-in wake word engine needs Windows")
        vs = self.e.config.settings.voice
        loop = self.loop

        def heard(text: str, conf: float, accepted: bool) -> None:
            loop.call_soon_threadsafe(lambda: self.e.bus.emit("voice.heard", phrase=text, confidence=round(conf, 2),
                                                              accepted=accepted))

        def hit(text: str, conf: float) -> None:
            loop.call_soon_threadsafe(self._wake_hits.put_nowait, (text, conf))

        def error(msg: str) -> None:
            self.error = msg
            loop.call_soon_threadsafe(lambda: self.set_state(self.state, self.detail))

        self._wake_hits: asyncio.Queue = asyncio.Queue()
        engine = WindowsWake(vs.wake_phrases, vs.wake_sensitivity, vs.wake_language, hit, heard, error)
        await asyncio.to_thread(engine.start)
        if self.error and not engine.language:
            engine.stop()
            raise RuntimeError(self.error)
        self.wake = engine  # type: ignore[assignment]
        log.info("Wake word listening for %s (%s)", engine.phrases, engine.language)

    async def stop(self) -> None:
        await self.live.stop()
        for t in (self._wake_task, self._classic_task):
            if t:
                t.cancel()
        self.tts.stop()
        self.mic.stop()
        self.speaker.close()
        if self.wake is not None and hasattr(self.wake, "stop"):
            self.wake.stop()
        self.wake = None
        self.set_state("off")

    async def restart(self) -> None:
        await self.stop()
        vs = self.e.config.settings.voice
        self.mic.device, self.speaker.device = vs.input_device, vs.output_device
        self.tts.voice, self.tts.rate = vs.tts_voice, vs.tts_rate
        await self.start()

    # ── Activation ─────────────────────────────────────────────────────────
    async def activate(self, preroll: list[bytes] | None = None) -> None:
        """Start listening now (mic button, hotkey or wake word)."""
        if not self.available or self.mode == "off":
            return
        self.muted = False
        self.tts.stop()
        if self.conv_id is None or self.e.store.get_conversation(self.conv_id) is None:
            conv = self.e.store.create_conversation(time.strftime("Voice · %d %b %H:%M"))
            self.conv_id = conv["id"]
            self.e.bus.emit("conversation.created", conversation=conv)
        if self.mode == "live":
            self.live.start(preroll)
        elif self._classic_task is None or self._classic_task.done():
            self._classic_task = asyncio.create_task(self._classic_turn(), name="classic-turn")

    async def deactivate(self) -> None:
        await self.live.stop()
        if self._classic_task:
            self._classic_task.cancel()
        self.tts.stop()
        self.set_state("standby")

    async def toggle(self) -> None:
        if self.live.running or (self._classic_task and not self._classic_task.done()):
            await self.deactivate()
        else:
            await self.activate()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.set_state(self.state, self.detail)

    def interrupt(self) -> None:
        self.tts.stop()
        self.speaker.clear()

    async def _wake_loop(self) -> None:
        q = self.mic.subscribe()
        ring: list[bytes] = []
        windows = not isinstance(self.wake, WakeWord)
        try:
            while True:
                frame = await q.get()
                ring.append(frame)
                ring = ring[-40:]  # ~0.8 s pre-roll so the command right after the wake phrase isn't lost
                busy = self.muted or self.live.running or (self._classic_task and not self._classic_task.done())
                if windows:
                    hit = None
                    while not self._wake_hits.empty():
                        hit = self._wake_hits.get_nowait()
                    if hit is None or busy:
                        continue
                    phrase = hit[0]
                elif busy or not self.wake.feed(frame):  # type: ignore[union-attr]  (~1 ms per frame)
                    continue
                else:
                    phrase = self.e.config.settings.voice.wake_word_model
                log.info("Wake word detected: %s", phrase)
                self.e.bus.emit("voice.wake", phrase=phrase)
                await self.activate(preroll=ring[-10:])
        finally:
            self.mic.unsubscribe(q)

    # ── Classic mode ───────────────────────────────────────────────────────
    async def _record_utterance(self, max_seconds: float = 15, silence: float = 0.9) -> bytes:
        q = self.mic.subscribe()
        pcm = bytearray()
        started = False
        quiet_since = None
        noise = 0.02
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < max_seconds:
                frame = await asyncio.wait_for(q.get(), timeout=2)
                lv = rms_level(frame)
                if not started:
                    noise = 0.95 * noise + 0.05 * lv
                    if lv > max(0.08, noise * 3):
                        started = True
                        pcm.extend(frame)
                    elif time.monotonic() - t0 > 6:
                        return b""  # nobody spoke
                    continue
                pcm.extend(frame)
                if lv < max(0.05, noise * 2):
                    quiet_since = quiet_since or time.monotonic()
                    if time.monotonic() - quiet_since > silence:
                        break
                else:
                    quiet_since = None
        finally:
            self.mic.unsubscribe(q)
        return bytes(pcm)

    async def _classic_turn(self) -> None:
        self.set_state("listening")
        try:
            pcm = await self._record_utterance()
            if len(pcm) < MIC_RATE * 2 * 0.3:
                self.set_state("standby")
                return
            self.set_state("thinking")
            text = await self.e.brain.transcribe(pcm_to_wav(pcm))
            if not text:
                self.set_state("standby")
                return
            self.e.bus.emit("voice.transcript", role="user", text=text, conv_id=self.conv_id)
            self.set_state("working")
            await self.e.agent.handle(text, self.conv_id, source="voice")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Classic voice turn failed")
            self.set_state("error", str(exc))
            return
        if self.state != "error":
            self.set_state("standby")

    def stream_speech(self):
        """A SpeechStream for a classic-mode voice turn, or None when replies aren't spoken."""
        if not self.available or self.mode != "classic" or not self.e.config.settings.voice.speak_replies:
            return None
        from .tts import SpeechStream

        return SpeechStream(self.tts)

    async def finish_stream(self, stream, reply: str) -> None:
        await stream.wait()
        if not stream.spoke and reply.strip():  # e.g. streaming TTS failed: fall back to one-shot
            await self.tts.speak(reply)
        if self.state == "speaking":
            self.set_state("standby")

    async def speak(self, text: str) -> None:
        if not self.available or self.mode == "off":
            return
        self.set_state("speaking")
        try:
            await self.tts.speak(text)
        finally:
            if self.state == "speaking":
                self.set_state("standby")

    # ── Spoken approvals ───────────────────────────────────────────────────
    async def on_approval(self, info: dict) -> None:
        """A voice-initiated task hit a risky step: ask out loud and accept "yes"/"no"."""
        if not self.available or self.mode == "off":
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
            pcm = await self._record_utterance(max_seconds=6, silence=0.7)
            if self._approval_id != info["id"]:
                return  # answered in the UI meanwhile
            if len(pcm) < MIC_RATE * 2 * 0.2:
                continue
            answer = parse_yes_no(await self.e.brain.transcribe(pcm_to_wav(pcm)))
            if answer is not None:
                self._resolve_approval(answer)
                return
            if attempt == 0:
                await self.tts.speak("Sorry — yes or no?")
        self.set_state("working", "Waiting for approval on screen")

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

