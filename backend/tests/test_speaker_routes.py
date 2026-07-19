from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from voice_app.app import AppServices, create_app
from voice_app.config import Settings
from voice_app.speaker import SpeakerDecision, SpeakerEnrollmentError, SpeakerProfileMissing


class ASR:
    is_loaded = True

    async def load(self) -> None:
        pass


class TTS:
    async def close(self) -> None:
        pass


class Sessions:
    def __init__(self) -> None:
        self.thresholds: dict[str, float] = {}

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_speaker_threshold(self, client_id: str, default: float) -> float:
        return self.thresholds.get(client_id, default)

    async def set_speaker_threshold(self, client_id: str, threshold: float) -> float:
        self.thresholds[client_id] = threshold
        return threshold


class Health:
    async def readiness(self):
        return {"ready": True, "dependencies": {"speaker": True}}

    async def close(self) -> None:
        pass


class Coordinator:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, int]] = []

    async def cancel(self, client_id: str, generation_id: int) -> None:
        self.cancelled.append((client_id, generation_id))

    async def handle_socket(self, websocket, _client_id: str) -> None:
        await websocket.close()


class Authorizations:
    def __init__(self) -> None:
        self.issued: list[tuple[str, int]] = []

    def issue(self, client_id: str, generation_id: int) -> str:
        self.issued.append((client_id, generation_id))
        return "speaker-token-with-at-least-thirty-two-characters"


@dataclass
class Profile:
    model_id: str = "campplus-zh-16k-192"
    created_at: str = "2026-07-19T03:00:00+00:00"


class Speaker:
    def __init__(self) -> None:
        self.current = None
        self.decision = SpeakerDecision(False, 0.2)
        self.enrolled_samples: list[bytes] = []
        self.failure: Exception | None = None
        self.thresholds: list[float | None] = []

    def profile(self):
        return self.current

    async def enroll(self, samples: list[bytes]):
        if self.failure:
            raise self.failure
        self.enrolled_samples = samples
        self.current = Profile()
        return self.current

    async def verify(self, _pcm: bytes, *, threshold: float | None = None):
        if self.failure:
            raise self.failure
        self.thresholds.append(threshold)
        return self.decision


class Verifier:
    async def close(self) -> None:
        pass


def make_client(tmp_path: Path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("voice")
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOICE_FRONTEND_DIST", str(frontend))
    monkeypatch.setenv("VOICE_PAIRING_TOKEN", "pair-token")
    monkeypatch.setenv("VOICE_COOKIE_SECRET", "cookie-signing-secret-at-least-32-bytes")
    config = Settings(_env_file=None)
    speaker = Speaker()
    coordinator = Coordinator()
    authorizations = Authorizations()
    services = AppServices(
        asr=ASR(),
        tts=TTS(),
        sessions=Sessions(),
        health=Health(),
        coordinator=coordinator,
        speaker=speaker,
        speaker_verifier=Verifier(),
        authorizations=authorizations,
    )
    return (
        TestClient(create_app(config, services=services), base_url="https://voice.local"),
        speaker,
        coordinator,
        authorizations,
    )


def pair(client: TestClient) -> str:
    assert client.get("/pair?token=pair-token", follow_redirects=False).status_code == 303
    return client.get("/api/session").json()["client_id"]


def valid_pcm() -> bytes:
    return b"\x00\x20" * 16_000


def test_profile_status_requires_pairing_and_hides_embeddings(tmp_path, monkeypatch) -> None:
    web, speaker, _coordinator, _authorizations = make_client(tmp_path, monkeypatch)
    with web:
        assert web.get("/api/speaker/profile").status_code == 401
        pair(web)
        assert web.get("/api/speaker/profile").json() == {"enrolled": False}
        speaker.current = Profile()
        assert web.get("/api/speaker/profile").json() == {
            "enrolled": True,
            "created_at": speaker.current.created_at,
            "model_id": speaker.current.model_id,
        }


def test_enrollment_requires_exactly_three_bounded_samples(tmp_path, monkeypatch) -> None:
    web, speaker, _coordinator, _authorizations = make_client(tmp_path, monkeypatch)
    with web:
        pair(web)
        files = [("samples", (f"{index}.pcm", valid_pcm(), "application/octet-stream")) for index in range(3)]
        response = web.post("/api/speaker/enroll", files=files)
        assert response.status_code == 200 and response.json()["enrolled"] is True
        assert speaker.enrolled_samples == [valid_pcm()] * 3
        assert web.post("/api/speaker/enroll", files=files[:2]).status_code == 422


def test_accepted_verification_cancels_and_issues_bound_token(tmp_path, monkeypatch) -> None:
    web, speaker, coordinator, authorizations = make_client(tmp_path, monkeypatch)
    with web:
        client_id = pair(web)
        speaker.current = Profile()
        speaker.decision = SpeakerDecision(True, 0.81)
        response = web.post(
            "/api/speaker/verify?generation_id=3",
            content=valid_pcm(),
            headers={"content-type": "application/octet-stream"},
        )
        payload = response.json()
        assert response.status_code == 200
        assert payload["accepted"] is True
        assert payload["speaker_token"] == "speaker-token-with-at-least-thirty-two-characters"
        assert payload["score"] == 0.81
        assert payload["threshold"] == 0.60
        assert isinstance(payload["speaker_ms"], float)
        assert speaker.thresholds == [0.60]
        assert coordinator.cancelled == [(client_id, 3)]
        assert authorizations.issued == [(client_id, 3)]


def test_rejection_and_failures_never_cancel(tmp_path, monkeypatch) -> None:
    web, speaker, coordinator, authorizations = make_client(tmp_path, monkeypatch)
    with web:
        pair(web)
        speaker.current = Profile()
        response = web.post(
            "/api/speaker/verify?generation_id=3",
            content=valid_pcm(),
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is False
        assert response.json()["score"] == 0.2
        assert response.json()["threshold"] == 0.60

        speaker.failure = SpeakerProfileMissing("请先录入声音")
        assert web.post(
            "/api/speaker/verify?generation_id=4",
            content=valid_pcm(),
            headers={"content-type": "application/octet-stream"},
        ).status_code == 409

        speaker.failure = httpx.ConnectError("offline")
        assert web.post(
            "/api/speaker/verify?generation_id=5",
            content=valid_pcm(),
            headers={"content-type": "application/octet-stream"},
        ).status_code == 503
        assert coordinator.cancelled == [] and authorizations.issued == []


def test_bad_enrollment_is_a_validation_error(tmp_path, monkeypatch) -> None:
    web, speaker, _coordinator, _authorizations = make_client(tmp_path, monkeypatch)
    with web:
        pair(web)
        speaker.failure = SpeakerEnrollmentError("三段录音不像同一个人，请重新录入")
        files = [("samples", (f"{index}.pcm", valid_pcm(), "application/octet-stream")) for index in range(3)]
        response = web.post("/api/speaker/enroll", files=files)
        assert response.status_code == 422
        assert "不像同一个人" in response.json()["detail"]


def test_speaker_settings_are_authenticated_bounded_and_used(tmp_path, monkeypatch) -> None:
    web, speaker, _coordinator, _authorizations = make_client(tmp_path, monkeypatch)
    with web:
        assert web.get("/api/speaker/settings").status_code == 401
        pair(web)
        assert web.get("/api/speaker/settings").json() == {
            "threshold": 0.60,
            "minimum": 0.30,
            "maximum": 0.80,
        }
        assert web.put(
            "/api/speaker/settings", json={"threshold": 0.30}
        ).json()["threshold"] == 0.30
        assert web.put(
            "/api/speaker/settings", json={"threshold": 0.67}
        ).json()["threshold"] == 0.67
        for payload in (
            {"threshold": 0.29},
            {"threshold": 0.81},
            {"threshold": True},
            {"threshold": 0.60, "extra": 1},
        ):
            assert web.put("/api/speaker/settings", json=payload).status_code == 422

        speaker.current = Profile()
        web.post(
            "/api/speaker/verify?generation_id=7",
            content=valid_pcm(),
            headers={"content-type": "application/octet-stream"},
        )
        assert speaker.thresholds[-1] == 0.67
