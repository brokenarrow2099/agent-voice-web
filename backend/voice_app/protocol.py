from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from voice_app.voices import DEFAULT_TTS_VOICE, TTSVoice


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ClientEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_id: int = Field(ge=0, strict=True)


class SessionStart(ClientEventBase):
    type: Literal["session.start"]
    client_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    voice: TTSVoice = DEFAULT_TTS_VOICE


class SessionConfigure(ClientEventBase):
    type: Literal["session.configure"]
    voice: TTSVoice


class AudioStart(ClientEventBase):
    type: Literal["audio.start"]
    turn_id: int = Field(gt=0, strict=True)
    speaker_token: str = Field(min_length=32, max_length=256)


class AudioCommit(ClientEventBase):
    type: Literal["audio.commit"]
    turn_id: int = Field(gt=0, strict=True)


class ResponseCancel(ClientEventBase):
    type: Literal["response.cancel"]


class SessionEnd(ClientEventBase):
    type: Literal["session.end"]


class Ping(ClientEventBase):
    type: Literal["ping"]
    nonce: str = Field(default="", max_length=128)


class ClientMetrics(ClientEventBase):
    type: Literal["client.metrics"]
    turn_id: int = Field(gt=0, strict=True)
    stage: Literal["first_audio", "complete"]
    speaker_roundtrip_ms: float | None = Field(
        default=None, ge=0, le=3_600_000, allow_inf_nan=False
    )
    commit_to_transcript_ms: float | None = Field(
        default=None, ge=0, le=3_600_000, allow_inf_nan=False
    )
    commit_to_first_text_ms: float | None = Field(
        default=None, ge=0, le=3_600_000, allow_inf_nan=False
    )
    commit_to_first_audio_ms: float | None = Field(
        default=None, ge=0, le=3_600_000, allow_inf_nan=False
    )
    first_audio_to_enqueue_ms: float | None = Field(
        default=None, ge=0, le=3_600_000, allow_inf_nan=False
    )


ClientEvent = Annotated[
    Union[
        SessionStart,
        SessionConfigure,
        AudioStart,
        AudioCommit,
        ResponseCancel,
        SessionEnd,
        Ping,
        ClientMetrics,
    ],
    Field(discriminator="type"),
]
_CLIENT_EVENT_ADAPTER = TypeAdapter(ClientEvent)
_KNOWN_CLIENT_TYPES = {
    "session.start",
    "session.configure",
    "audio.start",
    "audio.commit",
    "response.cancel",
    "session.end",
    "ping",
    "client.metrics",
}


def parse_client_event(raw: str) -> ClientEvent:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProtocolError("invalid_event", "控制消息不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_event", "控制消息必须是 JSON 对象")
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type not in _KNOWN_CLIENT_TYPES:
        raise ProtocolError("unknown_event", f"不支持的消息类型: {event_type}")
    try:
        return _CLIENT_EVENT_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ProtocolError("invalid_event", "控制消息字段无效") from exc


def event(type: str, *, generation_id: int, **payload: object) -> dict[str, object]:
    if not type:
        raise ValueError("event type cannot be empty")
    if isinstance(generation_id, bool) or not isinstance(generation_id, int) or generation_id < 0:
        raise ValueError("generation_id must be a non-negative integer")
    return {"type": type, "generation_id": generation_id, **payload}
