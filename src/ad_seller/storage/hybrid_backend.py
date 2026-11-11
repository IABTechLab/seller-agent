# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Hybrid storage backend — routes keys to Postgres or Redis by prefix.

Business data (products, deals, orders, proposals, negotiations, etc.) goes to
PostgreSQL for durability.  Sessions, caches, and ephemeral data go to Redis
for speed and automatic TTL eviction.
"""

from typing import Any, Optional

from ad_seller.storage.base import StorageBackend

# Key prefixes routed to Redis (sessions, caches, ephemeral)
_REDIS_PREFIXES = frozenset(
    {
        "session:",
        "session_index:",
        "cache:",
        "lock:",
        "pubsub:",
        "rate_limit:",
    }
)


def _is_redis_key(key: str) -> bool:
    """Return True if this key should be stored in Redis."""
    for prefix in _REDIS_PREFIXES:
        if key.startswith(prefix):
            return True
    return False


class HybridBackend(StorageBackend):
    """Routes storage operations to Postgres or Redis based on key prefix.

    - **Postgres**: products, proposals, deals, orders, negotiations, packages,
      quotes, change requests, agents, media kits — anything that must survive
      a Redis flush.
    - **Redis**: sessions, session indexes, caches, locks, pubsub, rate limits —
      ephemeral data that benefits from in-memory speed and native TTL.
    """

    def __init__(self, postgres: StorageBackend, redis: StorageBackend):
        self._pg = postgres
        self._redis = redis

    def _backend_for(self, key: str) -> StorageBackend:
        return self._redis if _is_redis_key(key) else self._pg

    async def connect(self) -> None:
        await self._pg.connect()
        await self._redis.connect()

    async def disconnect(self) -> None:
        await self._redis.disconnect()
        await self._pg.disconnect()

    async def get(self, key: str) -> Optional[Any]:
        return await self._backend_for(key).get(key)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        await self._backend_for(key).set(key, value, ttl=ttl)

    async def compare_and_set(self, key: str, expected: Any, value: Any) -> bool:
        return await self._backend_for(key).compare_and_set(key, expected, value)

    async def delete(self, key: str) -> bool:
        return await self._backend_for(key).delete(key)

    async def exists(self, key: str) -> bool:
        return await self._backend_for(key).exists(key)

    async def keys(self, pattern: str = "*") -> list[str]:
        """Query both backends and merge results."""
        pg_keys = await self._pg.keys(pattern)
        redis_keys = await self._redis.keys(pattern)
        return pg_keys + redis_keys
