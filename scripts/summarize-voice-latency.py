#!/usr/bin/env python3
"""Summarize safe numeric voice latency fields from journal text on stdin."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import math
import statistics
import sys
from typing import TextIO


METRICS = (
    "speaker_ms",
    "embed_ms",
    "speaker_roundtrip_ms",
    "audio_ms",
    "asr_ms",
    "model_first_text_ms",
    "first_sentence_ms",
    "tts_first_audio_ms",
    "response_first_audio_ms",
    "commit_to_transcript_ms",
    "commit_to_first_text_ms",
    "commit_to_first_audio_ms",
    "first_audio_to_enqueue_ms",
    "model_total_ms",
    "turn_total_ms",
)
MAX_MILLISECONDS = 3_600_000.0


def parse_lines(lines: Iterable[str], event: str | None = None) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {}
    for line in lines:
        fields: dict[str, str] = {}
        for token in line.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            fields[key] = value
        if event is not None and fields.get("event") != event:
            continue
        for metric in METRICS:
            raw_value = fields.get(metric)
            if raw_value is None:
                continue
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if not math.isfinite(value) or value < 0 or value > MAX_MILLISECONDS:
                continue
            samples.setdefault(metric, []).append(value)
    return samples


def summarize(samples: dict[str, list[float]]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for metric in METRICS:
        values = sorted(samples.get(metric, []))
        if not values:
            continue
        p90_index = math.ceil(0.9 * len(values)) - 1
        result[metric] = {
            "count": len(values),
            "median": round(statistics.median(values), 1),
            "p90": round(values[p90_index], 1),
            "max": round(values[-1], 1),
        }
    return result


def render(samples: dict[str, list[float]], output: TextIO = sys.stdout) -> None:
    rows = summarize(samples)
    print(f"{'metric':<30} {'count':>7} {'median':>10} {'p90':>10} {'max':>10}", file=output)
    for metric, values in rows.items():
        print(
            f"{metric:<30} {values['count']:>7} {values['median']:>10.1f} "
            f"{values['p90']:>10.1f} {values['max']:>10.1f}",
            file=output,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event",
        choices=("speaker_embed", "speaker_verify", "turn_backend", "turn_client"),
        help="only include one structured latency event",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    render(parse_lines(sys.stdin, event=args.event))


if __name__ == "__main__":
    main()
