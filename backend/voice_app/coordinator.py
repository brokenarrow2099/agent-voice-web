from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from voice_app.asr import ASRService, AudioValidationError
from voice_app.claude import ClaudeProcessError, ClaudeRunner, ClaudeTimeoutError
from voice_app.latency import TurnMetrics, log_latency
from voice_app.protocol import (
    AudioCommit,
    AudioStart,
    ClientMetrics,
    Ping,
    ProtocolError,
    ResponseCancel,
    SessionConfigure,
    SessionEnd,
    SessionStart,
    event,
    parse_client_event,
)
from voice_app.session_store import SessionStore
from voice_app.speech_filter import SpeakableStream
from voice_app.tts import TTSClient, TTSStreamError
from voice_app.voices import DEFAULT_TTS_VOICE, TTSVoice


logger = logging.getLogger(__name__)


class TurnCoordinator:
    def __init__(
        self,
        *,
        asr: ASRService,
        claude: ClaudeRunner,
        tts: TTSClient,
        sessions: SessionStore,
        authorizations: Any,
        sentence_queue_size: int = 8,
        max_frame_bytes: int = 160_000,
    ) -> None:
        self.asr = asr
        self.claude = claude
        self.tts = tts
        self.sessions = sessions
        self.authorizations = authorizations
        self.sentence_queue_size = sentence_queue_size
        self.max_frame_bytes = max_frame_bytes
        self._generations: dict[str, int] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start_turn(
        self,
        websocket: Any,
        client_id: str,
        pcm: bytes,
        generation_id: int,
        *,
        turn_id: int | None = None,
        voice: TTSVoice | None = None,
    ) -> asyncio.Task[None]:
        await self.cancel(client_id, generation_id)
        metrics = TurnMetrics(
            turn_id=turn_id or generation_id,
            generation_id=generation_id,
            audio_ms=len(pcm) / 2 / 16_000 * 1000,
        )
        task = asyncio.create_task(
            self._process_turn(websocket, client_id, pcm, generation_id, voice, metrics),
            name=f"voice-turn-{client_id}-{generation_id}",
        )
        async with self._lock:
            self._tasks[client_id] = task

        def forget(done: asyncio.Task[None]) -> None:
            if self._tasks.get(client_id) is done:
                self._tasks.pop(client_id, None)

        task.add_done_callback(forget)
        return task

    async def cancel(self, client_id: str, generation_id: int) -> None:
        async with self._lock:
            old_generation = self._generations.get(client_id)
            old_task = self._tasks.pop(client_id, None)
            self._generations[client_id] = generation_id
        if old_generation is not None and old_generation < generation_id:
            await self.claude.cancel(old_generation)
        if old_task is not None and not old_task.done():
            old_task.cancel()
            await asyncio.gather(old_task, return_exceptions=True)

    async def end_session(self, client_id: str, generation_id: int) -> None:
        await self.cancel(client_id, generation_id)
        await self.sessions.clear_claude_session(client_id)

    async def _process_turn(
        self,
        websocket: Any,
        client_id: str,
        pcm: bytes,
        generation_id: int,
        voice: TTSVoice | None,
        metrics: TurnMetrics,
    ) -> None:
        if not await self._send_state(websocket, client_id, generation_id, "transcribing"):
            return
        try:
            metrics.start_asr()
            transcript = await self.asr.transcribe(pcm)
            metrics.finish_asr()
        except AudioValidationError as exc:
            metrics.finish_asr()
            await self._finish_metrics(websocket, client_id, metrics, "error")
            await self._send_error(
                websocket, client_id, generation_id, "invalid_audio", str(exc), retryable=True
            )
            await self._send_state(websocket, client_id, generation_id, "listening")
            return

        if transcript.is_empty:
            await self._finish_metrics(websocket, client_id, metrics, "empty")
            await self._send_state(websocket, client_id, generation_id, "listening")
            await self._send_json(
                websocket,
                client_id,
                event("turn.end", generation_id=generation_id, empty=True),
            )
            return

        await self._send_json(
            websocket,
            client_id,
            event(
                "transcript.final",
                generation_id=generation_id,
                text=transcript.text,
                language=transcript.language,
            ),
        )
        await self._send_state(websocket, client_id, generation_id, "thinking")

        session = await self.sessions.get_or_create(client_id)
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue(self.sentence_queue_size)
        speech_failed = asyncio.Event()
        speaker = asyncio.create_task(
            self._speak_sentences(
                websocket,
                client_id,
                generation_id,
                sentence_queue,
                speech_failed,
                voice,
                metrics,
            )
        )
        filter_stream = SpeakableStream()
        visible_parts: list[str] = []
        final_text = ""
        saw_delta = False

        try:
            metrics.start_model()
            async for item in self.claude.run(
                transcript.text, generation_id, session.claude_session_id
            ):
                if not self._is_current(client_id, generation_id):
                    return
                if item.session_id:
                    await self.sessions.set_claude_session(client_id, item.session_id)
                if item.kind == "text_delta":
                    metrics.mark_model_first_text()
                    saw_delta = True
                    visible_parts.append(item.text)
                    await self._send_json(
                        websocket,
                        client_id,
                        event("assistant.delta", generation_id=generation_id, text=item.text),
                    )
                    for sentence in filter_stream.feed(item.text):
                        metrics.mark_first_sentence()
                        await sentence_queue.put(sentence)
                elif item.kind == "tool_start":
                    await self._send_state(websocket, client_id, generation_id, "tool")
                    await self._send_json(
                        websocket,
                        client_id,
                        event(
                            "tool.start",
                            generation_id=generation_id,
                            name=item.tool_name or "工具",
                        ),
                    )
                elif item.kind == "tool_end":
                    await self._send_state(websocket, client_id, generation_id, "thinking")
                elif item.kind == "result":
                    final_text = item.text

            metrics.finish_model()
            if not saw_delta and final_text:
                metrics.mark_model_first_text()
                for sentence in filter_stream.feed(final_text):
                    metrics.mark_first_sentence()
                    await sentence_queue.put(sentence)
            for sentence in filter_stream.flush():
                metrics.mark_first_sentence()
                await sentence_queue.put(sentence)
            await sentence_queue.put(None)
            await speaker
            await self._finish_metrics(websocket, client_id, metrics, "complete")

            visible_final = final_text or "".join(visible_parts)
            await self._send_json(
                websocket,
                client_id,
                event("assistant.final", generation_id=generation_id, text=visible_final),
            )
            await self._send_state(websocket, client_id, generation_id, "idle")
            await self._send_json(
                websocket,
                client_id,
                event(
                    "turn.end",
                    generation_id=generation_id,
                    speech_available=not speech_failed.is_set(),
                ),
            )
        except asyncio.CancelledError:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            metrics.finish("cancelled")
            log_latency("turn_backend", client_id, metrics.public_snapshot(final=True))
            raise
        except ClaudeTimeoutError as exc:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            metrics.finish_model()
            await self._finish_metrics(websocket, client_id, metrics, "error")
            await self._send_error(
                websocket,
                client_id,
                generation_id,
                "claude_timeout_tool" if exc.active_tool else "claude_timeout",
                str(exc),
                retryable=True,
            )
        except ClaudeProcessError as exc:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            metrics.finish_model()
            await self._finish_metrics(websocket, client_id, metrics, "error")
            await self._send_error(
                websocket, client_id, generation_id, "claude_failed", str(exc), retryable=True
            )
        finally:
            if not speaker.done():
                speaker.cancel()
                await asyncio.gather(speaker, return_exceptions=True)

    async def _speak_sentences(
        self,
        websocket: Any,
        client_id: str,
        generation_id: int,
        queue: asyncio.Queue[str | None],
        failed: asyncio.Event,
        voice: TTSVoice | None,
        metrics: TurnMetrics,
    ) -> None:
        sentence_id = 0
        while True:
            sentence = await queue.get()
            if sentence is None:
                return
            if failed.is_set() or not self._is_current(client_id, generation_id):
                continue
            sentence_id += 1
            metrics.increment_sentence_count()
            await self._send_state(websocket, client_id, generation_id, "speaking")
            await self._send_json(
                websocket,
                client_id,
                event(
                    "audio.start",
                    generation_id=generation_id,
                    sentence_id=sentence_id,
                    sample_rate=24_000,
                    channels=1,
                ),
            )
            try:
                metrics.start_tts()
                async for audio in self.tts.stream(sentence, generation_id, voice=voice):
                    if not self._is_current(client_id, generation_id):
                        return
                    if metrics.response_first_audio_ms is None:
                        metrics.mark_tts_first_audio()
                        await self._send_json(
                            websocket,
                            client_id,
                            event(
                                "turn.metrics",
                                generation_id=generation_id,
                                **{
                                    key: value
                                    for key, value in metrics.public_snapshot(final=False).items()
                                    if key != "generation_id"
                                },
                            ),
                        )
                    await websocket.send_bytes(audio)
                await self._send_json(
                    websocket,
                    client_id,
                    event(
                        "audio.end",
                        generation_id=generation_id,
                        sentence_id=sentence_id,
                        cancelled=False,
                    ),
                )
            except TTSStreamError as exc:
                failed.set()
                await self._send_error(
                    websocket,
                    client_id,
                    generation_id,
                    "tts_failed",
                    str(exc),
                    retryable=True,
                )

    async def handle_socket(self, websocket: WebSocket, client_id: str) -> None:
        await websocket.accept()
        audio = bytearray()
        audio_turn: int | None = None
        active_generation = self._generations.get(client_id, 0)
        voice: TTSVoice = DEFAULT_TTS_VOICE
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                binary = message.get("bytes")
                if binary is not None:
                    if len(binary) > self.max_frame_bytes:
                        await websocket.close(code=4409, reason="audio frame too large")
                        break
                    if audio_turn is not None:
                        audio.extend(binary)
                    continue
                raw = message.get("text")
                if raw is None:
                    continue
                try:
                    control = parse_client_event(raw)
                except ProtocolError as exc:
                    await websocket.send_json(
                        event(
                            "error",
                            generation_id=active_generation,
                            code=exc.code,
                            message=exc.message,
                            retryable=False,
                        )
                    )
                    continue
                if isinstance(control, SessionStart):
                    voice = control.voice
                    active_generation = control.generation_id
                    await self.cancel(client_id, active_generation)
                    session = await self.sessions.get_or_create(client_id)
                    await websocket.send_json(
                        event(
                            "session.ready",
                            generation_id=active_generation,
                            client_id=client_id,
                            resumed=session.claude_session_id is not None,
                        )
                    )
                    await self._send_state(
                        websocket, client_id, active_generation, "listening"
                    )
                elif isinstance(control, SessionConfigure):
                    voice = control.voice
                    await websocket.send_json(
                        event(
                            "session.configured",
                            generation_id=control.generation_id,
                            voice=voice,
                        )
                    )
                elif isinstance(control, AudioStart):
                    if not self.authorizations.consume(
                        client_id,
                        control.generation_id,
                        control.speaker_token,
                    ):
                        audio.clear()
                        audio_turn = None
                        await websocket.send_json(
                            event(
                                "error",
                                generation_id=active_generation,
                                code="speaker_unauthorized",
                                message="这段语音没有有效的声纹授权",
                                retryable=False,
                            )
                        )
                        continue
                    active_generation = control.generation_id
                    await self.cancel(client_id, active_generation)
                    audio.clear()
                    audio_turn = control.turn_id
                    await self._send_state(
                        websocket, client_id, active_generation, "listening"
                    )
                elif isinstance(control, AudioCommit):
                    if (
                        audio_turn == control.turn_id
                        and control.generation_id == active_generation
                    ):
                        await self.start_turn(
                            websocket,
                            client_id,
                            bytes(audio),
                            control.generation_id,
                            turn_id=control.turn_id,
                            voice=voice,
                        )
                        audio.clear()
                        audio_turn = None
                elif isinstance(control, ResponseCancel):
                    active_generation = control.generation_id
                    await self.cancel(client_id, control.generation_id)
                    audio.clear()
                    audio_turn = None
                    await self._send_state(websocket, client_id, control.generation_id, "listening")
                elif isinstance(control, SessionEnd):
                    active_generation = control.generation_id
                    await self.end_session(client_id, control.generation_id)
                    await websocket.close(code=1000)
                    break
                elif isinstance(control, ClientMetrics):
                    values = control.model_dump(
                        exclude={"type"}, exclude_none=True
                    )
                    log_latency("turn_client", client_id, values)
                elif isinstance(control, Ping):
                    await websocket.send_json(
                        event("pong", generation_id=control.generation_id, nonce=control.nonce)
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await self.cancel(client_id, active_generation)

    def _is_current(self, client_id: str, generation_id: int) -> bool:
        return self._generations.get(client_id) == generation_id

    async def _send_state(
        self, websocket: Any, client_id: str, generation_id: int, state: str
    ) -> bool:
        return await self._send_json(
            websocket,
            client_id,
            event("state", generation_id=generation_id, state=state),
        )

    async def _send_error(
        self,
        websocket: Any,
        client_id: str,
        generation_id: int,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> bool:
        logger.warning("voice turn failed code=%s generation=%s", code, generation_id)
        return await self._send_json(
            websocket,
            client_id,
            event(
                "error",
                generation_id=generation_id,
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    async def _finish_metrics(
        self,
        websocket: Any,
        client_id: str,
        metrics: TurnMetrics,
        outcome: str,
    ) -> None:
        metrics.finish(outcome)
        snapshot = metrics.public_snapshot(final=True)
        log_latency("turn_backend", client_id, snapshot)
        await self._send_json(
            websocket,
            client_id,
            event(
                "turn.metrics",
                generation_id=metrics.generation_id,
                **{
                    key: value
                    for key, value in snapshot.items()
                    if key != "generation_id"
                },
            ),
        )

    async def _send_json(
        self, websocket: Any, client_id: str, payload: dict[str, object]
    ) -> bool:
        if not self._is_current(client_id, int(payload["generation_id"])):
            return False
        await websocket.send_json(payload)
        return True
