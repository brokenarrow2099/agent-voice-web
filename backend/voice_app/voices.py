from __future__ import annotations

from typing import Literal, cast, get_args


TTSVoice = Literal[
    "serena",
    "vivian",
    "uncle_fu",
    "dylan",
    "eric",
    "ryan",
    "aiden",
    "ono_anna",
    "sohee",
]

SUPPORTED_TTS_VOICES = frozenset(get_args(TTSVoice))
DEFAULT_TTS_VOICE: TTSVoice = "serena"


def validate_tts_voice(voice: str) -> TTSVoice:
    if voice not in SUPPORTED_TTS_VOICES:
        raise ValueError(f"unsupported TTS voice: {voice}")
    return cast(TTSVoice, voice)
