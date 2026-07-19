from __future__ import annotations

import asyncio
import json

from voice_app.asr import Transcript
from voice_app.claude import ClaudeEvent
from voice_app.coordinator import TurnCoordinator
from voice_app.tts import TTSStreamError


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(("json", payload))

    async def send_bytes(self, payload):
        self.messages.append(("bytes", payload))


class ASR:
    def __init__(self, text="用户问题"):
        self.text = text
        self.calls = []

    async def transcribe(self, pcm):
        self.calls.append(pcm)
        return Transcript(text=self.text, language="zh", language_probability=0.99)


class Claude:
    def __init__(self, events=None, gate=None):
        self.events = events or [
            ClaudeEvent("session", session_id="claude-session"),
            ClaudeEvent("text_delta", text="第一句。"),
            ClaudeEvent("tool_start", tool_name="Bash"),
            ClaudeEvent("tool_end"),
            ClaudeEvent("text_delta", text="第二句。"),
            ClaudeEvent("result", text="第一句。第二句。", session_id="claude-session"),
        ]
        self.cancelled = []
        self.gate = gate

    async def run(self, prompt, generation_id, session_id=None):
        for event in self.events:
            if self.gate is not None:
                await self.gate.wait()
            yield event

    async def cancel(self, generation_id):
        self.cancelled.append(generation_id)


class TTS:
    def __init__(self, fail=False, gate=None):
        self.texts = []
        self.fail = fail
        self.gate = gate

    async def stream(self, text, generation_id, *, voice=None):
        self.texts.append((text, generation_id, voice))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise TTSStreamError("no voice")
        yield text.encode() + b"\x00"


class Store:
    def __init__(self):
        self.session_id = None

    async def get_or_create(self, client_id):
        return type("Session", (), {"claude_session_id": self.session_id})()

    async def set_claude_session(self, client_id, session_id):
        self.session_id = session_id

    async def clear_claude_session(self, client_id):
        self.session_id = None


class Authorizations:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.consumed = []

    def consume(self, client_id, generation_id, token):
        self.consumed.append((client_id, generation_id, token))
        return self.accepted


class ScriptedSocket(Socket):
    def __init__(self, messages):
        super().__init__()
        self.incoming = list(messages)
        self.closed = None

    async def accept(self):
        pass

    async def receive(self):
        if len(self.incoming) == 1:
            await asyncio.sleep(0.05)
        return self.incoming.pop(0)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


def text(payload):
    return {"type": "websocket.receive", "text": json.dumps(payload)}


def binary(payload):
    return {"type": "websocket.receive", "bytes": payload}


def disconnect():
    return {"type": "websocket.disconnect"}


def coordinator(asr=None, claude=None, tts=None, store=None, authorizations=None):
    return TurnCoordinator(
        asr=asr or ASR(),
        claude=claude or Claude(),
        tts=tts or TTS(),
        sessions=store or Store(),
        sentence_queue_size=2,
        authorizations=authorizations or Authorizations(),
    )


async def test_unauthorized_socket_audio_does_not_cancel_or_reach_asr():
    asr = ASR()
    claude = Claude()
    authorizations = Authorizations(accepted=False)
    service = coordinator(asr=asr, claude=claude, authorizations=authorizations)
    socket = ScriptedSocket(
        [
            text({"type": "session.start", "client_id": "phone", "generation_id": 0, "voice": "serena"}),
            text({"type": "audio.start", "turn_id": 1, "generation_id": 1, "speaker_token": "invalid-token-with-thirty-two-characters"}),
            binary(b"\x00\x20" * 16_000),
            text({"type": "audio.commit", "turn_id": 1, "generation_id": 1}),
            disconnect(),
        ]
    )

    await service.handle_socket(socket, "phone")

    assert asr.calls == []
    assert claude.cancelled == []
    errors = [payload for kind, payload in socket.messages if kind == "json" and payload["type"] == "error"]
    assert errors[-1]["code"] == "speaker_unauthorized"


async def test_authorized_socket_audio_is_consumed_once_and_reaches_asr():
    asr = ASR("")
    authorizations = Authorizations(accepted=True)
    service = coordinator(asr=asr, authorizations=authorizations)
    socket = ScriptedSocket(
        [
            text({"type": "session.start", "client_id": "phone", "generation_id": 0, "voice": "serena"}),
            text({"type": "audio.start", "turn_id": 1, "generation_id": 1, "speaker_token": "valid-token-with-at-least-thirty-two-characters"}),
            binary(b"\x00\x20" * 16_000),
            text({"type": "audio.commit", "turn_id": 1, "generation_id": 1}),
            disconnect(),
        ]
    )

    await service.handle_socket(socket, "phone")

    assert authorizations.consumed == [
        ("phone", 1, "valid-token-with-at-least-thirty-two-characters")
    ]
    assert asr.calls == [b"\x00\x20" * 16_000]


async def test_complete_turn_state_sequence_and_ordered_audio():
    socket = Socket()
    tts = TTS()
    service = coordinator(tts=tts)
    task = await service.start_turn(socket, "phone", b"\x01\x00" * 100, 1)
    await task

    json_messages = [payload for kind, payload in socket.messages if kind == "json"]
    states = [payload["state"] for payload in json_messages if payload["type"] == "state"]
    assert states[0:2] == ["transcribing", "thinking"]
    assert "tool" in states and "speaking" in states
    assert states[-1] == "idle"
    assert tts.texts == [("第一句。", 1, None), ("第二句。", 1, None)]
    assert [payload["type"] for payload in json_messages].count("assistant.final") == 1
    metrics = [payload for payload in json_messages if payload["type"] == "turn.metrics"]
    assert len(metrics) == 2
    assert metrics[0]["final"] is False
    assert metrics[0]["response_first_audio_ms"] >= 0
    assert metrics[1]["final"] is True and metrics[1]["outcome"] == "complete"
    assert metrics[1]["turn_id"] == 1
    assert json_messages[-1]["type"] == "turn.end"


async def test_empty_transcript_returns_to_listening_without_claude():
    socket = Socket()
    claude = Claude([])
    service = coordinator(asr=ASR(""), claude=claude)
    task = await service.start_turn(socket, "phone", b"\x00\x00", 1)
    await task
    messages = [payload for kind, payload in socket.messages if kind == "json"]
    assert [item["state"] for item in messages if item["type"] == "state"][-1] == "listening"
    assert not any(item["type"].startswith("assistant") for item in messages)


async def test_tool_payload_is_never_exposed_or_spoken():
    socket = Socket()
    tts = TTS()
    events = [
        ClaudeEvent("tool_start", tool_name="Bash"),
        ClaudeEvent("tool_end"),
        ClaudeEvent("result", text="操作完成。", session_id="s"),
    ]
    task = await coordinator(claude=Claude(events), tts=tts).start_turn(
        socket, "phone", b"\x01\x00", 1
    )
    await task
    tool = [p for k, p in socket.messages if k == "json" and p["type"] == "tool.start"]
    assert tool == [{"type": "tool.start", "generation_id": 1, "name": "Bash"}]
    assert tts.texts == [("操作完成。", 1, None)]


async def test_visible_agent_text_keeps_symbols_while_tts_receives_normalized_text():
    socket = Socket()
    tts = TTS()
    original = "✅ **部署完成**。"
    events = [
        ClaudeEvent("text_delta", text=original),
        ClaudeEvent("result", text=original, session_id="s"),
    ]
    task = await coordinator(claude=Claude(events), tts=tts).start_turn(
        socket, "phone", b"\x01\x00", 1
    )
    await task
    payloads = [payload for kind, payload in socket.messages if kind == "json"]
    assert any(p["type"] == "assistant.delta" and p["text"] == original for p in payloads)
    assert any(p["type"] == "assistant.final" and p["text"] == original for p in payloads)
    assert tts.texts == [("已完成 部署完成。", 1, None)]


async def test_tts_failure_keeps_visible_final_text_and_ends_turn():
    socket = Socket()
    task = await coordinator(tts=TTS(fail=True)).start_turn(socket, "phone", b"\x01\x00", 1)
    await task
    messages = [p for k, p in socket.messages if k == "json"]
    assert any(p["type"] == "assistant.final" and p["text"] == "第一句。第二句。" for p in messages)
    assert any(p["type"] == "error" and p["code"] == "tts_failed" for p in messages)
    assert messages[-1]["type"] == "turn.end"


async def test_barge_in_cancels_old_generation_and_discards_stale_audio():
    socket = Socket()
    gate = asyncio.Event()
    claude = Claude(gate=gate)
    tts = TTS(gate=gate)
    service = coordinator(claude=claude, tts=tts)
    old = await service.start_turn(socket, "phone", b"\x01\x00", 1)
    await asyncio.sleep(0)
    new = await service.start_turn(socket, "phone", b"\x01\x00", 2)
    gate.set()
    await asyncio.gather(old, new, return_exceptions=True)

    assert 1 in claude.cancelled
    first_new = next(
        i for i, item in enumerate(socket.messages) if item[0] == "json" and item[1]["generation_id"] == 2
    )
    assert not any(
        payload.get("generation_id") == 1
        for kind, payload in socket.messages[first_new:]
        if isinstance(payload, dict)
    )
    assert not any(generation == 1 for _text, generation, _voice in tts.texts)


async def test_selected_voice_is_fixed_for_every_sentence_in_turn():
    socket = Socket()
    tts = TTS()
    service = coordinator(tts=tts)
    task = await service.start_turn(
        socket,
        "phone",
        b"\x01\x00" * 100,
        1,
        voice="uncle_fu",
    )
    await task

    assert tts.texts == [
        ("第一句。", 1, "uncle_fu"),
        ("第二句。", 1, "uncle_fu"),
    ]


async def test_end_session_cancels_and_clears_persisted_conversation():
    socket = Socket()
    gate = asyncio.Event()
    claude = Claude(gate=gate)
    store = Store()
    store.session_id = "old"
    service = coordinator(claude=claude, store=store)
    task = await service.start_turn(socket, "phone", b"\x01\x00", 4)
    await asyncio.sleep(0)
    await service.end_session("phone", 5)
    await asyncio.gather(task, return_exceptions=True)
    assert claude.cancelled == [4]
    assert store.session_id is None
