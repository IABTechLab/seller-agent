# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite storage backend implementation."""

import json
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ad_seller.storage.base import StorageBackend


class SQLiteBackend(StorageBackend):
    """SQLite-based storage backend.

    Uses a simple key-value store pattern with JSON serialization.
    Suitable for development and single-instance deployments.
    """

    def __init__(self, database_url: str):
        """Initialize SQLite backend.

        Args:
            database_url: SQLite connection string (e.g., sqlite:///./ad_seller.db)
        """
        # Extract path from URL
        if database_url.startswith("sqlite:///"):
            self.db_path = database_url[len("sqlite:///") :]
        else:
            self.db_path = database_url

        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Establish connection and create tables."""
        # Ensure directory exists
        db_dir = Path(self.db_path).parent
        if db_dir and not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)

        # Create key-value table
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL
            )
        """)

        # Create index for expiration cleanup
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_expires_at ON kv_store(expires_at)
        """)

        await self._connection.commit()

    async def disconnect(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _cleanup_expired(self) -> None:
        """Remove expired entries."""
        if self._connection:
            current_time = time.time()
            await self._connection.execute(
                "DELETE FROM kv_store WHERE expires_at IS NOT NULL AND expires_at < ?",
                (current_time,),
            )
            await self._connection.commit()

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value by key."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        # Cleanup expired entries periodically
        await self._cleanup_expired()

        async with self._connection.execute(
            "SELECT value, expires_at FROM kv_store WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()

            if row is None:
                return None

            value, expires_at = row

            # Check if expired
            if expires_at is not None and expires_at < time.time():
                await self.delete(key)
                return None

            return json.loads(value)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store a value with optional TTL (seconds)."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        expires_at = time.time() + ttl if ttl else None
        json_value = json.dumps(value)

        await self._connection.execute(
            """
            INSERT OR REPLACE INTO kv_store (key, value, expires_at)
            VALUES (?, ?, ?)
            """,
            (key, json_value, expires_at),
        )
        await self._connection.commit()

    async def compare_and_set(self, key: str, expected: Any, value: Any) -> bool:
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")
        async with self._connection.execute(
            "SELECT value FROM kv_store WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or json.loads(row[0]) != expected:
            return False
        # Compare the original serialization in the UPDATE, so another connection
        # cannot change the record between our read and write.
        async with self._connection.execute(
            "UPDATE kv_store SET value = ? WHERE key = ? AND value = ? "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (json.dumps(value), key, row[0], time.time()),
        ) as cursor:
            await self._connection.commit()
            return cursor.rowcount == 1

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        async with self._connection.execute("DELETE FROM kv_store WHERE key = ?", (key,)) as cursor:
            await self._connection.commit()
            return cursor.rowcount > 0

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        # Cleanup expired entries
        await self._cleanup_expired()

        async with self._connection.execute(
            "SELECT 1 FROM kv_store WHERE key = ? AND (expires_at IS NULL OR expires_at > ?)",
            (key, time.time()),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None

    async def keys(self, pattern: str = "*") -> list[str]:
        """List keys matching pattern.

        Supports basic wildcards:
        - * matches any characters
        - ? matches single character
        """
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        # Cleanup expired entries
        await self._cleanup_expired()

        # Convert glob pattern to SQL LIKE pattern
        sql_pattern = pattern.replace("*", "%").replace("?", "_")

        async with self._connection.execute(
            "SELECT key FROM kv_store WHERE key LIKE ? AND (expires_at IS NULL OR expires_at > ?)",
            (sql_pattern, time.time()),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
