from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from faster_whisper import WhisperModel

from voice_app.config import Settings


class AudioValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str | None = None
    language_probability: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.text


class ASRService:
    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] = WhisperModel,
    ) -> None:
        self.settings = settings
        self._model_factory = model_factory
        self._model: Any | None = None
        self._inference_lock = asyncio.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    async def load(self) -> None:
        if self._model is not None:
            return
        async with self._inference_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(
                    self._model_factory,
                    str(self.settings.asr_model_path),
                    device=self.settings.asr_device,
                    device_index=self.settings.asr_device_index,
                    compute_type=self.settings.asr_compute_type,
                    cpu_threads=max(2, min(8, (await asyncio.to_thread(_cpu_count)))),
                    num_workers=1,
                    local_files_only=True,
                )

    async def transcribe(self, pcm: bytes) -> Transcript:
        if len(pcm) % 2:
            raise AudioValidationError("16 位 PCM 必须包含偶数字节")
        if not pcm:
            return Transcript(text="")
        samples = len(pcm) // 2
        maximum = self.settings.asr_sample_rate * self.settings.max_audio_seconds
        if samples > maximum:
            raise AudioValidationError("录音过长，请分段讲话")

        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if audio.size == 0 or float(np.sqrt(np.mean(np.square(audio)))) < 0.001:
            return Transcript(text="")

        await self.load()
        async with self._inference_lock:
            segments, info = await asyncio.to_thread(
                self._model.transcribe,
                audio,
                language=None,
                beam_size=2,
                best_of=2,
                temperature=0.0,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            text = await asyncio.to_thread(_consume_segments, segments)
        return Transcript(
            text=text,
            language=getattr(info, "language", None),
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        )


def _consume_segments(segments: Any) -> str:
    raw = "".join(str(getattr(segment, "text", "")) for segment in segments)
    normalized = re.sub(r"\s+", " ", raw).strip()
    normalized = re.sub(r"(?<=[，。！？；：、])\s+", "", normalized)
    normalized = re.sub(r"\s+(?=[，。！？；：、])", "", normalized)
    return normalized


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 4
