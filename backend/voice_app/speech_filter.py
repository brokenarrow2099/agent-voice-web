from __future__ import annotations

import re

from voice_app.speech_normalize import normalize_speech_symbols


_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((?:[^()\s]|\([^)]*\))+\)")
_RAW_URL = re.compile(r"https?://[^\s\])}>，。！？；：]+", re.IGNORECASE)
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_TABLE_ROW = re.compile(r"^(?:[^|\n]*\|)+[^|\n]*$")
_LINE_PREFIX = re.compile(r"(?m)^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+)")
_MARKUP = re.compile(r"(?:\*\*|__|~~|`|\*|_)")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([，。！？；：,.!?;:])")
_REPEATED_PUNCTUATION = re.compile(r"([。！？!?；;，,])\1+")


class SpeakableStream:
    """Remove non-speakable Markdown while emitting ordered text segments.

    Fence parsing happens before segmentation and keeps incomplete backtick runs
    across Claude stream deltas, so code never leaks merely because a fence was
    split between JSON events.
    """

    def __init__(self, max_chars: int = 120) -> None:
        if max_chars < 8:
            raise ValueError("max_chars must be at least 8")
        self.max_chars = max_chars
        self._in_fence = False
        self._ticks = ""
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        for char in delta:
            if char == "`":
                self._ticks += char
                if len(self._ticks) == 3:
                    self._in_fence = not self._in_fence
                    self._ticks = ""
                continue

            if self._ticks:
                # One or two backticks are inline-code decoration. Their text is
                # speakable, but the marker itself is not.
                self._ticks = ""
            if not self._in_fence:
                self._buffer += char
        return self._drain(final=False)

    def flush(self) -> list[str]:
        # Incomplete fence markers are Markdown decoration, never prose.
        self._ticks = ""
        segments = self._drain(final=True)
        self._buffer = ""
        return segments

    def _drain(self, *, final: bool) -> list[str]:
        emitted: list[str] = []
        while self._buffer:
            boundary = self._find_boundary(final=final)
            if boundary is None:
                break
            raw = self._buffer[:boundary]
            self._buffer = self._buffer[boundary:]
            normalized = self._normalize(raw)
            if normalized:
                emitted.extend(self._split_normalized(normalized))
        return emitted

    def _find_boundary(self, *, final: bool) -> int | None:
        text = self._buffer
        for index, char in enumerate(text):
            end = index + 1
            if char == "\n":
                return end
            if char in "。！？；!?;":
                if not self._inside_url(index):
                    return end
            if char == "." and not self._inside_url(index):
                if end < len(text) and text[end].isspace():
                    return end

        if len(text) >= self.max_chars and not self._has_incomplete_url_prefix(text[: self.max_chars]):
            return self.max_chars
        if final:
            return len(text)
        return None

    def _inside_url(self, index: int) -> bool:
        prefix = self._buffer[: index + 1]
        token_start = max(prefix.rfind(" "), prefix.rfind("\n"), prefix.rfind("(")) + 1
        token = prefix[token_start:]
        return token.lower().startswith(("http://", "https://"))

    @staticmethod
    def _has_incomplete_url_prefix(text: str) -> bool:
        token = re.split(r"\s", text)[-1].lower()
        return token.startswith(("http://", "https://"))

    def _split_normalized(self, text: str) -> list[str]:
        return [text[start : start + self.max_chars] for start in range(0, len(text), self.max_chars)]

    @staticmethod
    def _normalize(raw: str) -> str:
        stripped = raw.strip()
        if _TABLE_SEPARATOR.fullmatch(stripped) or _TABLE_ROW.fullmatch(stripped):
            return ""
        text = _MARKDOWN_LINK.sub(r"\1", raw)
        text = _RAW_URL.sub("", text)
        text = _LINE_PREFIX.sub("", text)
        text = text.replace("|", " ")
        text = _MARKUP.sub("", text)
        text = normalize_speech_symbols(text)
        text = _REPEATED_PUNCTUATION.sub(r"\1", text)
        text = _SPACE.sub(" ", text)
        text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
        return text.strip()
