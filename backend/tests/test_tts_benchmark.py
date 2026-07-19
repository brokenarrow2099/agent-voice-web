from __future__ import annotations

import httpx
import pytest

from voice_app.tts_benchmark import (
    BenchmarkError,
    custom_voice_payload,
    measure_tts_stream,
)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def client_with_chunks(chunks: list[bytes]) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/speech"
        return httpx.Response(200, stream=ChunkStream(chunks))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def measure_with_chunks(chunks: list[bytes]):
    ticks = iter([10.0, 10.125, 10.4])
    async with client_with_chunks(chunks) as client:
        return await measure_tts_stream(
            client,
            "http://127.0.0.1:8766",
            custom_voice_payload("测试"),
            clock=lambda: next(ticks),
        )


async def test_measurement_uses_first_nonempty_pcm_chunk_for_ttfa():
    metrics = await measure_with_chunks([b"", b"\x01\x00", b"\x02\x00"])

    assert metrics.ttfa_ms == 125.0
    assert metrics.total_ms == pytest.approx(400.0)
    assert metrics.byte_count == 4
    assert metrics.chunk_count == 2
    assert metrics.audio_seconds == pytest.approx(4 / (24_000 * 2))
    assert metrics.rtf == pytest.approx(0.4 / metrics.audio_seconds)


@pytest.mark.parametrize(
    ("chunks", "message"),
    [([], "empty"), ([b"\x00"], "odd")],
)
async def test_measurement_rejects_empty_or_odd_pcm(chunks, message):
    with pytest.raises(BenchmarkError, match=message):
        await measure_with_chunks(chunks)


def test_custom_voice_payload_is_reference_free_raw_pcm_stream():
    payload = custom_voice_payload(" 你好 ", voice="vivian", initial_codec_chunk_frames=2)

    assert payload == {
        "model": "/home/agentvoice/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "input": "你好",
        "task_type": "CustomVoice",
        "voice": "vivian",
        "language": "Chinese",
        "stream": True,
        "stream_format": "audio",
        "response_format": "pcm",
        "initial_codec_chunk_frames": 2,
    }
    assert not {"ref_audio", "ref_text", "x_vector_only_mode"} & payload.keys()


@pytest.mark.parametrize("frames", [0, 65])
def test_custom_voice_payload_bounds_initial_codec_chunk_frames(frames):
    with pytest.raises(ValueError, match="between 1 and 64"):
        custom_voice_payload("测试", initial_codec_chunk_frames=frames)


def test_custom_voice_payload_rejects_unknown_voice():
    with pytest.raises(ValueError, match="unsupported TTS voice"):
        custom_voice_payload("测试", voice="unknown")
