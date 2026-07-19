from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import time
from typing import Protocol

import httpx


EMBEDDING_SIZE = 192
PROFILE_VERSION = 1


class SpeakerError(RuntimeError):
    pass


class SpeakerEnrollmentError(SpeakerError):
    pass


class SpeakerProfileMissing(SpeakerError):
    pass


class SpeakerVerifier(Protocol):
    async def embed(self, pcm: bytes) -> tuple[str, list[float]]: ...


@dataclass(frozen=True, slots=True)
class SpeakerProfile:
    version: int
    model_id: str
    embeddings: tuple[tuple[float, ...], ...]
    centroid: tuple[float, ...]
    threshold: float
    created_at: str


@dataclass(frozen=True, slots=True)
class SpeakerDecision:
    accepted: bool
    score: float


def normalize(values: Sequence[float]) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != EMBEDDING_SIZE or not all(math.isfinite(value) for value in vector):
        raise ValueError("speaker embedding must contain 192 finite values")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise ValueError("speaker embedding norm must be positive")
    return [value / norm for value in vector]


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    normalized_left = normalize(left)
    normalized_right = normalize(right)
    return sum(a * b for a, b in zip(normalized_left, normalized_right, strict=True))


def _profile_from_payload(payload: object) -> SpeakerProfile:
    if not isinstance(payload, dict):
        raise ValueError("speaker profile must be an object")
    if payload.get("version") != PROFILE_VERSION:
        raise ValueError("unsupported speaker profile version")
    model_id = payload.get("model_id")
    created_at = payload.get("created_at")
    threshold = payload.get("threshold")
    raw_embeddings = payload.get("embeddings")
    raw_centroid = payload.get("centroid")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("invalid speaker model id")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("invalid profile timestamp")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("invalid speaker threshold")
    if not 0 <= float(threshold) <= 1:
        raise ValueError("invalid speaker threshold")
    if not isinstance(raw_embeddings, (list, tuple)) or len(raw_embeddings) != 3:
        raise ValueError("speaker profile requires three embeddings")
    if not isinstance(raw_centroid, (list, tuple)):
        raise ValueError("invalid speaker centroid")
    embeddings = tuple(tuple(normalize(item)) for item in raw_embeddings)
    centroid = tuple(normalize(raw_centroid))
    return SpeakerProfile(
        version=PROFILE_VERSION,
        model_id=model_id,
        embeddings=embeddings,
        centroid=centroid,
        threshold=float(threshold),
        created_at=created_at,
    )


class SpeakerProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SpeakerProfile | None:
        if not self.path.is_file():
            return None
        try:
            return _profile_from_payload(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            quarantine = self.path.with_name(f"{self.path.name}.corrupt-{time.time_ns()}")
            try:
                os.replace(self.path, quarantine)
            except OSError:
                pass
            return None

    def save(self, profile: SpeakerProfile) -> None:
        validated = _profile_from_payload(asdict(profile))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(asdict(validated), temporary, ensure_ascii=False, allow_nan=False)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


class SpeakerGate:
    def __init__(
        self,
        verifier: SpeakerVerifier,
        store: SpeakerProfileStore,
        threshold: float,
        min_enrollment_similarity: float = 0.55,
    ) -> None:
        self.verifier = verifier
        self.store = store
        self.threshold = threshold
        self.min_enrollment_similarity = min_enrollment_similarity

    def profile(self) -> SpeakerProfile | None:
        return self.store.load()

    async def enroll(self, samples: Sequence[bytes]) -> SpeakerProfile:
        if len(samples) != 3:
            raise SpeakerEnrollmentError("必须提交三段录音")
        results = [await self.verifier.embed(sample) for sample in samples]
        model_ids = {model_id for model_id, _embedding in results}
        if len(model_ids) != 1:
            raise SpeakerEnrollmentError("声纹模型版本不一致")
        vectors = [normalize(embedding) for _model_id, embedding in results]
        scores = [
            cosine(vectors[left], vectors[right])
            for left, right in ((0, 1), (0, 2), (1, 2))
        ]
        if min(scores) < self.min_enrollment_similarity:
            raise SpeakerEnrollmentError("三段录音不像同一个人，请重新录入")
        centroid = normalize(
            [sum(vector[index] for vector in vectors) / 3 for index in range(EMBEDDING_SIZE)]
        )
        profile = SpeakerProfile(
            version=PROFILE_VERSION,
            model_id=results[0][0],
            embeddings=tuple(tuple(vector) for vector in vectors),
            centroid=tuple(centroid),
            threshold=self.threshold,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.save(profile)
        return profile

    async def verify(
        self, pcm: bytes, *, threshold: float | None = None
    ) -> SpeakerDecision:
        profile = self.store.load()
        if profile is None:
            raise SpeakerProfileMissing("请先录入声音")
        model_id, embedding = await self.verifier.embed(pcm)
        if model_id != profile.model_id:
            raise SpeakerProfileMissing("声纹模型已变化，请重新录入")
        score = cosine(embedding, profile.centroid)
        selected_threshold = self.threshold if threshold is None else float(threshold)
        if not math.isfinite(selected_threshold) or not 0 <= selected_threshold <= 1:
            raise ValueError("speaker threshold must be finite and between zero and one")
        return SpeakerDecision(accepted=score >= selected_threshold, score=score)


class SpeakerVerifierClient:
    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def embed(self, pcm: bytes) -> tuple[str, list[float]]:
        response = await self._client.post(
            f"{self.url}/embed",
            content=pcm,
            headers={"content-type": "application/octet-stream"},
        )
        response.raise_for_status()
        payload = response.json()
        model_id = payload.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("speaker verifier returned an invalid model id")
        return model_id, normalize(payload.get("embedding", []))

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self.url}/health")
            return response.status_code == 200 and response.json().get("ready") is True
        except (httpx.HTTPError, ValueError):
            return False

    async def close(self) -> None:
        await self._client.aclose()


class SpeakerAuthorizations:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries: dict[tuple[str, int], tuple[bytes, float]] = {}

    def issue(self, client_id: str, generation_id: int) -> str:
        self._purge_expired()
        token = secrets.token_urlsafe(32)
        self._entries[(client_id, generation_id)] = (
            hashlib.sha256(token.encode()).digest(),
            self.clock() + self.ttl_seconds,
        )
        return token

    def consume(self, client_id: str, generation_id: int, token: str) -> bool:
        key = (client_id, generation_id)
        entry = self._entries.get(key)
        if entry is None:
            return False
        digest, expires_at = entry
        if expires_at <= self.clock():
            self._entries.pop(key, None)
            return False
        candidate = hashlib.sha256(token.encode()).digest()
        if not hmac.compare_digest(digest, candidate):
            return False
        self._entries.pop(key, None)
        return True

    def _purge_expired(self) -> None:
        now = self.clock()
        for key, (_digest, expires_at) in list(self._entries.items()):
            if expires_at <= now:
                self._entries.pop(key, None)
