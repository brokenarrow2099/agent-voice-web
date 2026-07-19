from __future__ import annotations

from fastapi.testclient import TestClient

from speaker_service.server import create_app


class FakeEngine:
    model_id = "campplus-zh-16k-192"

    def __init__(self, embedding: list[float] | None = None) -> None:
        self.ready = False
        self.embedding = embedding or ([1.0] + [0.0] * 191)
        self.calls: list[bytes] = []

    async def load(self) -> None:
        self.ready = True

    async def embed(self, pcm: bytes) -> list[float]:
        self.calls.append(pcm)
        return self.embedding


def pcm(seconds: float = 1.0, sample: bytes = b"\x00\x20") -> bytes:
    return sample * round(16_000 * seconds)


def post_pcm(client: TestClient, audio: bytes):
    return client.post(
        "/embed",
        content=audio,
        headers={"content-type": "application/octet-stream"},
    )


def test_health_waits_for_model_load() -> None:
    engine = FakeEngine()
    assert engine.ready is False
    with TestClient(create_app(engine)) as client:
        assert client.get("/health").json() == {
            "ready": True,
            "model_id": "campplus-zh-16k-192",
        }


def test_embed_accepts_mono_16k_pcm16_and_returns_192_dimensions(caplog) -> None:
    engine = FakeEngine()
    audio = pcm()
    caplog.set_level("INFO", logger="voice_app.latency")
    with TestClient(create_app(engine)) as client:
        response = client.post(
            "/embed",
            content=audio,
            headers={"content-type": "application/octet-stream"},
        )
    assert response.status_code == 200
    assert response.json()["model_id"] == "campplus-zh-16k-192"
    assert len(response.json()["embedding"]) == 192
    assert engine.calls == [audio]
    assert "event=speaker_embed" in caplog.text
    assert "audio_ms=1000.0" in caplog.text
    assert "embed_ms=" in caplog.text


def test_embed_rejects_invalid_format_duration_size_and_silence() -> None:
    engine = FakeEngine()
    with TestClient(create_app(engine)) as client:
        assert post_pcm(client, b"").status_code == 422
        assert post_pcm(client, b"\x00").status_code == 422
        assert post_pcm(client, pcm(0.5)).status_code == 422
        assert post_pcm(client, pcm(16)).status_code == 413
        assert post_pcm(client, pcm(1, b"\x00\x00")).status_code == 422
        assert client.post(
            "/embed",
            content=pcm(),
            headers={"content-type": "audio/wav"},
        ).status_code == 415
    assert engine.calls == []


def test_embed_rejects_invalid_engine_output() -> None:
    with TestClient(create_app(FakeEngine([float("nan")] * 192))) as client:
        response = client.post(
            "/embed",
            content=pcm(),
            headers={"content-type": "application/octet-stream"},
        )
    assert response.status_code == 500
