from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import math
import time
from collections.abc import Callable, Mapping


logger = logging.getLogger("voice_app.latency")


def configure_latency_logging() -> None:
    """Route structured latency records through Uvicorn's journal handler."""
    uvicorn_handlers = (
        logging.getLogger("uvicorn.error").handlers
        or logging.getLogger("uvicorn").handlers
    )
    logger.setLevel(logging.INFO)
    if uvicorn_handlers:
        logger.handlers = list(uvicorn_handlers)
        logger.propagate = False


def safe_client_label(client_id: str) -> str:
    return hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:10]


def log_latency(event_name: str, client_id: str, values: Mapping[str, object]) -> None:
    parts = [f"event={event_name}", f"client={safe_client_label(client_id)}"]
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, float):
            if not math.isfinite(value):
                continue
            rendered = f"{value:.1f}"
        elif isinstance(value, bool):
            rendered = str(value).lower()
        elif isinstance(value, (int, str)):
            rendered = str(value).replace(" ", "_")
        else:
            continue
        parts.append(f"{key}={rendered}")
    logger.info(" ".join(parts))


@dataclass(slots=True)
class TurnMetrics:
    turn_id: int
    generation_id: int
    audio_ms: float
    clock: Callable[[], float] = time.perf_counter
    sentence_count: int = 0
    asr_ms: float | None = None
    model_first_text_ms: float | None = None
    first_sentence_ms: float | None = None
    tts_first_audio_ms: float | None = None
    response_first_audio_ms: float | None = None
    model_total_ms: float | None = None
    turn_total_ms: float | None = None
    outcome: str | None = None
    _started_at: float = field(init=False, repr=False)
    _asr_started_at: float | None = field(default=None, init=False, repr=False)
    _model_started_at: float | None = field(default=None, init=False, repr=False)
    _tts_started_at: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._started_at = self.clock()

    def _elapsed(self, started_at: float) -> float:
        return max(0.0, (self.clock() - started_at) * 1000)

    def start_asr(self) -> None:
        self._asr_started_at = self.clock()

    def finish_asr(self) -> None:
        if self._asr_started_at is not None:
            self.asr_ms = self._elapsed(self._asr_started_at)

    def start_model(self) -> None:
        self._model_started_at = self.clock()

    def mark_model_first_text(self) -> None:
        if self.model_first_text_ms is None and self._model_started_at is not None:
            self.model_first_text_ms = self._elapsed(self._model_started_at)

    def mark_first_sentence(self) -> None:
        if self.first_sentence_ms is None and self._model_started_at is not None:
            self.first_sentence_ms = self._elapsed(self._model_started_at)

    def finish_model(self) -> None:
        if self._model_started_at is not None:
            self.model_total_ms = self._elapsed(self._model_started_at)

    def start_tts(self) -> None:
        if self._tts_started_at is None:
            self._tts_started_at = self.clock()

    def mark_tts_first_audio(self) -> None:
        if self.tts_first_audio_ms is None and self._tts_started_at is not None:
            self.tts_first_audio_ms = self._elapsed(self._tts_started_at)
            self.response_first_audio_ms = self._elapsed(self._started_at)

    def increment_sentence_count(self) -> None:
        self.sentence_count += 1

    def finish(self, outcome: str) -> None:
        self.outcome = outcome
        self.turn_total_ms = self._elapsed(self._started_at)

    def public_snapshot(self, *, final: bool) -> dict[str, object]:
        values: dict[str, object] = {
            "turn_id": self.turn_id,
            "generation_id": self.generation_id,
            "final": final,
            "audio_ms": self.audio_ms,
            "asr_ms": self.asr_ms,
            "model_first_text_ms": self.model_first_text_ms,
            "first_sentence_ms": self.first_sentence_ms,
            "tts_first_audio_ms": self.tts_first_audio_ms,
            "response_first_audio_ms": self.response_first_audio_ms,
            "model_total_ms": self.model_total_ms,
            "turn_total_ms": self.turn_total_ms,
            "sentence_count": self.sentence_count,
            "outcome": self.outcome,
        }
        return {
            key: round(value, 1) if isinstance(value, float) else value
            for key, value in values.items()
            if value is not None
        }
