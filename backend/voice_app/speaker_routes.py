from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
import httpx
from pydantic import BaseModel, ConfigDict, Field

from voice_app.auth import PairingAuth
from voice_app.latency import log_latency
from voice_app.speaker import (
    SpeakerAuthorizations,
    SpeakerEnrollmentError,
    SpeakerGate,
    SpeakerProfileMissing,
)


SPEAKER_THRESHOLD_MINIMUM = 0.30
SPEAKER_THRESHOLD_MAXIMUM = 0.80


class SpeakerSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(
        ge=SPEAKER_THRESHOLD_MINIMUM,
        le=SPEAKER_THRESHOLD_MAXIMUM,
        allow_inf_nan=False,
    )


def _settings_payload(threshold: float) -> dict[str, float]:
    return {
        "threshold": round(float(threshold), 4),
        "minimum": SPEAKER_THRESHOLD_MINIMUM,
        "maximum": SPEAKER_THRESHOLD_MAXIMUM,
    }


async def _read_upload(upload: UploadFile, maximum: int) -> bytes:
    if upload.content_type != "application/octet-stream":
        raise HTTPException(status_code=415, detail="录音必须是 PCM 数据")
    content = await upload.read(maximum + 1)
    await upload.close()
    if len(content) > maximum:
        raise HTTPException(status_code=413, detail="录音数据过大")
    return content


async def _read_body(request: Request, maximum: int) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise HTTPException(status_code=415, detail="录音必须是 PCM 数据")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > maximum:
        raise HTTPException(status_code=413, detail="录音数据过大")
    content = await request.body()
    if len(content) > maximum:
        raise HTTPException(status_code=413, detail="录音数据过大")
    return content


def install_speaker_routes(
    app: FastAPI,
    auth: PairingAuth,
    speaker: SpeakerGate,
    authorizations: SpeakerAuthorizations,
    coordinator: Any,
    sessions: Any,
    *,
    max_audio_bytes: int,
    default_threshold: float,
) -> None:
    @app.get("/api/speaker/settings")
    async def settings(request: Request) -> dict[str, float]:
        client_id = auth.require_request(request)
        threshold = await sessions.get_speaker_threshold(client_id, default_threshold)
        return _settings_payload(threshold)

    @app.put("/api/speaker/settings")
    async def update_settings(
        request: Request, update: SpeakerSettingsUpdate
    ) -> dict[str, float]:
        client_id = auth.require_request(request)
        threshold = await sessions.set_speaker_threshold(client_id, update.threshold)
        return _settings_payload(threshold)

    @app.get("/api/speaker/profile")
    async def profile(request: Request) -> dict[str, object]:
        auth.require_request(request)
        current = speaker.profile()
        if current is None:
            return {"enrolled": False}
        return {
            "enrolled": True,
            "created_at": current.created_at,
            "model_id": current.model_id,
        }

    @app.post("/api/speaker/enroll")
    async def enroll(
        request: Request,
        samples: Annotated[list[UploadFile], File()],
    ) -> dict[str, object]:
        auth.require_request(request)
        if len(samples) != 3:
            raise HTTPException(status_code=422, detail="必须提交三段录音")
        try:
            pcm = [await _read_upload(sample, max_audio_bytes) for sample in samples]
            enrolled = await speaker.enroll(pcm)
        except SpeakerEnrollmentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail="声纹服务暂不可用") from exc
        return {
            "enrolled": True,
            "created_at": enrolled.created_at,
            "model_id": enrolled.model_id,
        }

    @app.post("/api/speaker/verify")
    async def verify(
        request: Request,
        generation_id: Annotated[int, Query(ge=1)],
    ) -> dict[str, object]:
        client_id = auth.require_request(request)
        pcm = await _read_body(request, max_audio_bytes)
        threshold = await sessions.get_speaker_threshold(client_id, default_threshold)
        started = time.perf_counter()
        try:
            decision = await speaker.verify(pcm, threshold=threshold)
        except SpeakerProfileMissing as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="声纹服务暂不可用") from exc
        speaker_ms = round((time.perf_counter() - started) * 1000, 1)
        log_latency(
            "speaker_verify",
            client_id,
            {
                "generation": generation_id,
                "accepted": decision.accepted,
                "score": round(decision.score, 4),
                "threshold": round(threshold, 4),
                "speaker_ms": speaker_ms,
            },
        )
        payload: dict[str, object] = {
            "accepted": decision.accepted,
            "score": round(decision.score, 4),
            "threshold": round(threshold, 4),
            "speaker_ms": speaker_ms,
        }
        if not decision.accepted:
            return payload
        token = authorizations.issue(client_id, generation_id)
        await coordinator.cancel(client_id, generation_id)
        payload["speaker_token"] = token
        return payload
