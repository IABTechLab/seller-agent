# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests for GitHub issue #34 (field feedback).

Bug 1 — silent empty placements:
    POST /packages returned 200 with ``placements: []`` whenever the
    supplied ``product_ids`` came from ``GET /products``. The router
    resolved ids only via ``storage.get_product()``, but ``/products``
    serves the in-memory static catalog which is never persisted to
    storage. ``POST /packages/assemble`` had the same store mismatch
    (surfacing as a 400).

    Fix under test: catalog-first resolution (static catalog, then
    storage fallback), 422 when ZERO supplied ids resolve (naming the
    unresolved ids), and ``unresolved_ids``/``warnings`` on the response
    for partial resolution.

Bug 2 — non-idempotent sync:
    POST /packages/sync duplicated the entire SYNCED layer on every run
    (fresh ``pkg-{uuid8}`` ids, no existing-package check; 2 syncs took
    storage from 1 to 7 packages).

    Fix under test: deterministic synced-package IDs with upsert
    semantics plus pruning of stale SYNCED-layer packages, so repeated
    syncs converge to the same package set. Curated/operator packages
    are untouched.
"""

import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used in test_endpoint_no_flow_kickoff.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api import deps  # noqa: E402
from ad_seller.interfaces.api import main as api_main  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.storage.base import StorageBackend  # noqa: E402

# =============================================================================
# In-memory storage (no SQLite file, no aiosqlite)
# =============================================================================


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


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """Reset the static catalog cache between tests."""
    api_main._STATIC_PRODUCT_CATALOG = None
    yield
    api_main._STATIC_PRODUCT_CATALOG = None


@pytest.fixture
def client(storage):
    """ASGI client with storage + event emission patched hermetically."""
    with patch("ad_seller.storage.factory.get_storage", return_value=storage):
        with patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            yield httpx.AsyncClient(transport=transport, base_url="http://test")


def _catalog_product_ids(n: int = 2) -> list[str]:
    """Return the first n product ids from the static catalog (as served by GET /products)."""
    catalog = deps.get_product_catalog()
    ids = list(catalog["products"].keys())
    assert len(ids) >= n, "static catalog unexpectedly small"
    return ids[:n]


def _storage_product_dict(product_id: str = "prod-negotiated-1") -> dict:
    """A product as persisted by the negotiation flow (storage-only, not in catalog)."""
    return {
        "product_id": product_id,
        "name": "Negotiated Custom Product",
        "description": "Persisted via negotiation, absent from static catalog",
        "inventory_type": "video",
        "base_cpm": 22.0,
        "floor_cpm": 18.0,
    }


# =============================================================================
# Bug 1 — POST /packages: catalog-first resolution
# =============================================================================


class TestCreatePackageResolution:
    async def test_products_sourced_ids_attach_placements(self, client):
        """End-to-end reporter scenario: ids from GET /products must yield placements."""
        async with client as c:
            list_resp = await c.get("/products")
            assert list_resp.status_code == 200
            product_ids = [p["product_id"] for p in list_resp.json()["products"][:2]]

            resp = await c.post(
                "/packages",
                json={
                    "name": "Q3 Sports Bundle",
                    "product_ids": product_ids,
                    "base_price": 20.0,
                    "floor_price": 12.0,
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["placements"]) == 2, (
            f"placements silently empty for /products-sourced ids: {body['placements']}"
        )
        assert {pl["product_id"] for pl in body["placements"]} == set(product_ids)
        # Fully resolved: no partial-resolution warnings surface.
        assert "unresolved_ids" not in body

    async def test_zero_resolution_returns_422_naming_ids(self, client):
        """When no supplied product_ids resolve, fail loudly with the ids named."""
        async with client as c:
            resp = await c.post(
                "/packages",
                json={
                    "name": "Ghost Bundle",
                    "product_ids": ["prod-nope-1", "prod-nope-2"],
                    "base_price": 20.0,
                    "floor_price": 12.0,
                },
            )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["unresolved_ids"] == ["prod-nope-1", "prod-nope-2"]
        assert "prod-nope-1" in detail["message"]
        assert "prod-nope-2" in detail["message"]

    async def test_partial_resolution_returns_warning_and_unresolved_ids(self, client):
        good_id = _catalog_product_ids(1)[0]
        async with client as c:
            resp = await c.post(
                "/packages",
                json={
                    "name": "Partial Bundle",
                    "product_ids": [good_id, "prod-nope-9"],
                    "base_price": 20.0,
                    "floor_price": 12.0,
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["placements"]) == 1
        assert body["placements"][0]["product_id"] == good_id
        assert body["unresolved_ids"] == ["prod-nope-9"]
        assert body["warnings"], "partial resolution must surface a warning"
        assert "prod-nope-9" in " ".join(body["warnings"])

    async def test_storage_persisted_products_still_resolve(self, client, storage):
        """Fallback preserved: negotiation-persisted (storage-only) products resolve."""
        prod = _storage_product_dict()
        await storage.set_product(prod["product_id"], prod)
        async with client as c:
            resp = await c.post(
                "/packages",
                json={
                    "name": "Negotiated Bundle",
                    "product_ids": [prod["product_id"]],
                    "base_price": 20.0,
                    "floor_price": 12.0,
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["placements"]) == 1
        assert body["placements"][0]["product_id"] == prod["product_id"]

    async def test_empty_product_ids_still_creates_package(self, client):
        """Pin: a package with no product_ids is legal (no placements, no 422)."""
        async with client as c:
            resp = await c.post(
                "/packages",
                json={"name": "Shell Package", "base_price": 20.0, "floor_price": 12.0},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["placements"] == []


# =============================================================================
# Bug 1 — POST /packages/assemble: same store mismatch
# =============================================================================


class TestAssemblePackageResolution:
    async def test_products_sourced_ids_assemble_with_placements(self, client):
        async with client as c:
            list_resp = await c.get("/products")
            product_ids = [p["product_id"] for p in list_resp.json()["products"][:2]]

            resp = await c.post(
                "/packages/assemble",
                json={"name": "Dynamic Duo", "product_ids": product_ids},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {pl["product_id"] for pl in body["placements"]} == set(product_ids)

    async def test_zero_resolution_returns_422_naming_ids(self, client):
        async with client as c:
            resp = await c.post(
                "/packages/assemble",
                json={"name": "Ghost Dynamic", "product_ids": ["prod-nope-1"]},
            )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["unresolved_ids"] == ["prod-nope-1"]
        assert "prod-nope-1" in detail["message"]

    async def test_partial_resolution_reports_unresolved_ids(self, client):
        good_id = _catalog_product_ids(1)[0]
        async with client as c:
            resp = await c.post(
                "/packages/assemble",
                json={"name": "Partial Dynamic", "product_ids": [good_id, "prod-nope-9"]},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [pl["product_id"] for pl in body["placements"]] == [good_id]
        assert body["unresolved_ids"] == ["prod-nope-9"]
        assert body["warnings"]

    async def test_all_unpriced_products_still_400(self, client, storage):
        """Pin: resolvable but unpriceable products stay a 400 (honest pricing)."""
        prod = _storage_product_dict("prod-unpriced-1")
        prod["base_cpm"] = None
        prod["floor_cpm"] = None
        await storage.set_product(prod["product_id"], prod)
        async with client as c:
            resp = await c.post(
                "/packages/assemble",
                json={"name": "Unpriceable", "product_ids": ["prod-unpriced-1"]},
            )
        assert resp.status_code == 400, resp.text


# =============================================================================
# Bug 2 — sync idempotency (ProductSetupFlow SYNCED layer)
# =============================================================================


def _flow_settings(**overrides) -> SimpleNamespace:
    defaults = {
        "gam_network_code": None,
        "freewheel_sh_mcp_url": None,
        "ad_server_type": "none",
        "seller_organization_id": "test-seller-org",
        "seller_organization_name": "Test Seller",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def _run_sync_once(storage, settings) -> list[str]:
    """Run only the sync step of ProductSetupFlow against the given storage."""
    with patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings):
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            from ad_seller.flows.product_setup_flow import ProductSetupFlow

            flow = ProductSetupFlow()
            await flow.sync_from_ad_server()
            return list(flow.state.synced_segments)


async def _synced_layer_ids(storage) -> set[str]:
    return {p["package_id"] for p in await storage.list_packages() if p.get("layer") == "synced"}


class _FakeInventoryItem:
    def __init__(self, id: str, name: str, raw: Optional[dict] = None):
        self.id = id
        self.name = name
        self.raw = raw or {}


class _FakeAdServerClient:
    """Minimal stand-in for a CSV ad server client."""

    ad_server_type = SimpleNamespace(value="csv")

    def __init__(self, items):
        self._items = items

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_inventory(self, filter_str=None):
        return self._items


class TestSyncIdempotency:
    async def test_double_sync_mock_path_converges(self, storage):
        """Two syncs must produce the SAME package set, not a duplicated layer."""
        settings = _flow_settings()
        first_ids = await _run_sync_once(storage, settings)
        after_first = await _synced_layer_ids(storage)

        second_ids = await _run_sync_once(storage, settings)
        after_second = await _synced_layer_ids(storage)

        assert after_first == after_second, (
            f"sync is not idempotent: first={sorted(after_first)} second={sorted(after_second)}"
        )
        assert set(first_ids) == set(second_ids)
        assert len(await storage.list_packages()) == len(after_first)

    async def test_double_sync_csv_path_converges(self, storage):
        settings = _flow_settings(ad_server_type="csv")
        items = [
            _FakeInventoryItem("csv-001", "Homepage Display Banner"),
            _FakeInventoryItem("csv-002", "CTV Premium App"),
        ]

        def _fake_client():
            return _FakeAdServerClient(items)

        with patch("ad_seller.clients.ad_server_base.get_ad_server_client", _fake_client):
            first_ids = await _run_sync_once(storage, settings)
            after_first = await _synced_layer_ids(storage)

            second_ids = await _run_sync_once(storage, settings)
            after_second = await _synced_layer_ids(storage)

        assert after_first == after_second
        assert set(first_ids) == set(second_ids)
        # One package per inventory type (display, ctv).
        assert len(after_first) == 2

    async def test_sync_prunes_stale_synced_packages_only(self, storage):
        """Stale SYNCED-layer packages are replaced; curated packages untouched."""
        from ad_seller.models.media_kit import Package, PackageLayer, PackageStatus

        stale = Package(
            package_id="pkg-stale-synced",
            name="Old Synced Package",
            layer=PackageLayer.SYNCED,
            status=PackageStatus.ACTIVE,
        )
        curated = Package(
            package_id="pkg-operator-curated",
            name="Operator Curated Package",
            layer=PackageLayer.CURATED,
            status=PackageStatus.ACTIVE,
        )
        await storage.set_package(stale.package_id, stale.model_dump(mode="json"))
        await storage.set_package(curated.package_id, curated.model_dump(mode="json"))

        await _run_sync_once(storage, _flow_settings())

        remaining = {p["package_id"] for p in await storage.list_packages()}
        assert "pkg-stale-synced" not in remaining, (
            "stale SYNCED-layer package must be removed on re-sync"
        )
        assert "pkg-operator-curated" in remaining, "curated packages must never be pruned"

    async def test_resync_preserves_created_at_of_unchanged_packages(self, storage):
        """Re-seeded packages keep their identity (stable id + original created_at)."""
        settings = _flow_settings()
        await _run_sync_once(storage, settings)
        before = {p["package_id"]: p["created_at"] for p in await storage.list_packages()}

        await _run_sync_once(storage, settings)
        after = {p["package_id"]: p["created_at"] for p in await storage.list_packages()}

        assert before == after
