from __future__ import annotations

import socket

import httpx
import pytest

from voice_app.web_tools import (
    UnsafeURL,
    fetch_public_page,
    search_web,
    validate_public_url,
)


async def test_search_returns_compact_bounded_results():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "json"
        assert request.url.params["q"] == "SGLang"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"Result {index}",
                        "url": f"https://example.com/{index}",
                        "content": "  useful   summary  ",
                        "engine": "bing",
                    }
                    for index in range(8)
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_web("SGLang", limit=3, client=client)

    assert len(results) == 3
    assert results[0] == {
        "title": "Result 0",
        "url": "https://example.com/0",
        "snippet": "useful summary",
        "engine": "bing",
    }


async def test_search_rejects_empty_query():
    with pytest.raises(ValueError, match="query"):
        await search_web("   ")


def test_validate_public_url_blocks_private_and_non_http_targets():
    def resolver(host, port, *, type):
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, type, 6, "", ("127.0.0.1", port))]

    with pytest.raises(UnsafeURL):
        validate_public_url("http://internal.example/admin", resolver=resolver)
    with pytest.raises(UnsafeURL):
        validate_public_url("file:///etc/passwd")


async def test_fetch_extracts_readable_text_and_bounds_output():
    def resolver(host, port, *, type):
        return [(socket.AF_INET, type, 6, "", ("93.184.216.34", port))]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><script>ignore()</script><h1>Title</h1><p>Hello   world.</p></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await fetch_public_page(
            "https://example.com/article",
            max_chars=18,
            client=client,
            resolver=resolver,
        )

    assert page["url"] == "https://example.com/article"
    assert page["text"] == "Title Hello world."


async def test_fetch_validates_redirect_targets():
    def resolver(host, port, *, type):
        address = "127.0.0.1" if host == "localhost" else "93.184.216.34"
        return [(socket.AF_INET, type, 6, "", (address, port))]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://localhost/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnsafeURL):
            await fetch_public_page(
                "https://example.com/start", client=client, resolver=resolver
            )
