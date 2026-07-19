#!/usr/bin/env python3
"""Benchmark the loopback CAMPPlus service without exposing audio or embeddings."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import time
import wave

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enroll",
        type=Path,
        nargs=3,
        required=True,
        metavar=("ONE_WAV", "TWO_WAV", "THREE_WAV"),
    )
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getframerate() != 16_000
            or source.getsampwidth() != 2
            or source.getcomptype() != "NONE"
        ):
            raise ValueError(f"{path} must be an uncompressed mono 16 kHz PCM16 WAV")
        return source.readframes(source.getnframes())


def normalize(values: list[float]) -> list[float]:
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("speaker service returned a non-finite embedding")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("speaker service returned an empty embedding")
    return [value / norm for value in values]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("speaker embeddings have different dimensions")
    return sum(a * b for a, b in zip(left, right, strict=True))


def request_embedding(
    client: httpx.Client,
    url: str,
    path: Path,
) -> tuple[str, list[float], float]:
    started = time.perf_counter()
    response = client.post(
        f"{url.rstrip('/')}/embed",
        content=read_pcm(path),
        headers={"content-type": "application/octet-stream"},
    )
    latency_ms = (time.perf_counter() - started) * 1_000
    response.raise_for_status()
    payload = response.json()
    model_id = payload.get("model_id")
    raw_embedding = payload.get("embedding")
    if not isinstance(model_id, str) or not isinstance(raw_embedding, list):
        raise ValueError("speaker service returned an invalid response")
    try:
        embedding = normalize([float(value) for value in raw_embedding])
    except (TypeError, ValueError) as exc:
        raise ValueError("speaker service returned an invalid embedding") from exc
    return model_id, embedding, latency_ms


def benchmark(args: argparse.Namespace) -> dict[str, object]:
    paths = [*args.enroll, args.probe]
    model_id: str | None = None
    embeddings: list[list[float]] = []
    latencies: list[float] = []
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for path in paths:
            current_model, embedding, latency = request_embedding(client, args.url, path)
            if model_id is None:
                model_id = current_model
            elif current_model != model_id:
                raise ValueError("speaker service model_id changed during the benchmark")
            embeddings.append(embedding)
            latencies.append(latency)

    enrollment = embeddings[:3]
    pairwise = [
        cosine(enrollment[0], enrollment[1]),
        cosine(enrollment[0], enrollment[2]),
        cosine(enrollment[1], enrollment[2]),
    ]
    centroid = normalize(
        [sum(vector[index] for vector in enrollment) / 3 for index in range(len(enrollment[0]))]
    )
    probe_score = cosine(centroid, embeddings[3])
    rounded_latencies = [round(value, 2) for value in latencies]
    return {
        "model_id": model_id,
        "enrollment_pairwise": [round(value, 4) for value in pairwise],
        "probe_score": round(probe_score, 4),
        "accepted_at_0_60": probe_score >= 0.60,
        "latency_ms": rounded_latencies,
        "median_latency_ms": round(statistics.median(latencies), 2),
    }


def main() -> None:
    result = benchmark(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
