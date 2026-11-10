"""CAS contract against real storage. External backends use optional test URLs."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
import pytest_asyncio

from ad_seller.storage.hybrid_backend import HybridBackend
from ad_seller.storage.sqlite_backend import SQLiteBackend


@pytest_asyncio.fixture(params=["sqlite", "redis", "postgres", "hybrid"])
async def atomic_backends(request, tmp_path):
    kind = request.param
    if kind in {"redis", "hybrid"} and not os.environ.get("TEST_REDIS_URL"):
        pytest.skip("TEST_REDIS_URL is not set")
    if kind in {"postgres", "hybrid"} and not os.environ.get("TEST_POSTGRES_URL"):
        pytest.skip("TEST_POSTGRES_URL is not set")

    def make():
        if kind == "sqlite":
            return SQLiteBackend(str(tmp_path / "cas.db"))
        if kind in {"redis", "hybrid"}:
            from ad_seller.storage.redis_backend import RedisBackend

            redis = RedisBackend(os.environ["TEST_REDIS_URL"])
        if kind in {"postgres", "hybrid"}:
            from ad_seller.storage.postgres_backend import PostgresBackend

            pg = PostgresBackend(os.environ["TEST_POSTGRES_URL"])
        return {
            "redis": lambda: redis,
            "postgres": lambda: pg,
            "hybrid": lambda: HybridBackend(pg, redis),
        }[kind]()

    backends = [make(), make()]
    for backend in backends:
        await backend.connect()
    try:
        yield backends
    finally:
        for backend in backends:
            await backend.disconnect()


@pytest.mark.asyncio
async def test_compare_and_set_has_one_winner_and_preserves_ttl(atomic_backends):
    first, second = atomic_backends
    key = f"approval:cas-test-{uuid4()}"
    # JSON object key order must not affect equality.
    expected = {"status": "approved", "nested": {"b": 2, "a": 1}}
    equivalent = {"nested": {"a": 1, "b": 2}, "status": "approved"}
    try:
        assert not await first.compare_and_set(key, expected, {})
        await first.set(key, expected, ttl=1)
        results = await asyncio.gather(
            first.compare_and_set(key, equivalent, {"status": "consumed", "token": "one"}),
            second.compare_and_set(key, expected, {"status": "consumed", "token": "two"}),
        )
        assert sorted(results) == [False, True]
        stored = await first.get(key)
        # Current PostgreSQL get() returns raw JSON with asyncpg's default codec.
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored["status"] == "consumed"
        assert not await second.compare_and_set(key, expected, {"status": "approved"})
        await asyncio.sleep(1.1)
        assert not await second.compare_and_set(key, stored, expected)
        assert await first.get(key) is None
        await first.set(key, {"status": "approved"})
        assert await first.compare_and_set(key, {"status": "approved"}, {"status": "consumed"})
        assert not await first.compare_and_set(key, expected, {})
    finally:
        await first.delete(key)


@pytest.mark.asyncio
async def test_postgres_compare_and_set_with_native_json_codec():
    if not os.environ.get("TEST_POSTGRES_URL"):
        pytest.skip("TEST_POSTGRES_URL is not set")
    from ad_seller.storage.postgres_backend import PostgresBackend

    backend = PostgresBackend(os.environ["TEST_POSTGRES_URL"], pool_min=1, pool_max=1)
    await backend.connect()
    key = f"approval:codec-test-{uuid4()}"
    try:
        async with backend._pool.acquire() as conn:
            await conn.set_type_codec(
                "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
            )
            # Seed directly: current set() assumes the default asyncpg codec.
            await conn.execute(
                "INSERT INTO kv_store (key, value) VALUES ($1, $2::text::jsonb)",
                key,
                json.dumps({"status": "approved"}),
            )
        assert await backend.compare_and_set(key, {"status": "approved"}, {"status": "consumed"})
        assert await backend.get(key) == {"status": "consumed"}
        assert not await backend.compare_and_set(key, {"status": "approved"}, {})
    finally:
        await backend.delete(key)
        await backend.disconnect()
