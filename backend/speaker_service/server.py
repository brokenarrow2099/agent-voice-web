from __future__ import annotations

import asyncio
from array import array
from contextlib import asynccontextmanager
import math
import logging
import os
from pathlib import Path
import sys
import time
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request

from voice_app.latency import configure_latency_logging


MODEL_ID = "campplus-zh-16k-192"
SAMPLE_RATE = 16_000
MIN_AUDIO_BYTES = round(SAMPLE_RATE * 0.8) * 2
MAX_AUDIO_BYTES = SAMPLE_RATE * 15 * 2
MIN_RMS = 0.002
DEFAULT_MODEL_PATH = Path(
    "/home/agentvoice/agent-service-gateway/index-tts/checkpoints/hf_cache/"
    "campplus_cn_common.bin"
)
DEFAULT_INDEX_ROOT = Path("/home/agentvoice/agent-service-gateway/index-tts")
logger = logging.getLogger("voice_app.latency")


class EmbeddingEngine(Protocol):
    model_id: str
    ready: bool

    async def load(self) -> None: ...

    async def embed(self, pcm: bytes) -> list[float]: ...


class CampPlusEngine:
    model_id = MODEL_ID

    def __init__(self, model_path: Path, index_root: Path) -> None:
        self.model_path = model_path
        self.index_root = index_root
        self.ready = False
        self._model: object | None = None
        self._inference_lock = asyncio.Lock()

    async def load(self) -> None:
        if not self.model_path.is_file():
            raise FileNotFoundError(f"speaker model not found: {self.model_path}")
        if str(self.index_root) not in sys.path:
            sys.path.insert(0, str(self.index_root))
        import torch
        from indextts.s2mel.modules.campplus.DTDNN import CAMPPlus

        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        model = CAMPPlus(feat_dim=80, embedding_size=192)
        state = torch.load(self.model_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()
        self._model = model
        self.ready = True

    async def embed(self, pcm: bytes) -> list[float]:
        if not self.ready or self._model is None:
            raise RuntimeError("speaker model is not ready")
        async with self._inference_lock:
            return await asyncio.to_thread(self._embed_sync, pcm)

    def _embed_sync(self, pcm: bytes) -> list[float]:
        import numpy as np
        import torch
        import torch.nn.functional as functional
        import torchaudio

        waveform = (
            torch.from_numpy(np.frombuffer(pcm, dtype="<i2").copy())
            .float()
            .unsqueeze(0)
            / 32768.0
        )
        features = torchaudio.compliance.kaldi.fbank(
            waveform,
            num_mel_bins=80,
            dither=0,
            sample_frequency=SAMPLE_RATE,
        )
        features = features - features.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            vector = self._model(features.unsqueeze(0)).squeeze(0)  # type: ignore[operator]
            vector = functional.normalize(vector, dim=0)
        return vector.cpu().tolist()


def _default_engine() -> CampPlusEngine:
    return CampPlusEngine(
        Path(os.environ.get("VOICE_SPEAKER_MODEL_PATH", str(DEFAULT_MODEL_PATH))).expanduser(),
        Path(os.environ.get("VOICE_SPEAKER_INDEX_ROOT", str(DEFAULT_INDEX_ROOT))).expanduser(),
    )


def _validate_pcm(pcm: bytes) -> None:
    if len(pcm) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio body is too large")
    if not pcm or len(pcm) % 2 or len(pcm) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=422, detail="audio must be 16 kHz mono PCM16")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
    if rms < MIN_RMS:
        raise HTTPException(status_code=422, detail="audio is silent")


def _validate_embedding(embedding: list[float]) -> list[float]:
    if len(embedding) != 192 or not all(math.isfinite(value) for value in embedding):
        raise HTTPException(status_code=500, detail="speaker model returned an invalid embedding")
    norm = math.sqrt(sum(value * value for value in embedding))
    if norm <= 0:
        raise HTTPException(status_code=500, detail="speaker model returned an empty embedding")
    return [value / norm for value in embedding]


def create_app(engine: EmbeddingEngine | None = None) -> FastAPI:
    configure_latency_logging()
    runtime = engine or _default_engine()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.load()
        yield

    app = FastAPI(title="Speaker Verifier", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"ready": runtime.ready, "model_id": runtime.model_id}

    @app.post("/embed")
    async def embed(request: Request) -> dict[str, object]:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/octet-stream":
            raise HTTPException(status_code=415, detail="content type must be application/octet-stream")
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="audio body is too large")
        pcm = await request.body()
        _validate_pcm(pcm)
        started = time.perf_counter()
        embedding = _validate_embedding(await runtime.embed(pcm))
        logger.info(
            "event=speaker_embed model=%s audio_ms=%.1f embed_ms=%.1f",
            runtime.model_id,
            len(pcm) / 2 / SAMPLE_RATE * 1000,
            (time.perf_counter() - started) * 1000,
        )
        return {"model_id": runtime.model_id, "embedding": embedding}

    return app


app = create_app()
