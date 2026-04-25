"""
redis_client.py — Redis caching layer.

Key conventions:
  tenant:{tenant_id}:query:{hash}   Query result cache (TTL = CACHE_TTL)
  session:{user_id}                 JWT session cache  (TTL = JWT_EXPIRY)

Cache invalidation: on any write to a tenant's data, call
  cache_invalidate_tenant(tenant_id) to flush all query keys for that tenant.
"""
import json
import logging
import threading
from typing import Any

import redis

from provisioning.config import Config

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None
_redis_lock = threading.Lock()


def get_redis() -> redis.Redis | None:
    """Return a lazily-initialised Redis client, or None if unavailable."""
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                try:
                    client = redis.Redis(
                        host     = Config.REDIS_HOST,
                        port     = Config.REDIS_PORT,
                        password = Config.REDIS_PASSWORD or None,
                        db       = Config.REDIS_DB,
                        decode_responses = True,
                        socket_connect_timeout = 3,
                        socket_timeout         = 3,
                        retry_on_timeout       = True,
                    )
                    client.ping()
                    _redis_client = client
                    logger.info("Redis connection established.")
                except Exception as exc:
                    logger.warning("Redis unavailable — caching disabled: %s", exc)
                    return None
    return _redis_client


def cache_get(key: str) -> Any | None:
    """Return deserialised cached value, or None on miss / Redis unavailable."""
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Cache GET failed for key=%s: %s", key, exc)
        return None


def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    """Serialise and cache value with an optional TTL (seconds)."""
    r = get_redis()
    if r is None:
        return
    try:
        serialised = json.dumps(value, default=str)
        if ttl:
            r.setex(key, ttl, serialised)
        else:
            r.set(key, serialised)
    except Exception as exc:
        logger.warning("Cache SET failed for key=%s: %s", key, exc)


def cache_delete(key: str) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        r.delete(key)
    except Exception as exc:
        logger.warning("Cache DELETE failed for key=%s: %s", key, exc)


def cache_invalidate_tenant(tenant_id: str) -> int:
    """
    Delete all query cache keys for a given tenant.
    Pattern: tenant:{tenant_id}:query:*

    Returns the number of keys deleted.
    """
    r = get_redis()
    if r is None:
        return 0
    pattern = f"tenant:{tenant_id}:query:*"
    try:
        keys = list(r.scan_iter(pattern, count=100))
        if keys:
            deleted = r.delete(*keys)
            logger.debug("Cache invalidated %d keys for tenant %s", deleted, tenant_id)
            return deleted
        return 0
    except Exception as exc:
        logger.warning("Cache invalidation failed for tenant %s: %s", tenant_id, exc)
        return 0
