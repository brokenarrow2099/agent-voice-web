from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
import json
from statistics import median
import sys
from time import monotonic

import httpx

from voice_app.voices import DEFAULT_TTS_VOICE, SUPPORTED_TTS_VOICES, validate_tts_voice


DEFAULT_TTS_URL = "http://127.0.0.1:8766"
DEFAULT_MODEL_PATH = "/home/agentvoice/Qwen3-TTS-12Hz-0.6B-CustomVoice"
PCM_BYTES_PER_SECOND = 24_000 * 2


class BenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TTSMetrics:
    ttfa_ms: float
    total_ms: float
    byte_count: int
    audio_seconds: float
    rtf: float
    chunk_count: int


def custom_voice_payload(
    text: str,
    *,
    model: str = DEFAULT_MODEL_PATH,
    voice: str = DEFAULT_TTS_VOICE,
    language: str = "Chinese",
    initial_codec_chunk_frames: int | None = None,
) -> dict[str, object]:
    text = text.strip()
    if not text:
        raise ValueError("TTS text cannot be empty")
    selected_voice = validate_tts_voice(voice)
    if initial_codec_chunk_frames is not None and (
        isinstance(initial_codec_chunk_frames, bool)
        or not 1 <= initial_codec_chunk_frames <= 64
    ):
        raise ValueError("initial codec chunk frames must be between 1 and 64")

    payload: dict[str, object] = {
        "model": model,
        "input": text,
        "task_type": "CustomVoice",
        "voice": selected_voice,
        "language": language,
        "stream": True,
        "stream_format": "audio",
        "response_format": "pcm",
    }
    if initial_codec_chunk_frames is not None:
        payload["initial_codec_chunk_frames"] = initial_codec_chunk_frames
    return payload


async def measure_tts_stream(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, object],
    *,
    clock: Callable[[], float] = monotonic,
) -> TTSMetrics:
    started_at = clock()
    first_audio_at: float | None = None
    byte_count = 0
    chunk_count = 0
    endpoint = f"{url.rstrip('/')}/v1/audio/speech"

    try:
        async with client.stream("POST", endpoint, json=payload) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                if first_audio_at is None:
                    first_audio_at = clock()
                byte_count += len(chunk)
                chunk_count += 1
    except httpx.HTTPError as exc:
        raise BenchmarkError(f"TTS HTTP request failed: {exc}") from exc

    finished_at = clock()
    if first_audio_at is None or byte_count == 0:
        raise BenchmarkError("TTS returned an empty PCM stream")
    if byte_count % 2:
        raise BenchmarkError("TTS returned an odd PCM byte count")

    total_seconds = finished_at - started_at
    audio_seconds = byte_count / PCM_BYTES_PER_SECOND
    return TTSMetrics(
        ttfa_ms=(first_audio_at - started_at) * 1_000,
        total_ms=total_seconds * 1_000,
        byte_count=byte_count,
        audio_seconds=audio_seconds,
        rtf=total_seconds / audio_seconds,
        chunk_count=chunk_count,
    )


async def wait_for_health(
    client: httpx.AsyncClient,
    url: str,
    wait_seconds: float,
) -> None:
    deadline = monotonic() + wait_seconds
    endpoint = f"{url.rstrip('/')}/health"
    last_error = "service did not return HTTP 200"
    while True:
        try:
            response = await client.get(endpoint)
            if response.status_code == 200:
                return
            last_error = f"health returned HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        if monotonic() >= deadline:
            raise BenchmarkError(f"TTS health check timed out: {last_error}")
        await asyncio.sleep(min(0.5, max(0.0, deadline - monotonic())))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure Qwen3-TTS raw PCM stream latency")
    parser.add_argument("--url", default=DEFAULT_TTS_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--text", default="你好，这是一段语音延迟测试。")
    parser.add_argument("--voice", choices=sorted(SUPPORTED_TTS_VOICES), default=DEFAULT_TTS_VOICE)
    parser.add_argument("--language", default="Chinese")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--wait-seconds", type=float, default=60.0)
    parser.add_argument("--warmup-only", action="store_true")
    parser.add_argument("--initial-codec-chunk-frames", type=int)
    parser.add_argument("--max-ttfa-ms", type=float)
    return parser


async def run_benchmark(args: argparse.Namespace) -> int:
    if args.runs < 1:
        raise BenchmarkError("--runs must be at least 1")
    if args.wait_seconds < 0:
        raise BenchmarkError("--wait-seconds cannot be negative")
    if args.max_ttfa_ms is not None and args.max_ttfa_ms <= 0:
        raise BenchmarkError("--max-ttfa-ms must be positive")

    payload = custom_voice_payload(
        args.text,
        model=args.model,
        voice=args.voice,
        language=args.language,
        initial_codec_chunk_frames=args.initial_codec_chunk_frames,
    )
    timeout = httpx.Timeout(300.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        await wait_for_health(client, args.url, args.wait_seconds)
        if args.warmup_only:
            metrics = await measure_tts_stream(client, args.url, payload)
            if args.max_ttfa_ms is not None and metrics.ttfa_ms > args.max_ttfa_ms:
                raise BenchmarkError(
                    f"TTFA {metrics.ttfa_ms:.1f} ms exceeded {args.max_ttfa_ms:.1f} ms"
                )
            print(json.dumps({"status": "warm"}, separators=(",", ":")))
            return 0

        # Keep reported measurements representative of the resident, warmed service.
        await measure_tts_stream(client, args.url, payload)
        measured: list[TTSMetrics] = []
        for run_number in range(1, args.runs + 1):
            metrics = await measure_tts_stream(client, args.url, payload)
            measured.append(metrics)
            print(
                json.dumps(
                    {"type": "run", "run": run_number, **asdict(metrics)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )

    median_ttfa = median(item.ttfa_ms for item in measured)
    summary = {
        "type": "summary",
        "runs": len(measured),
        "median_ttfa_ms": median_ttfa,
        "median_total_ms": median(item.total_ms for item in measured),
        "median_rtf": median(item.rtf for item in measured),
    }
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    if args.max_ttfa_ms is not None and median_ttfa > args.max_ttfa_ms:
        raise BenchmarkError(
            f"median TTFA {median_ttfa:.1f} ms exceeded {args.max_ttfa_ms:.1f} ms"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run_benchmark(args))
    except (BenchmarkError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
