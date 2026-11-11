# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""PostgreSQL storage backend implementation.

Uses a JSONB key-value table, drop-in compatible with SQLite and Redis backends.
Requires asyncpg. Install with: pip install ad_seller_system[postgres]
"""

import json
import time
from typing import Any, Optional

from ad_seller.storage.base import StorageBackend


class PostgresBackend(StorageBackend):
    """PostgreSQL-based storage backend.

    Uses a key-value store with JSONB values, matching the same interface
    as SQLiteBackend and RedisBackend. Suitable for production multi-instance
    deployments with durable storage.
    """

    def __init__(self, database_url: str, pool_min: int = 2, pool_max: int = 10):
        """Initialize PostgreSQL backend.

        Args:
            database_url: PostgreSQL connection string.
                          Accepts both ``postgresql://`` and ``postgresql+asyncpg://`` prefixes.
            pool_min: Minimum connection pool size.
            pool_max: Maximum connection pool size.
        """
        # Normalize URL — asyncpg expects ``postgresql://`` not ``postgresql+asyncpg://``
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool = None

    async def connect(self) -> None:
        """Create connection pool and ensure the KV table exists."""
        import asyncpg

        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._pool_min,
            max_size=self._pool_max,
        )

        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key   TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    expires_at DOUBLE PRECISION
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kv_expires
                ON kv_store (expires_at)
                WHERE expires_at IS NOT NULL
            """)

    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    def _ensure_pool(self) -> None:
        if self._pool is None:
            raise RuntimeError("Storage not connected. Call connect() first.")

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value by key, respecting TTL."""
        self._ensure_pool()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value, expires_at FROM kv_store WHERE key = $1",
                key,
            )
            if row is None:
                return None

            expires_at = row["expires_at"]
            if expires_at is not None and expires_at < time.time():
                await conn.execute("DELETE FROM kv_store WHERE key = $1", key)
                return None

            # asyncpg returns JSONB as native Python objects
            return row["value"]

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store a value with optional TTL (seconds)."""
        self._ensure_pool()
        expires_at = time.time() + ttl if ttl else None

        # asyncpg needs the value as a JSON string for JSONB parameter binding
        json_value = json.dumps(value)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO kv_store (key, value, expires_at)
                VALUES ($1, $2::jsonb, $3)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at
                """,
                key,
                json_value,
                expires_at,
            )

    async def compare_and_set(self, key: str, expected: Any, value: Any) -> bool:
        # Bind serialized values as text so custom JSONB codecs cannot encode them twice.
        self._ensure_pool()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE kv_store SET value = $3::text::jsonb WHERE key = $1 "
                "AND value = $2::text::jsonb AND (expires_at IS NULL OR expires_at > $4)",
                key,
                json.dumps(expected),
                json.dumps(value),
                time.time(),
            )
            return result == "UPDATE 1"

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""
        self._ensure_pool()
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM kv_store WHERE key = $1", key)
            # asyncpg returns 'DELETE N' where N is row count
            return result != "DELETE 0"

    async def exists(self, key: str) -> bool:
        """Check if a non-expired key exists."""
        self._ensure_pool()
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(
                """
                SELECT 1 FROM kv_store
                WHERE key = $1
                  AND (expires_at IS NULL OR expires_at > $2)
                """,
                key,
                time.time(),
            )
            return row is not None

    async def keys(self, pattern: str = "*") -> list[str]:
        """List keys matching a glob pattern.

        Translates ``*`` to ``%`` and ``?`` to ``_`` for SQL LIKE.
        """
        self._ensure_pool()
        sql_pattern = pattern.replace("*", "%").replace("?", "_")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT key FROM kv_store
                WHERE key LIKE $1
                  AND (expires_at IS NULL OR expires_at > $2)
                """,
                sql_pattern,
                time.time(),
            )
            return [row["key"] for row in rows]

    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of rows deleted."""
        self._ensure_pool()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM kv_store WHERE expires_at IS NOT NULL AND expires_at < $1",
                time.time(),
            )
            # result is 'DELETE N'
            return int(result.split()[-1])
