from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_app.config import Settings


def test_settings_pin_local_models_and_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)

    assert settings.tts_gpu_uuid == "0"
    assert settings.tts_url == "http://127.0.0.1:8766"
    assert settings.tts_model_path == Path(
        "/home/agentvoice/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    )
    assert settings.tts_voice == "serena"
    assert settings.tts_language == "Chinese"
    assert settings.tts_initial_codec_chunk_frames is None
    assert settings.asr_device == "cpu"
    assert settings.asr_device_index == 0
    assert settings.asr_compute_type == "int8"
    assert not hasattr(settings, "reference_audio_path")
    assert not hasattr(settings, "reference_transcript")
    assert settings.sglang_url == "http://127.0.0.1:8060"
    assert settings.searxng_url == "http://127.0.0.1:8081"
    assert settings.https_port == 8443
    assert settings.http_port == 8088
    assert settings.data_dir == tmp_path
    assert settings.speaker_url == "http://127.0.0.1:8767"
    assert settings.speaker_threshold == 0.60
    assert settings.speaker_profile_path == Path(
        "/home/agentvoice/.local/share/claude-voice/speaker-profile.json"
    )


def test_runtime_paths_are_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)

    for value in (
        settings.claude_cli,
        settings.tts_model_path,
        settings.asr_model_path,
        settings.frontend_dist,
        settings.cert_path,
        settings.key_path,
    ):
        assert isinstance(value, Path)
        assert value.is_absolute()


def test_relative_runtime_path_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_CLAUDE_CLI", "bin/claude")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_initial_codec_chunk_frames_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_TTS_INITIAL_CODEC_CHUNK_FRAMES", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
