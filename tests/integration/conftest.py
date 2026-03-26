"""Shared fixtures for integration tests.

Provides mocked storage, settings, and ad server/SSP registry objects
so integration tests can exercise real flow logic without external services.
"""

import json
import types
from pathlib import Path
from typing import Any, Optional

import pytest

from ad_seller.storage.base import StorageBackend


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file by name."""
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# In-memory storage backend (no SQLite file, no aiosqlite)
# ---------------------------------------------------------------------------


class InMemoryStorage(StorageBackend):
    """Fully in-memory storage backend for tests."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        self._data.clear()

    async def get(self, key: str) -> Optional[Any]:
        return self._data.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        return [k for k in self._data if fnmatch.fnmatch(k, pattern)]


@pytest.fixture
def in_memory_storage() -> InMemoryStorage:
    """Create a fresh in-memory storage instance."""
    return InMemoryStorage()


# ---------------------------------------------------------------------------
# Settings stub (plain namespace to avoid pydantic env validation)
# ---------------------------------------------------------------------------


def make_settings(**overrides) -> types.SimpleNamespace:
    """Create a settings stub with sensible defaults for integration tests."""
    defaults = {
        "seller_organization_name": "Integration Test Publisher",
        "seller_organization_id": "org-integ-001",
        "seller_agent_url": "http://localhost:8000",
        "seller_agent_name": "Test Seller Agent",
        "gam_network_code": None,
        "freewheel_sh_mcp_url": None,
        "freewheel_enabled": False,
        "freewheel_inventory_mode": "deals_only",
        "gam_enabled": False,
        "ssp_connectors": "",
        "ssp_routing_rules": "",
        "ad_server_type": "google_ad_manager",
        "default_currency": "USD",
        "default_price_floor_cpm": 5.0,
        "approval_gate_enabled": False,
        "approval_timeout_hours": 24,
        "approval_required_flows": "",
        "yield_optimization_enabled": True,
        "programmatic_floor_multiplier": 1.2,
        "preferred_deal_discount_max": 0.15,
        "agent_registry_enabled": False,
        "agent_registry_url": "",
        "pubmatic_mcp_url": "",
        "index_exchange_api_url": "",
        "magnite_api_url": "",
        "anthropic_api_key": "sk-test-dummy",
        "database_url": "sqlite:///:memory:",
        "redis_url": None,
        "storage_type": "sqlite",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


@pytest.fixture
def test_settings() -> types.SimpleNamespace:
    """Return default integration-test settings."""
    return make_settings()


# ---------------------------------------------------------------------------
# Fixture data helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def freewheel_products() -> list[dict]:
    """Load synthetic FreeWheel inventory products."""
    return load_fixture("freewheel_inventory.json")["products"]


@pytest.fixture
def pubmatic_deal_response() -> dict:
    """Load synthetic PubMatic deal creation response."""
    return load_fixture("pubmatic_deal_response.json")


@pytest.fixture
def ix_deal_response() -> dict:
    """Load synthetic Index Exchange deal creation response."""
    return load_fixture("index_exchange_deal_response.json")
