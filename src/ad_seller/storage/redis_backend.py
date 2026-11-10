# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Redis storage backend implementation."""

import json
from typing import Any, Optional

from ad_seller.storage.base import StorageBackend

try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class RedisBackend(StorageBackend):
    """Redis-based storage backend.

    Suitable for production deployments requiring:
    - High availability
    - Distributed access
    - Pub/sub capabilities
    - Advanced caching with TTL

    Requires redis package: pip install redis
    """

    def __init__(self, redis_url: str, key_prefix: str = "ad_seller:"):
        """Initialize Redis backend.

        Args:
            redis_url: Redis connection URL (e.g., redis://localhost:6379/0)
            key_prefix: Prefix for all keys to namespace the data
        """
        if not REDIS_AVAILABLE:
            raise ImportError("Redis package not installed. Install with: pip install redis")

        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self._client: Optional[redis.Redis] = None

    def _prefixed_key(self, key: str) -> str:
        """Add prefix to key for namespacing."""
        return f"{self.key_prefix}{key}"

    def _unprefixed_key(self, key: str) -> str:
        """Remove prefix from key."""
        if key.startswith(self.key_prefix):
            return key[len(self.key_prefix) :]
        return key

    async def connect(self) -> None:
        """Establish connection to Redis."""
        self._client = redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        # Test connection
        await self._client.ping()

    async def disconnect(self) -> None:
        """Close the Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value by key."""
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        value = await self._client.get(self._prefixed_key(key))
        if value is None:
            return None

        return json.loads(value)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store a value with optional TTL (seconds)."""
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        json_value = json.dumps(value)
        prefixed = self._prefixed_key(key)

        if ttl:
            await self._client.setex(prefixed, ttl, json_value)
        else:
            await self._client.set(prefixed, json_value)

    async def compare_and_set(self, key: str, expected: Any, value: Any) -> bool:
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")
        from redis.exceptions import WatchError

        prefixed = self._prefixed_key(key)
        async with self._client.pipeline() as pipe:
            try:
                await pipe.watch(prefixed)
                current = await pipe.get(prefixed)
                if current is None or json.loads(current) != expected:
                    return False
                pipe.multi()
                pipe.set(prefixed, json.dumps(value), keepttl=True)
                await pipe.execute()
                return True
            except WatchError:
                return False

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        result = await self._client.delete(self._prefixed_key(key))
        return result > 0

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        result = await self._client.exists(self._prefixed_key(key))
        return result > 0

    async def keys(self, pattern: str = "*") -> list[str]:
        """List keys matching pattern."""
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        prefixed_pattern = self._prefixed_key(pattern)
        keys = await self._client.keys(prefixed_pattern)
        return [self._unprefixed_key(k) for k in keys]

    # Redis-specific methods

    async def publish(self, channel: str, message: Any) -> int:
        """Publish a message to a channel (pub/sub).

        Returns the number of subscribers that received the message.
        """
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        json_message = json.dumps(message)
        return await self._client.publish(f"{self.key_prefix}channel:{channel}", json_message)

    async def incr(self, key: str) -> int:
        """Increment a counter. Returns new value."""
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        return await self._client.incr(self._prefixed_key(key))

    async def get_stats(self) -> dict:
        """Get Redis server statistics."""
        if not self._client:
            raise RuntimeError("Storage not connected. Call connect() first.")

        info = await self._client.info()
        return {
            "connected_clients": info.get("connected_clients"),
            "used_memory_human": info.get("used_memory_human"),
            "total_commands_processed": info.get("total_commands_processed"),
            "keyspace_hits": info.get("keyspace_hits"),
            "keyspace_misses": info.get("keyspace_misses"),
        }
