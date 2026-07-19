from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx

from voice_app.config import Settings
from voice_app.voices import TTSVoice, validate_tts_voice


class TTSStreamError(RuntimeError):
    pass


class TTSClient:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=5.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        )

    def build_payload(self, text: str, *, voice: str | None = None) -> dict[str, object]:
        text = text.strip()
        if not text:
            raise ValueError("TTS text cannot be empty")
        selected_voice = validate_tts_voice(voice or self.settings.tts_voice)
        payload: dict[str, object] = {
            "model": str(self.settings.tts_model_path),
            "input": text,
            "task_type": "CustomVoice",
            "voice": selected_voice,
            "language": self.settings.tts_language,
            "stream": True,
            "stream_format": "audio",
            "response_format": "pcm",
        }
        if self.settings.tts_initial_codec_chunk_frames is not None:
            payload["initial_codec_chunk_frames"] = (
                self.settings.tts_initial_codec_chunk_frames
            )
        return payload

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self.settings.tts_url.rstrip('/')}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def stream(
        self,
        text: str,
        generation_id: int,
        *,
        voice: TTSVoice | None = None,
    ) -> AsyncIterator[bytes]:
        del generation_id  # Cancellation is controlled by closing this generator/task.
        payload = self.build_payload(text, voice=voice)
        endpoint = f"{self.settings.tts_url.rstrip('/')}/v1/audio/speech"
        for attempt in range(2):
            emitted = False
            carry = b""
            try:
                async with self._client.stream("POST", endpoint, json=payload) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        combined = carry + chunk
                        even_length = len(combined) - len(combined) % 2
                        if even_length:
                            emitted = True
                            yield combined[:even_length]
                        carry = combined[even_length:]
                    if carry:
                        raise TTSStreamError("TTS PCM 流以半个采样结束")
                    if not emitted:
                        raise TTSStreamError("TTS 服务返回了空音频流")
                    return
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, TTSStreamError) as exc:
                if emitted or attempt == 1:
                    if isinstance(exc, TTSStreamError):
                        raise
                    raise TTSStreamError(f"TTS 流式请求失败: {exc}") from exc
                if not await self.health():
                    raise TTSStreamError("TTS 服务不可用") from exc
        raise TTSStreamError("TTS 请求重试失败")  # pragma: no cover

    async def close(self) -> None:
        await self._client.aclose()
