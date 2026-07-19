#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from voice_app.web_tools import fetch_public_page, search_web


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local SearXNG search and safe page reader")
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("url")
    fetch.add_argument("--max-chars", type=int, default=12_000)
    return parser


async def run(args: argparse.Namespace) -> object:
    if args.command == "search":
        results = await search_web(
            args.query,
            limit=args.limit,
            searxng_url=os.environ.get("VOICE_SEARXNG_URL", "http://127.0.0.1:8081"),
        )
        return {"query": args.query, "results": results}
    return await fetch_public_page(args.url, max_chars=args.max_chars)


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
