from __future__ import annotations

import json
import math
from pathlib import Path
import stat

import httpx
import pytest

from voice_app.speaker import (
    SpeakerAuthorizations,
    SpeakerEnrollmentError,
    SpeakerGate,
    SpeakerProfile,
    SpeakerProfileMissing,
    SpeakerProfileStore,
    SpeakerVerifierClient,
)


def vector(primary: int, secondary: int | None = None, secondary_value: float = 0.0) -> list[float]:
    result = [0.0] * 192
    result[primary] = 1.0
    if secondary is not None:
        result[secondary] = secondary_value
    norm = math.sqrt(sum(value * value for value in result))
    return [value / norm for value in result]


class FakeVerifier:
    def __init__(self, vectors: list[list[float]], model_id: str = "campplus-zh-16k-192") -> None:
        self.vectors = iter(vectors)
        self.model_id = model_id

    async def embed(self, _pcm: bytes) -> tuple[str, list[float]]:
        return self.model_id, next(self.vectors)


def profile() -> SpeakerProfile:
    embedding = tuple(vector(0))
    return SpeakerProfile(
        version=1,
        model_id="campplus-zh-16k-192",
        embeddings=(embedding, embedding, embedding),
        centroid=embedding,
        threshold=0.60,
        created_at="2026-07-19T03:00:00+00:00",
    )


async def test_enrollment_atomically_saves_only_embeddings_with_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "profile.json"
    verifier = FakeVerifier([vector(0), vector(0, 1, 0.1), vector(0, 2, 0.2)])
    gate = SpeakerGate(verifier, SpeakerProfileStore(path), threshold=0.60)

    enrolled = await gate.enroll([b"first raw pcm", b"second raw pcm", b"third raw pcm"])

    assert enrolled.model_id == "campplus-zh-16k-192"
    assert len(enrolled.centroid) == 192
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = path.read_text()
    assert "raw pcm" not in saved and "embedding" in saved
    assert SpeakerProfileStore(path).load() == enrolled


async def test_mixed_speakers_do_not_replace_existing_profile(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    store = SpeakerProfileStore(path)
    old = profile()
    store.save(old)
    gate = SpeakerGate(FakeVerifier([vector(0), vector(1), vector(2)]), store, threshold=0.60)

    with pytest.raises(SpeakerEnrollmentError, match="不像同一个人"):
        await gate.enroll([b"a", b"b", b"c"])

    assert store.load() == old


async def test_verify_uses_centroid_threshold_and_model_id(tmp_path: Path) -> None:
    store = SpeakerProfileStore(tmp_path / "profile.json")
    store.save(profile())
    accepted = SpeakerGate(FakeVerifier([vector(0, 1, 0.2)]), store, threshold=0.60)
    rejected = SpeakerGate(FakeVerifier([vector(1)]), store, threshold=0.60)
    changed = SpeakerGate(FakeVerifier([vector(0)], model_id="other"), store, threshold=0.60)

    assert (await accepted.verify(b"probe")).accepted is True
    assert (await rejected.verify(b"probe")).accepted is False
    with pytest.raises(SpeakerProfileMissing, match="重新录入"):
        await changed.verify(b"probe")


async def test_verify_accepts_an_immutable_per_call_threshold(tmp_path: Path) -> None:
    store = SpeakerProfileStore(tmp_path / "profile.json")
    store.save(profile())
    score_about_seventy = vector(0, 1, 1.0)
    gate = SpeakerGate(FakeVerifier([score_about_seventy, score_about_seventy]), store, 0.60)

    assert (await gate.verify(b"probe", threshold=0.70)).accepted is True
    assert (await gate.verify(b"probe", threshold=0.80)).accepted is False
    assert gate.threshold == 0.60


def test_corrupt_profile_is_quarantined_and_treated_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"version": 1, "centroid": [float("nan")]}))

    assert SpeakerProfileStore(path).load() is None
    assert not path.exists()
    assert len(list(tmp_path.glob("profile.json.corrupt-*"))) == 1


class Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_authorization_is_bound_one_time_and_expires() -> None:
    clock = Clock(10.0)
    store = SpeakerAuthorizations(ttl_seconds=5.0, clock=clock)
    token = store.issue("phone", 4)
    assert len(token) >= 32
    assert not store.consume("other", 4, token)
    assert store.consume("phone", 4, token)
    assert not store.consume("phone", 4, token)

    expired = store.issue("phone", 5)
    clock.value = 16.0
    assert not store.consume("phone", 5, expired)


async def test_verifier_client_normalizes_embedding_and_reports_health() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ready": True})
        assert request.headers["content-type"] == "application/octet-stream"
        return httpx.Response(
            200,
            json={"model_id": "campplus-zh-16k-192", "embedding": [2.0] + [0.0] * 191},
        )

    client = SpeakerVerifierClient(
        "http://speaker.local",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )
    model_id, embedding = await client.embed(b"pcm")
    assert model_id == "campplus-zh-16k-192"
    assert embedding == [1.0] + [0.0] * 191
    assert await client.health() is True
    await client.close()
