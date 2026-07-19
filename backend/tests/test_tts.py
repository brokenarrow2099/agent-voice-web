from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from voice_app.config import Settings
from voice_app.tts import TTSClient, TTSStreamError


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def settings(tmp_path, monkeypatch, *, initial_frames: str | None = None):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path / "data"))
    if initial_frames is None:
        monkeypatch.delenv("VOICE_TTS_INITIAL_CODEC_CHUNK_FRAMES", raising=False)
    else:
        monkeypatch.setenv("VOICE_TTS_INITIAL_CODEC_CHUNK_FRAMES", initial_frames)
    return Settings(_env_file=None)


async def test_payload_uses_streaming_custom_voice_without_reference(tmp_path, monkeypatch):
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, stream=ChunkStream([b"\x01\x00"]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = TTSClient(settings(tmp_path, monkeypatch), client=client)
    assert b"".join([chunk async for chunk in tts.stream("你好", generation_id=1)]) == b"\x01\x00"

    payload = json.loads(seen[0].content)
    assert payload == {
        "model": "/home/agentvoice/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "input": "你好",
        "task_type": "CustomVoice",
        "voice": "serena",
        "language": "Chinese",
        "stream": True,
        "stream_format": "audio",
        "response_format": "pcm",
    }
    assert not {"ref_audio", "ref_text", "x_vector_only_mode"} & payload.keys()
    await client.aclose()


async def test_payload_includes_configured_initial_codec_chunk_frames(tmp_path, monkeypatch):
    config = settings(tmp_path, monkeypatch, initial_frames="2")
    tts = TTSClient(config, client=httpx.AsyncClient())
    payload = tts.build_payload("一句话")
    assert payload["initial_codec_chunk_frames"] == 2
    await tts.close()


async def test_payload_accepts_official_voice_override(tmp_path, monkeypatch):
    tts = TTSClient(settings(tmp_path, monkeypatch), client=httpx.AsyncClient())
    assert tts.build_payload("你好", voice="uncle_fu")["voice"] == "uncle_fu"
    await tts.close()


async def test_payload_rejects_unknown_voice_override(tmp_path, monkeypatch):
    tts = TTSClient(settings(tmp_path, monkeypatch), client=httpx.AsyncClient())
    with pytest.raises(ValueError, match="unsupported TTS voice"):
        tts.build_payload("你好", voice="../../voice")
    await tts.close()


async def test_odd_http_chunks_are_reassembled_as_int16(tmp_path, monkeypatch):
    expected = bytes(range(12))

    async def handler(request):
        return httpx.Response(200, stream=ChunkStream([expected[:1], expected[1:4], expected[4:9], expected[9:]]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = TTSClient(settings(tmp_path, monkeypatch), client=client)
    chunks = [chunk async for chunk in tts.stream("测试", generation_id=4)]
    assert b"".join(chunks) == expected
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
    await client.aclose()


async def test_truncated_sample_raises(tmp_path, monkeypatch):
    async def handler(request):
        return httpx.Response(200, stream=ChunkStream([b"\x00\x01\x02"]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = TTSClient(settings(tmp_path, monkeypatch), client=client)
    with pytest.raises(TTSStreamError, match="半个采样"):
        _ = [chunk async for chunk in tts.stream("测试", generation_id=4)]
    await client.aclose()


async def test_failed_request_health_checks_and_retries_once(tmp_path, monkeypatch):
    speech_calls = 0
    health_calls = 0

    async def handler(request):
        nonlocal speech_calls, health_calls
        if request.url.path == "/health":
            health_calls += 1
            return httpx.Response(200, json={"status": "ok"})
        speech_calls += 1
        if speech_calls == 1:
            return httpx.Response(503, text="warming")
        return httpx.Response(200, stream=ChunkStream([b"\x01\x00"]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = TTSClient(settings(tmp_path, monkeypatch), client=client)
    audio = b"".join([chunk async for chunk in tts.stream("重试", generation_id=2)])
    assert audio == b"\x01\x00"
    assert speech_calls == 2 and health_calls == 1
    await client.aclose()


async def test_does_not_retry_after_audio_was_emitted(tmp_path, monkeypatch):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\x01\x00"
            raise httpx.ReadError("broken")

    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=BrokenStream())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = TTSClient(settings(tmp_path, monkeypatch), client=client)
    with pytest.raises(TTSStreamError):
        _ = [chunk async for chunk in tts.stream("只发一次", generation_id=2)]
    assert calls == 1
    await client.aclose()


async def test_empty_audio_stream_raises_after_one_retry(tmp_path, monkeypatch):
    speech_calls = 0

    async def handler(request):
        nonlocal speech_calls
        if request.url.path == "/health":
            return httpx.Response(200)
        speech_calls += 1
        return httpx.Response(200, stream=ChunkStream([]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = TTSClient(settings(tmp_path, monkeypatch), client=client)
    with pytest.raises(TTSStreamError, match="空音频流"):
        _ = [chunk async for chunk in tts.stream("没有音频", generation_id=3)]
    assert speech_calls == 2
    await client.aclose()


async def test_cancellation_closes_upstream_http_stream(tmp_path, monkeypatch):
    class BlockingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.closed = asyncio.Event()

        async def __aiter__(self):
            self.started.set()
            await asyncio.Event().wait()
            yield b""  # pragma: no cover

        async def aclose(self) -> None:
            self.closed.set()

    stream = BlockingStream()

    async def handler(request):
        return httpx.Response(200, stream=stream)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = TTSClient(settings(tmp_path, monkeypatch), client=client)

    async def drain() -> None:
        _ = [chunk async for chunk in tts.stream("打断", generation_id=8)]

    task = asyncio.create_task(drain())
    await stream.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(stream.closed.wait(), timeout=1)
    await client.aclose()
