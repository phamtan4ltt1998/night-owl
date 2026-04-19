"""Anti-scraping configuration — all values read from environment variables.

Environment variables (with defaults):

  ANTI_SCRAPING_ENABLED        true   — master switch; false disables ALL layers

  # Layer 1: IP ban (honeypot)
  HONEYPOT_ENABLED             true

  # Layer 2: User-Agent blocking
  BOT_UA_ENABLED               true

  # Layer 3: missing-header detection on content paths
  HEADER_CHECK_ENABLED         true

  # Layer 4: session token for chapter content
  SESSION_TOKEN_ENABLED        true
  SESSION_TOKEN_TTL            600    — seconds before token expires

  # Layer 5: rate limiting (slowapi)
  RATE_LIMIT_ENABLED           true
  RATE_LIMIT_BOOKS             60/minute
  RATE_LIMIT_CHAPTERS          30/minute
  RATE_LIMIT_CONTENT           20/minute
"""
from __future__ import annotations

import os


def _bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _str(key: str, default: str) -> str:
    return os.getenv(key, default).strip() or default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, ""))
    except (ValueError, TypeError):
        return default


# ── Master switch ──────────────────────────────────────────────────────────────
ANTI_SCRAPING_ENABLED: bool = _bool("ANTI_SCRAPING_ENABLED", True)

# ── Per-layer switches ─────────────────────────────────────────────────────────
HONEYPOT_ENABLED: bool      = ANTI_SCRAPING_ENABLED and _bool("HONEYPOT_ENABLED",      True)
BOT_UA_ENABLED: bool        = ANTI_SCRAPING_ENABLED and _bool("BOT_UA_ENABLED",        True)
HEADER_CHECK_ENABLED: bool  = ANTI_SCRAPING_ENABLED and _bool("HEADER_CHECK_ENABLED",  True)
SESSION_TOKEN_ENABLED: bool = ANTI_SCRAPING_ENABLED and _bool("SESSION_TOKEN_ENABLED", True)
RATE_LIMIT_ENABLED: bool    = ANTI_SCRAPING_ENABLED and _bool("RATE_LIMIT_ENABLED",    True)

# ── Session token ──────────────────────────────────────────────────────────────
SESSION_TOKEN_TTL: int = _int("SESSION_TOKEN_TTL", 600)

# ── Rate limit strings (slowapi format: "N/period") ───────────────────────────
_UNLIMITED = "10000/minute"  # effective no-op when rate limiting disabled

RATE_LIMIT_BOOKS: str    = _str("RATE_LIMIT_BOOKS",    "60/minute")    if RATE_LIMIT_ENABLED else _UNLIMITED
RATE_LIMIT_CHAPTERS: str = _str("RATE_LIMIT_CHAPTERS", "30/minute")    if RATE_LIMIT_ENABLED else _UNLIMITED
RATE_LIMIT_CONTENT: str  = _str("RATE_LIMIT_CONTENT",  "20/minute")    if RATE_LIMIT_ENABLED else _UNLIMITED
