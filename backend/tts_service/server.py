from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_MODEL = "/home/agentvoice/Qwen3-TTS-12Hz-0.6B-Base"
DEFAULT_REFERENCE = "/home/agentvoice/comfy/ComfyUI/input/reference.wav"


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = DEFAULT_MODEL
    input: str = Field(min_length=1, max_length=1000)
    task_type: Literal["Base"] = "Base"
    language: str = "Auto"
    ref_audio: str | None = None
    ref_text: str | None = None
    x_vector_only_mode: bool = True
    stream: bool = True
    stream_format: Literal["audio"] = "audio"
    response_format: Literal["pcm"] = "pcm"


class SpeechEngine(Protocol):
    model_path: str
    ready: bool

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        ref_audio: str | None,
        ref_text: str | None,
        x_vector_only_mode: bool,
    ) -> tuple[np.ndarray, int]: ...


class QwenSpeechEngine:
    """One resident official Qwen3-TTS model, serialized on a single GPU."""

    def __init__(self) -> None:
        import torch
        from qwen_tts import Qwen3TTSModel

        self.model_path = os.environ.get("QWEN3_TTS_MODEL", DEFAULT_MODEL)
        self.reference_path = Path(os.environ.get("QWEN3_TTS_REFERENCE_AUDIO", DEFAULT_REFERENCE)).expanduser()
        self.ready = False
        self._lock = threading.Lock()
        self._prompt_cache: OrderedDict[str, object] = OrderedDict()
        self._torch = torch
        self._model = Qwen3TTSModel.from_pretrained(
            self.model_path,
            device_map="cuda:0",
            dtype=torch.bfloat16,
        )
        default_bytes = self.reference_path.read_bytes()
        self._default_digest = hashlib.sha256(default_bytes).hexdigest()
        self._default_prompt = self._model.create_voice_clone_prompt(
            ref_audio=str(self.reference_path),
            x_vector_only_mode=True,
        )
        self._prompt_cache[f"{self._default_digest}:xvec"] = self._default_prompt
        # Pay one-time kernel/JIT cost before health reports ready.
        self._generate("预热。", "Auto", self._default_prompt, max_new_tokens=64)
        self.ready = True

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        ref_audio: str | None,
        ref_text: str | None,
        x_vector_only_mode: bool,
    ) -> tuple[np.ndarray, int]:
        with self._lock:
            prompt = self._voice_prompt(ref_audio, ref_text, x_vector_only_mode)
            return self._generate(text, language, prompt, max_new_tokens=1024)

    def _generate(self, text: str, language: str, prompt: object, *, max_new_tokens: int):
        with self._torch.inference_mode():
            wavs, sample_rate = self._model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=prompt,
                max_new_tokens=max_new_tokens,
            )
        return np.asarray(wavs[0], dtype=np.float32), int(sample_rate)

    def _voice_prompt(self, ref_audio: str | None, ref_text: str | None, x_vector_only_mode: bool):
        if not ref_audio and x_vector_only_mode and not ref_text:
            return self._default_prompt

        source = ref_audio or str(self.reference_path)
        digest = self._audio_digest(source)
        mode = "xvec" if x_vector_only_mode else f"icl:{ref_text or ''}"
        key = f"{digest}:{mode}"
        cached = self._prompt_cache.get(key)
        if cached is not None:
            self._prompt_cache.move_to_end(key)
            return cached

        prompt = self._model.create_voice_clone_prompt(
            ref_audio=source,
            ref_text=ref_text,
            x_vector_only_mode=x_vector_only_mode,
        )
        self._prompt_cache[key] = prompt
        self._prompt_cache.move_to_end(key)
        while len(self._prompt_cache) > 4:
            self._prompt_cache.popitem(last=False)
        return prompt

    @staticmethod
    def _audio_digest(source: str) -> str:
        if source.startswith("data:"):
            try:
                encoded = source.split(",", 1)[1]
                data = base64.b64decode(encoded, validate=True)
            except (IndexError, ValueError) as exc:
                raise ValueError("Invalid reference audio data URL") from exc
        else:
            data = Path(source).expanduser().read_bytes()
        return hashlib.sha256(data).hexdigest()


def _pcm16(waveform: np.ndarray) -> bytes:
    clipped = np.clip(np.asarray(waveform, dtype=np.float32), -1.0, 1.0)
    return np.rint(clipped * 32767.0).astype("<i2", copy=False).tobytes()


def _chunks(data: bytes, size: int = 4096) -> Iterator[bytes]:
    for offset in range(0, len(data), size):
        yield data[offset : offset + size]


def create_app(engine: SpeechEngine | None = None) -> FastAPI:
    state: dict[str, SpeechEngine | None] = {"engine": engine}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if state["engine"] is None:
            state["engine"] = await asyncio.to_thread(QwenSpeechEngine)
        yield

    app = FastAPI(title="Resident Qwen3-TTS", lifespan=lifespan)

    @app.get("/health")
    async def health():
        current = state["engine"]
        return {"status": "ok" if current and current.ready else "loading", "ready": bool(current and current.ready)}

    @app.get("/v1/models")
    async def models():
        current = state["engine"]
        return {"object": "list", "data": [{"id": current.model_path if current else DEFAULT_MODEL, "object": "model"}]}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest):
        current = state["engine"]
        if current is None or not current.ready:
            raise HTTPException(status_code=503, detail="Qwen3-TTS is loading")
        try:
            waveform, sample_rate = await asyncio.to_thread(
                current.synthesize,
                text=request.input.strip(),
                language=request.language,
                ref_audio=request.ref_audio,
                ref_text=request.ref_text,
                x_vector_only_mode=request.x_vector_only_mode,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        pcm = _pcm16(waveform)
        return StreamingResponse(
            _chunks(pcm),
            media_type="audio/pcm",
            headers={"X-Audio-Sample-Rate": str(sample_rate), "X-Audio-Channels": "1"},
        )

    return app


app = create_app()
