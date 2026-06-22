"""
LLM Cache — SHA256 key, TTL from env var, Redis with memory fallback.
Caches deterministic LLM responses (temperature=0) to reduce API costs.
"""

import hashlib
import json
import logging
import os
from typing import Optional, Any

logger = logging.getLogger(__name__)

# TTL from environment (default 1 hour)
LLM_CACHE_TTL = int(os.getenv("LLM_CACHE_TTL", "3600"))

# Fallback memory cache
_memory_cache: dict[str, dict] = {}
_memory_cache_order: list[str] = []
_MAX_MEMORY_CACHE_SIZE = 500


def _cache_key(model: str, prompt: str, system: Optional[str], temperature: float) -> str:
    """Generate a SHA256 cache key from the request parameters."""
    data = {"model": model, "prompt": prompt, "system": system, "temperature": temperature}
    serialized = json.dumps(data, sort_keys=True)
    return "llm_cache:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class LLMResponseCache:
    @classmethod
    async def get(
        cls,
        model: str,
        prompt: str,
        system: Optional[str],
        temperature: float,
    ) -> Optional[dict[str, Any]]:
        """Get a cached response. Returns None on miss or non-deterministic requests."""
        if temperature > 0:
            return None

        key = _cache_key(model, prompt, system, temperature)

        # Try Redis
        try:
            import redis.asyncio as redis_pkg
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            client = redis_pkg.from_url(redis_url)
            cached = await client.get(key)
            await client.close()
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.debug(f"Redis cache get failed: {e}")

        # Try memory fallback
        if key in _memory_cache:
            return _memory_cache[key]

        return None

    @classmethod
    async def set(
        cls,
        model: str,
        prompt: str,
        system: Optional[str],
        temperature: float,
        result: dict[str, Any],
        ttl: Optional[int] = None,
    ):
        """Cache a response. Only caches deterministic (temperature=0) requests."""
        if temperature > 0:
            return

        key = _cache_key(model, prompt, system, temperature)
        effective_ttl = ttl if ttl is not None else LLM_CACHE_TTL

        # Try Redis
        try:
            import redis.asyncio as redis_pkg
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            client = redis_pkg.from_url(redis_url)
            await client.setex(key, effective_ttl, json.dumps(result))
            await client.close()
            return
        except Exception as e:
            logger.debug(f"Redis cache set failed: {e}")

        # Memory fallback with LRU eviction
        if len(_memory_cache) >= _MAX_MEMORY_CACHE_SIZE:
            oldest = _memory_cache_order.pop(0)
            _memory_cache.pop(oldest, None)

        _memory_cache[key] = result
        _memory_cache_order.append(key)

    @classmethod
    async def invalidate(cls, model: str, prompt: str, system: Optional[str], temperature: float):
        """Remove a cached entry."""
        key = _cache_key(model, prompt, system, temperature)

        try:
            import redis.asyncio as redis_pkg
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            client = redis_pkg.from_url(redis_url)
            await client.delete(key)
            await client.close()
        except Exception:
            pass

        _memory_cache.pop(key, None)
