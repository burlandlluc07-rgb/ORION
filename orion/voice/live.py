"""Gemini Live session: real-time voice-to-voice with tool use.

Native-audio Live models hear and speak directly (no separate STT/TTS hop),
which is the lowest-latency path available — and it's on the free tier.
The session reuses ORION's tool registry and approval flow, so voice
commands get exactly the same capabilities and safety as typed ones.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from google.genai import errors, types

from ..brain.agent import BUILTIN_DECLS
from ..brain.gemini import friendly_error
from .audio import rms_level

if TYPE_CHECKING:
    from ..engine import Engine
    from .manager import VoiceManager

log = logging.getLogger("orion.live")

IDLE_DISCONNECT = 90  # seconds of silence before the Live session is closed to save quota


class LiveSession:
    def __init__(self, engine: "Engine", vm: "VoiceManager") -> None:
        self.e = engine
        self.vm = vm
        self._task: asyncio.Task | None = None
        self._handle: str | None = None
        self.last_activity = time.monotonic()
        self.active = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, preroll: list[bytes] | None = None) -> None:
        if self.running:
            self.last_activity = time.monotonic()
            return
        self.last_activity = time.monotonic()
        self._task = asyncio.create_task(self._run(preroll or []), name="live-session")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    def _config(self) -> types.LiveConnectConfig:
        s = self.e.config.settings
        decls = self.e.registry.declarations() + BUILTIN_DECLS
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=self.e.agent.system_prompt("", voice=True),
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in decls])],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=s.voice.live_voice))),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            session_resumption=types.SessionResumptionConfig(handle=self._handle),
        )

    async def _run(self, preroll: list[bytes]) -> None:
        e, vm = self.e, self.vm
        backoff = 1.0
        while True:
            model = e.brain.live_model
            if not model:
                try:
                    await e.brain.discover()
                    model = e.brain.live_model
                except Exception as exc:
                    vm.set_state("error", friendly_error(exc))
                    return
            if not model:
                vm.set_state("error", "No Live voice model is available for this API key. Switch voice mode to Classic.")
                return
            vm.set_state("connecting")
            try:
                async with e.brain.client.aio.live.connect(model=model, config=self._config()) as session:
                    log.info("Live session open on %s", model)
                    self.active = True
                    backoff = 1.0
                    vm.set_state("listening")
                    e.governor.record(model)
                    for frame in preroll:
                        await session.send_realtime_input(audio=types.Blob(data=frame, mime_type="audio/pcm;rate=16000"))
                    preroll = []
                    reconnect = await self._pump(session)
                    if not reconnect:
                        return
            except asyncio.CancelledError:
                raise
            except errors.APIError as exc:
                msg = friendly_error(exc)
                log.warning("Live error: %s", exc)
                if exc.code in (400, 401, 403, 404):
                    vm.set_state("error", msg)
                    return
                vm.set_state("connecting", msg)
            except Exception as exc:
                log.warning("Live session dropped: %s", exc)
                vm.set_state("connecting", "Reconnecting…")
            finally:
                self.active = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 20)

    async def _pump(self, session) -> bool:
        """Run send/receive until idle-timeout (False) or the server asks us to reconnect (True)."""
        vm = self.vm
        q = vm.mic.subscribe()
        result = {"reconnect": False}

        async def send() -> None:
            while True:
                frame = await q.get()
                if vm.muted:
                    continue
                if vm.speaker.recently_played():
                    # Half-duplex echo guard: while ORION talks, only forward loud speech (barge-in).
                    if rms_level(frame) < vm.barge_in_threshold:
                        frame = b"\x00" * len(frame)
                await session.send_realtime_input(audio=types.Blob(data=frame, mime_type="audio/pcm;rate=16000"))

        async def idle_watch() -> None:
            while True:
                await asyncio.sleep(2)
                if vm.speaker.playing or self.e.approvals.pending:
                    self.last_activity = time.monotonic()
                if time.monotonic() - self.last_activity > IDLE_DISCONNECT:
                    return

        async def receive() -> None:
            user_buf, model_buf = [], []
            task = None
            while True:
                async for msg in session.receive():
                    if msg.session_resumption_update and msg.session_resumption_update.new_handle:
                        self._handle = msg.session_resumption_update.new_handle
                    if msg.go_away is not None:
                        result["reconnect"] = True
                        return
                    sc = msg.server_content
                    if sc:
                        if sc.input_transcription and sc.input_transcription.text:
                            self.last_activity = time.monotonic()
                            user_buf.append(sc.input_transcription.text)
                            vm.set_state("thinking")
                            self.e.bus.emit("voice.transcript", role="user", text="".join(user_buf), conv_id=vm.conv_id)
                        if sc.model_turn and sc.model_turn.parts:
                            for part in sc.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    vm.speaker.play(part.inline_data.data)
                                    vm.set_state("speaking")
                        if sc.output_transcription and sc.output_transcription.text:
                            model_buf.append(sc.output_transcription.text)
                            self.e.bus.emit("voice.transcript", role="assistant", text="".join(model_buf), conv_id=vm.conv_id)
                        if sc.interrupted:
                            vm.speaker.clear()
                            vm.set_state("listening")
                        if sc.turn_complete:
                            self.last_activity = time.monotonic()
                            await self.vm.commit_turn("".join(user_buf), "".join(model_buf), task)
                            user_buf, model_buf, task = [], [], None
                            asyncio.create_task(self._back_to_listening())
                    if msg.tool_call and msg.tool_call.function_calls:
                        self.last_activity = time.monotonic()
                        if task is None:
                            task = self.e.tracker.start("".join(user_buf)[:80] or "Voice command", vm.conv_id)
                        vm.set_state("working")
                        responses = []
                        for fc in msg.tool_call.function_calls:
                            ok, res = await self.e.agent._dispatch(fc.name, dict(fc.args or {}), task, vm.conv_id)
                            responses.append(types.FunctionResponse(id=fc.id, name=fc.name,
                                                                    response={"result": res} if ok else {"error": res}))
                        await session.send_tool_response(function_responses=responses)

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive()), asyncio.create_task(idle_watch())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception():
                    raise t.exception()  # type: ignore[misc]
        finally:
            for t in tasks:
                t.cancel()
            vm.mic.unsubscribe(q)
        if not result["reconnect"]:
            vm.set_state("standby")
        return result["reconnect"]

    async def _back_to_listening(self) -> None:
        await self.vm.speaker.wait_done()
        if self.vm.state in ("speaking", "thinking", "working"):
            self.vm.set_state("listening")
