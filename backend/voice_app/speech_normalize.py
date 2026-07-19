from __future__ import annotations

import re


_SEMANTIC_SYMBOLS = str.maketrans(
    {
        "✅": " 已完成 ",
        "❌": " 失败 ",
        "⚠": " 注意 ",
        "→": " 接下来 ",
        "⇒": " 接下来 ",
        "➡": " 接下来 ",
    }
)
_VARIATION_AND_JOINERS = re.compile(r"[\u200d\ufe0e\ufe0f\u20e3]")
_DECORATIVE_BULLETS = re.compile(r"[•●◆◇▪▫■□★☆]+")
_REPEATED_SEPARATORS = re.compile(r"(?:[-–—]{2,}|…{2,}|\.{3,}|[:：]{2,})")
_REDUNDANT_PAUSE = re.compile(r"，(?=[。！？!?；;])")
_PICTOGRAPHS = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]"
)


def normalize_speech_symbols(text: str) -> str:
    text = text.translate(_SEMANTIC_SYMBOLS)
    text = _VARIATION_AND_JOINERS.sub("", text)
    text = _DECORATIVE_BULLETS.sub(" ", text)
    text = _REPEATED_SEPARATORS.sub("，", text)
    text = _REDUNDANT_PAUSE.sub("", text)
    return _PICTOGRAPHS.sub("", text)
