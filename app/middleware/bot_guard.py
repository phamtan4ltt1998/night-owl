"""Bot guard middleware: block known scraper User-Agents and banned IPs.

Controlled by environment variables (see app/config.py):
  ANTI_SCRAPING_ENABLED, BOT_UA_ENABLED, HEADER_CHECK_ENABLED, HONEYPOT_ENABLED
"""
from __future__ import annotations

import ipaddress

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import BOT_UA_ENABLED, HEADER_CHECK_ENABLED, HONEYPOT_ENABLED


def _is_private(ip: str) -> bool:
    """Return True for loopback/private IPs — never ban own infra."""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_loopback or addr.is_private
    except ValueError:
        return False


# IPs that hit the honeypot endpoint are added here.
# In production, replace with Redis set for multi-process safety.
BANNED_IPS: set[str] = set()

BLOCKED_UA_SUBSTRINGS = [
    "python-requests",
    "scrapy",
    "httpx",
    "aiohttp",
    "go-http-client",
    "java/",
    "libwww-perl",
    "mechanize",
    "wget",
    "curl/",
    "pycurl",
    "node-fetch",
    "axios/",
    "okhttp",
    "headless",
    "phantomjs",
    "selenium",
    "playwright",
    "puppeteer",
]

# Paths that require browser-like headers
_STRICT_PATHS = ("/chapters",)


async def bot_guard_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else ""

    # Layer 1: banned IP (honeypot) — skip if honeypot disabled or private IP
    if HONEYPOT_ENABLED and client_ip in BANNED_IPS and not _is_private(client_ip):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)

    # Layer 2: known scraper UA
    if BOT_UA_ENABLED:
        ua = request.headers.get("user-agent", "").lower()
        if any(pattern in ua for pattern in BLOCKED_UA_SUBSTRINGS):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)

    # Layer 3: missing browser headers on content paths
    if HEADER_CHECK_ENABLED:
        path = request.url.path
        if any(path.startswith(p) for p in _STRICT_PATHS) or "/content" in path:
            if not request.headers.get("accept-language"):
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            if not request.headers.get("accept"):
                return JSONResponse({"detail": "Forbidden"}, status_code=403)

    return await call_next(request)
