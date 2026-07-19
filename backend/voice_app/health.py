from __future__ import annotations

import os

import httpx

from voice_app.asr import ASRService
from voice_app.config import Settings
from voice_app.tts import TTSClient
from voice_app.speaker import SpeakerVerifierClient


class HealthChecker:
    def __init__(
        self,
        settings: Settings,
        asr: ASRService,
        tts: TTSClient,
        speaker: SpeakerVerifierClient,
    ) -> None:
        self.settings = settings
        self.asr = asr
        self.tts = tts
        self.speaker = speaker
        self._client = httpx.AsyncClient(timeout=2.0)

    async def readiness(self) -> dict[str, object]:
        dependencies: dict[str, bool] = {
            "asr": self.asr.is_loaded,
            "tts": await self.tts.health(),
            "claude": self.settings.claude_cli.is_file()
            and os.access(self.settings.claude_cli, os.X_OK),
            "sglang": await self._sglang_health(),
            "speaker": await self.speaker.health(),
        }
        return {"ready": all(dependencies.values()), "dependencies": dependencies}

    async def close(self) -> None:
        await self._client.aclose()

    async def _sglang_health(self) -> bool:
        try:
            response = await self._client.get(f"{self.settings.sglang_url.rstrip('/')}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False
