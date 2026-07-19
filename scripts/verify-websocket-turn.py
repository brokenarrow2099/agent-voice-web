#!/usr/bin/env python3
"""Run one real paired microphone-PCM -> ASR -> Claude -> TTS WebSocket turn."""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import subprocess
import time
import wave
from pathlib import Path

import httpx
from websockets.asyncio.client import connect


DEFAULT_WAV = Path("/home/agentvoice/comfy/ComfyUI/input/reference.wav")
DEFAULT_ENV = Path.home() / ".config/claude-voice/voice.env"
DEFAULT_CA = Path.home() / ".config/claude-voice/certs/ca.crt"


def read_env_value(path: Path, name: str) -> str:
    for raw in path.read_text().splitlines():
        key, separator, value = raw.partition("=")
        if separator and key == name:
            return value
    raise RuntimeError(f"{name} is missing from {path}")


def pcm16_mono_16k(source: Path) -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout


async def verify(host: str, source: Path, output: Path, timeout: float) -> dict[str, object]:
    token = read_env_value(DEFAULT_ENV, "VOICE_PAIRING_TOKEN")
    https_url = f"https://{host}:8443"
    pcm = pcm16_mono_16k(source)
    async with httpx.AsyncClient(verify=str(DEFAULT_CA), trust_env=False, timeout=30) as client:
        paired = await client.get(f"{https_url}/pair", params={"token": token})
        if paired.status_code != 303:
            raise RuntimeError(f"pairing failed with HTTP {paired.status_code}")
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        if not cookie:
            raise RuntimeError("pairing did not set a cookie")
        session = await client.get(f"{https_url}/api/session")
        session.raise_for_status()
        authorization = await client.post(
            f"{https_url}/api/speaker/verify",
            params={"generation_id": 1},
            content=pcm,
            headers={"content-type": "application/octet-stream"},
        )
        authorization.raise_for_status()
        authorization_payload = authorization.json()
        if not authorization_payload.get("accepted"):
            raise RuntimeError("speaker verification rejected the input audio")
        speaker_token = str(authorization_payload.get("speaker_token", ""))
        if not speaker_token:
            raise RuntimeError("speaker verification did not return a token")

    tls = ssl.create_default_context(cafile=str(DEFAULT_CA))
    started = time.monotonic()
    committed_at = 0.0
    first_audio_at: float | None = None
    audio = bytearray()
    controls: list[dict[str, object]] = []
    transcript = ""
    assistant = ""
    audio_rate = 24_000

    async with connect(
        f"wss://{host}:8443/ws/voice",
        ssl=tls,
        additional_headers={"Cookie": cookie},
        proxy=None,
        open_timeout=15,
        max_size=2**22,
    ) as websocket:
        generation = 1
        await websocket.send(
            json.dumps(
                {"type": "session.start", "client_id": "runtime-verifier", "generation_id": generation}
            )
        )
        while True:
            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            controls.append(event)
            if event.get("type") == "session.ready":
                break

        await websocket.send(
            json.dumps(
                {
                    "type": "audio.start",
                    "turn_id": 1,
                    "generation_id": generation,
                    "speaker_token": speaker_token,
                }
            )
        )
        for offset in range(0, len(pcm), 32_000):
            await websocket.send(pcm[offset : offset + 32_000])
        committed_at = time.monotonic()
        await websocket.send(
            json.dumps({"type": "audio.commit", "turn_id": 1, "generation_id": generation})
        )

        while True:
            message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            if isinstance(message, bytes):
                if first_audio_at is None:
                    first_audio_at = time.monotonic()
                audio.extend(message)
                continue
            event = json.loads(message)
            controls.append(event)
            event_type = event.get("type")
            if event_type == "transcript.final":
                transcript = str(event.get("text", ""))
            elif event_type == "assistant.final":
                assistant = str(event.get("text", ""))
            elif event_type == "audio.start":
                audio_rate = int(event.get("sample_rate", 24_000))
            elif event_type == "error":
                raise RuntimeError(f"voice turn error: {event.get('code')}: {event.get('message')}")
            elif event_type == "turn.end":
                break

        await websocket.send(json.dumps({"type": "session.end", "generation_id": generation + 1}))

    if not transcript:
        raise RuntimeError("ASR returned no transcript")
    if not assistant:
        raise RuntimeError("Claude returned no final answer")
    if not audio:
        raise RuntimeError("Qwen3-TTS returned no PCM")
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(audio_rate)
        target.writeframes(audio)
    event_types = [str(item.get("type")) for item in controls]
    return {
        "ok": True,
        "transcript": transcript,
        "assistant_chars": len(assistant),
        "assistant_preview": assistant[:160],
        "event_types": event_types,
        "audio_bytes": len(audio),
        "audio_rate": audio_rate,
        "audio_seconds": round(len(audio) / (audio_rate * 2), 3),
        "commit_to_first_audio_seconds": (
            round(first_audio_at - committed_at, 3) if first_audio_at is not None else None
        ),
        "total_seconds": round(time.monotonic() - started, 3),
        "output_wav": str(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.0.2.10")
    parser.add_argument("--input", type=Path, default=DEFAULT_WAV)
    parser.add_argument("--output", type=Path, default=Path("/tmp/claude-voice-e2e.wav"))
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.host, args.input, args.output, args.timeout)), ensure_ascii=False))


if __name__ == "__main__":
    main()
