import logging
import os
from typing import Optional

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

_redis: Optional["aioredis.Redis"] = None  # type: ignore[name-defined]
logger = logging.getLogger("nightowl.redis")


async def init_redis() -> None:
    global _redis
    if not _REDIS_AVAILABLE:
        logger.warning("[redis] redis[asyncio] not installed — cache uses TTLCache only")
        return
    if os.getenv("REDIS_ENABLED", "true").lower() != "true":
        logger.info("[redis] Disabled via REDIS_ENABLED=false")
        return
    try:
        client = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _redis = client
        logger.info("[redis] Connected to %s", os.getenv("REDIS_URL", "redis://localhost:6379"))
    except Exception as exc:
        logger.warning("[redis] Unavailable — falling back to TTLCache only: %s", exc)


def get_redis() -> Optional["aioredis.Redis"]:  # type: ignore[name-defined]
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
        logger.info("[redis] Connection closed")
