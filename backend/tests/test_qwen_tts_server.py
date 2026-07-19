from __future__ import annotations

import base64

import numpy as np
from fastapi.testclient import TestClient

from tts_service.server import create_app


class FakeEngine:
    model_path = "/models/qwen3-tts"
    ready = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        return np.array([-1.0, -0.5, 0.0, 0.5, 1.0], dtype=np.float32), 24_000


def test_health_and_models_report_resident_engine():
    engine = FakeEngine()
    with TestClient(create_app(engine)) as client:
        assert client.get("/health").json() == {"status": "ok", "ready": True}
        assert client.get("/v1/models").json()["data"][0]["id"] == engine.model_path


def test_speech_endpoint_accepts_vllm_compatible_payload_and_returns_pcm():
    engine = FakeEngine()
    ref = base64.b64encode(b"RIFF-reference").decode()
    payload = {
        "model": engine.model_path,
        "input": "你好。",
        "task_type": "Base",
        "language": "Auto",
        "ref_audio": f"data:audio/wav;base64,{ref}",
        "x_vector_only_mode": True,
        "stream": True,
        "stream_format": "audio",
        "response_format": "pcm",
    }
    with TestClient(create_app(engine)) as client:
        response = client.post("/v1/audio/speech", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/pcm")
    assert len(response.content) == 10
    assert engine.calls == [
        {
            "text": "你好。",
            "language": "Auto",
            "ref_audio": payload["ref_audio"],
            "ref_text": None,
            "x_vector_only_mode": True,
        }
    ]


def test_speech_endpoint_rejects_non_pcm_and_oversized_text():
    engine = FakeEngine()
    with TestClient(create_app(engine)) as client:
        assert client.post(
            "/v1/audio/speech", json={"input": "hello", "response_format": "mp3"}
        ).status_code == 422
        assert client.post(
            "/v1/audio/speech", json={"input": "字" * 1001, "response_format": "pcm"}
        ).status_code == 422
