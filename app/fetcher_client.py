"""Async client for the Go nightowl-fetcher service.

Usage (flag-guarded in scraper.py):
    client = FetcherClient()
    async for story_url in client.fetch_listing("https://truyencom.com/.../"):
        ...
    async for event in client.fetch_story("https://truyencom.com/ten-truyen/"):
        if event["type"] == "story_meta":
            meta = event["data"]
        elif event["type"] == "chapter":
            chapter = event["data"]  # {url, title, number, slug, content_md}
        elif event["type"] == "done":
            break
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

FETCHER_HOST = os.getenv("FETCHER_HOST", "http://localhost:8080")
_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=10.0, pool=5.0)


class FetcherClient:
    def __init__(self, host: str = FETCHER_HOST) -> None:
        self._host = host.rstrip("/")

    async def fetch_listing(self, url: str) -> AsyncIterator[str]:
        """Yield story URLs discovered from a listing page (and its pagination)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{self._host}/fetch/listing",
                json={"url": url},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get("type") == "story_ref":
                        yield event["url"]

    async def fetch_story(self, url: str, render_js: bool = False) -> AsyncIterator[dict[str, Any]]:
        """Yield NDJSON events from the story fetcher.

        Event types:
            story_meta  — {"type": "story_meta", "data": {title, author, ...}}
            chapter     — {"type": "chapter",    "data": {number, title, content_md, ...}}
            done        — {"type": "done",        "count": N}
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{self._host}/fetch/story",
                json={"url": url, "render_js": render_js},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)

    async def health(self) -> bool:
        """Return True if the fetcher service is reachable."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                resp = await client.get(f"{self._host}/health")
                return resp.status_code == 200
        except Exception:
            return False
