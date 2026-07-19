from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


DEFAULT_SEARXNG_URL = "http://127.0.0.1:8081"
DEFAULT_TIMEOUT_SECONDS = 12.0
MAX_REDIRECTS = 3
Resolver = Callable[..., list[tuple[Any, ...]]]


class UnsafeURL(ValueError):
    pass


def _single_line(value: object, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:max_chars]


async def search_web(
    query: str,
    *,
    limit: int = 5,
    searxng_url: str = DEFAULT_SEARXNG_URL,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, str]]:
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    limit = max(1, min(limit, 10))
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(DEFAULT_TIMEOUT_SECONDS, connect=2.0),
        trust_env=False,
    )
    try:
        response = await active_client.get(
            f"{searxng_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
        )
        response.raise_for_status()
        payload = response.json()
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
        results: list[dict[str, str]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = _single_line(item.get("title"), 240)
            url = _single_line(item.get("url"), 2048)
            if not title or not url:
                continue
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": _single_line(item.get("content"), 500),
                    "engine": _single_line(item.get("engine"), 40),
                }
            )
            if len(results) >= limit:
                break
        return results
    finally:
        if owns_client:
            await active_client.aclose()


def validate_public_url(url: str, *, resolver: Resolver = socket.getaddrinfo) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeURL("only public HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeURL("URLs with credentials are not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = resolver(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL("hostname could not be resolved") from exc
    if not addresses:
        raise UnsafeURL("hostname could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeURL("private, local, and reserved targets are blocked")
    return url


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data)


def _readable_text(body: bytes, content_type: str, max_chars: int) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1).strip('"\'')
    text = body.decode(charset, errors="replace")
    if "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = _ReadableHTML()
        parser.feed(text)
        text = " ".join(parser.parts)
    return _single_line(text, max_chars)


async def fetch_public_page(
    url: str,
    *,
    max_chars: int = 12_000,
    max_bytes: int = 1_000_000,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver = socket.getaddrinfo,
) -> dict[str, str]:
    max_chars = max(1, min(max_chars, 50_000))
    max_bytes = max(1, min(max_bytes, 2_000_000))
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(DEFAULT_TIMEOUT_SECONDS, connect=3.0),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "AgentVoiceLocalFetch/1.0"},
    )
    current_url = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            validate_public_url(current_url, resolver=resolver)
            async with active_client.stream(
                "GET", current_url, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "text/plain")
                if not any(kind in content_type.lower() for kind in ("text/", "json", "xml")):
                    raise ValueError(f"unsupported content type: {content_type}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        remaining = len(chunk) - (size - max_bytes)
                        chunks.append(chunk[:remaining])
                        break
                    chunks.append(chunk)
                return {
                    "url": str(response.url),
                    "text": _readable_text(b"".join(chunks), content_type, max_chars),
                }
        raise UnsafeURL("too many redirects")
    finally:
        if owns_client:
            await active_client.aclose()
