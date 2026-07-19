from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from voice_app.voices import DEFAULT_TTS_VOICE, TTSVoice, validate_tts_voice


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Validated configuration for the local voice gateway."""

    model_config = SettingsConfigDict(
        env_file=Path.home() / ".config/claude-voice/voice.env",
        env_prefix="VOICE_",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    lan_ip: str = "192.0.2.10"
    wireguard_ip: str = "10.0.0.2"
    lan_hostname: str = "voice-host.local"
    https_port: int = 8443
    http_port: int = 8088

    tts_gpu_uuid: str = "0"
    tts_url: str = "http://127.0.0.1:8766"
    sglang_url: str = "http://127.0.0.1:8060"
    searxng_url: str = "http://127.0.0.1:8081"
    speaker_url: str = "http://127.0.0.1:8767"

    claude_cli: Path = Path("/home/agentvoice/.hermes/node/bin/claude")
    claude_workdir: Path = Path("/home/agentvoice")
    tts_model_path: Path = Path(
        "/home/agentvoice/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    )
    tts_voice: TTSVoice = DEFAULT_TTS_VOICE
    tts_language: str = "Chinese"
    tts_initial_codec_chunk_frames: int | None = Field(default=None, ge=1, le=64)
    asr_model_path: Path = Path(
        "/home/agentvoice/agent-service-gateway/models/faster-whisper/small"
    )
    asr_device: Literal["cpu", "cuda"] = "cpu"
    asr_device_index: int = Field(default=0, ge=0)
    asr_compute_type: Literal["int8", "float16"] = "int8"
    speaker_model_path: Path = Path(
        "/home/agentvoice/agent-service-gateway/index-tts/checkpoints/hf_cache/"
        "campplus_cn_common.bin"
    )
    speaker_index_root: Path = Path("/home/agentvoice/agent-service-gateway/index-tts")
    speaker_profile_path: Path = Path(
        "/home/agentvoice/.local/share/claude-voice/speaker-profile.json"
    )
    frontend_dist: Path = PROJECT_ROOT / "frontend/dist"
    data_dir: Path = Path("/home/agentvoice/.local/share/claude-voice")
    cert_path: Path = Path("/home/agentvoice/.config/claude-voice/certs/server.crt")
    key_path: Path = Path("/home/agentvoice/.config/claude-voice/certs/server.key")
    ca_cert_path: Path = Path("/home/agentvoice/.config/claude-voice/certs/ca.crt")

    pairing_token: str = ""
    cookie_secret: str = ""
    anthropic_api_key: str = Field(
        default="local-sglang-key",
        validation_alias=AliasChoices("VOICE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    cookie_name: str = "claude_voice_session"
    cookie_max_age: int = 60 * 60 * 24 * 90

    asr_sample_rate: int = 16_000
    tts_sample_rate: int = 24_000
    max_audio_seconds: int = 45
    max_ws_frame_bytes: int = 16_000 * 2 * 5
    sentence_queue_size: int = 8
    claude_timeout_seconds: float = 600
    speaker_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    speaker_min_enrollment_similarity: float = Field(default=0.55, ge=0.0, le=1.0)
    speaker_verify_timeout_seconds: float = Field(default=2.0, gt=0.0, le=10.0)
    speaker_authorization_ttl_seconds: float = Field(default=60.0, gt=0.0, le=300.0)
    speaker_max_audio_seconds: int = Field(default=15, ge=5, le=30)

    @field_validator(
        "claude_cli",
        "claude_workdir",
        "tts_model_path",
        "asr_model_path",
        "speaker_model_path",
        "speaker_index_root",
        "speaker_profile_path",
        "frontend_dist",
        "data_dir",
        "cert_path",
        "key_path",
        "ca_cert_path",
    )
    @classmethod
    def absolute_paths_only(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError("runtime paths must be absolute")
        return expanded

    @field_validator("tts_voice", mode="before")
    @classmethod
    def official_tts_voices_only(cls, value: object) -> TTSVoice:
        if not isinstance(value, str):
            raise ValueError("TTS voice must be a string")
        return validate_tts_voice(value)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "sessions.sqlite3"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


if __name__ == "__main__":
    settings = get_settings()
    safe = settings.model_dump(mode="json", exclude={"pairing_token", "cookie_secret", "anthropic_api_key"})
    print(json.dumps(safe, ensure_ascii=False, indent=2))
