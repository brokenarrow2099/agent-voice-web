from __future__ import annotations

import numpy as np
import pytest

from voice_app.asr import ASRService, AudioValidationError
from voice_app.config import Settings


class Segment:
    def __init__(self, text):
        self.text = text


class Info:
    language = "zh"
    language_probability = 0.97


class FakeWhisper:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter([Segment("  你好， "), Segment(" 世界。  ")]), Info()


def pcm_tone(seconds=0.4, rate=16_000, amplitude=0.2):
    time = np.arange(int(seconds * rate), dtype=np.float32) / rate
    samples = np.sin(2 * np.pi * 440 * time) * amplitude
    return (samples * 32767).astype("<i2").tobytes()


def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    return Settings(_env_file=None)


async def test_odd_pcm_is_rejected(tmp_path, monkeypatch):
    service = ASRService(settings(tmp_path, monkeypatch), model_factory=lambda *_a, **_k: FakeWhisper())
    with pytest.raises(AudioValidationError, match="偶数"):
        await service.transcribe(b"\x00")


@pytest.mark.parametrize("pcm", [b"", b"\x00\x00" * 8000])
async def test_empty_or_silent_audio_returns_empty_transcript(pcm, tmp_path, monkeypatch):
    service = ASRService(settings(tmp_path, monkeypatch), model_factory=lambda *_a, **_k: FakeWhisper())
    transcript = await service.transcribe(pcm)
    assert transcript.text == ""
    assert transcript.is_empty


async def test_audio_over_max_duration_is_rejected(tmp_path, monkeypatch):
    config = settings(tmp_path, monkeypatch)
    service = ASRService(config, model_factory=lambda *_a, **_k: FakeWhisper())
    too_long = b"\x01\x00" * (config.asr_sample_rate * config.max_audio_seconds + 1)
    with pytest.raises(AudioValidationError, match="过长"):
        await service.transcribe(too_long)


async def test_transcript_is_normalized_and_model_loads_once(tmp_path, monkeypatch):
    created = []
    fake = FakeWhisper()

    def factory(path, **kwargs):
        created.append((path, kwargs))
        return fake

    service = ASRService(settings(tmp_path, monkeypatch), model_factory=factory)
    await service.load()
    first = await service.transcribe(pcm_tone())
    second = await service.transcribe(pcm_tone())

    assert first.text == "你好，世界。"
    assert first.language == "zh"
    assert first.language_probability == pytest.approx(0.97)
    assert second.text == first.text
    assert len(created) == 1
    assert created[0][1]["device"] == "cpu"
    assert created[0][1]["compute_type"] == "int8"
    audio, options = fake.calls[0]
    assert audio.dtype == np.float32
    assert options["vad_filter"] is True


async def test_model_load_uses_configured_gpu_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_ASR_DEVICE", "cuda")
    monkeypatch.setenv("VOICE_ASR_DEVICE_INDEX", "0")
    monkeypatch.setenv("VOICE_ASR_COMPUTE_TYPE", "float16")
    created = []

    def factory(path, **kwargs):
        created.append((path, kwargs))
        return FakeWhisper()

    service = ASRService(settings(tmp_path, monkeypatch), model_factory=factory)
    await service.load()

    assert created[0][1]["device"] == "cuda"
    assert created[0][1]["device_index"] == 0
    assert created[0][1]["compute_type"] == "float16"
