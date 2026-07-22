# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CSV-mode catalog coherence tests.

Defect: in CSV mode (``AD_SERVER_TYPE=csv``) the catalog surface
(``GET /products``, ``GET /products/{id}``, avails, package resolution —
everything reading ``deps.get_product_catalog``) served the static
13-product default catalog while the actual CSV inventory
(``CSV_DATA_DIR``) only ever surfaced as SYNCED packages via
``POST /packages/sync``. ``/products`` lied about what the seller has.

Fix under test: ``catalog_service`` builds the catalog from the CSV
inventory when CSV mode is active.

Invariants pinned here:

- CSV product IDs come verbatim from the CSV ``id`` column, so they are
  stable across calls within a process (single-cache design, issue #34)
  AND deterministic across process restarts.
- Non-CSV modes are byte-identical to the previous behavior: the default
  catalog built from ``DEFAULT_PRODUCT_CONFIGS``.
- Packages created from CSV catalog product ids attach placements
  (issue #34 catalog-first resolution, now over the CSV catalog).
"""

import sys
from types import ModuleType
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used in test_issue34_catalog_fixes.py.
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

from ad_seller.config import get_settings  # noqa: E402
from ad_seller.interfaces.api import main as api_main  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.services import catalog_service  # noqa: E402
from ad_seller.storage.base import StorageBackend  # noqa: E402

# =============================================================================
# Test CSV inventory
# =============================================================================

CSV_HEADER = (
    "id,name,parent_id,status,sizes,ad_formats,device_types,inventory_type,"
    "content_categories,floor_price_cpm,currency,geo_targets,description"
)
CSV_ROWS = [
    # name-classified as "video" ("video" in name)
    "inv-t-video-preroll,Test Video Preroll,,ACTIVE,1920x1080,video,2|4,video,"
    "IAB1,24.00,USD,US,Test preroll slot",
    # name-classified as "display" (fallback)
    "inv-t-display-hp,Test Homepage Takeover,,ACTIVE,728x90,banner,2,display,"
    "IAB1,12.50,USD,US,Test homepage display",
    # name-classified as "ctv" ("ctv" in name)
    "inv-t-ctv-drama,Test CTV Drama,,ACTIVE,1920x1080,video,3|7,ctv,"
    "IAB1,30.00,USD,US,Test ctv slot",
]
CSV_IDS = {"inv-t-video-preroll", "inv-t-display-hp", "inv-t-ctv-drama"}


def _write_inventory(tmp_path):
    (tmp_path / "inventory.csv").write_text(CSV_HEADER + "\n" + "\n".join(CSV_ROWS) + "\n")


def _reset_caches():
    catalog_service.reset_catalog_cache()
    api_main._STATIC_PRODUCT_CATALOG = None
    get_settings.cache_clear()


@pytest.fixture
def csv_mode(tmp_path, monkeypatch):
    """Activate CSV mode against a temp inventory dir, resetting all caches."""
    _write_inventory(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AD_SERVER_TYPE", "csv")
    monkeypatch.setenv("CSV_DATA_DIR", str(tmp_path))
    _reset_caches()
    yield tmp_path
    _reset_caches()


@pytest.fixture
def default_mode(monkeypatch):
    """Explicit non-CSV mode, pinned via env so a developer .env with
    AD_SERVER_TYPE=csv cannot leak into the byte-identical pin."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AD_SERVER_TYPE", "google_ad_manager")
    monkeypatch.delenv("CSV_DATA_DIR", raising=False)
    _reset_caches()
    yield
    _reset_caches()


# =============================================================================
# In-memory storage (no SQLite file) — same shape as test_issue34
# =============================================================================


class InMemoryStorage(StorageBackend):
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
        return self._data.pop(key, None) is not None

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        return [k for k in self._data if fnmatch.fnmatch(k, pattern)]


@pytest.fixture
def client():
    """ASGI client with storage + event emission patched hermetically."""
    storage = InMemoryStorage()
    with patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)):
        with patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            yield httpx.AsyncClient(transport=transport, base_url="http://test")


def _wire_product_ids(body: dict) -> set[str]:
    """Extract product ids from the shared ProductListResponse wire shape."""
    return {p.get("product_id") or p.get("productid") for p in body["products"]}


# =============================================================================
# Catalog service — CSV mode
# =============================================================================


class TestCsvCatalogService:
    def test_catalog_serves_csv_inventory_in_csv_mode(self, csv_mode):
        catalog = catalog_service.get_static_product_catalog()
        assert set(catalog["products"].keys()) == CSV_IDS, (
            "CSV mode must serve the CSV inventory as the product catalog, "
            f"got: {sorted(catalog['products'].keys())}"
        )

    def test_csv_products_carry_row_data(self, csv_mode):
        catalog = catalog_service.get_static_product_catalog()
        product = catalog["products"]["inv-t-ctv-drama"]
        assert product.name == "Test CTV Drama"
        assert product.base_cpm == 30.0
        assert product.floor_cpm == round(30.0 * 0.85, 2)
        assert product.inventory_type == "ctv"
        assert "ctv" in catalog["inventory_types"]

    def test_csv_catalog_ids_stable_within_process(self, csv_mode):
        first = catalog_service.get_static_product_catalog()
        second = catalog_service.get_static_product_catalog()
        assert first is second, "single-cache design: catalog built once per process"
        assert list(first["products"].keys()) == list(second["products"].keys())

    def test_csv_catalog_ids_deterministic_across_rebuild(self, csv_mode):
        """Simulated reboot: fresh cache over the same CSV yields the same ids."""
        first_ids = set(catalog_service.get_static_product_catalog()["products"])
        catalog_service.reset_catalog_cache()
        api_main._STATIC_PRODUCT_CATALOG = None
        second_ids = set(catalog_service.get_static_product_catalog()["products"])
        assert first_ids == second_ids == CSV_IDS


# =============================================================================
# Non-CSV modes pinned byte-identical
# =============================================================================


class TestNonCsvModesUnchanged:
    def test_default_mode_serves_default_catalog(self, default_mode):
        catalog = catalog_service.get_static_product_catalog()
        expected_names = [c["name"] for c in catalog_service.DEFAULT_PRODUCT_CONFIGS]
        assert [p.name for p in catalog["products"].values()] == expected_names
        assert len(catalog["products"]) == 13

    def test_default_mode_ids_stay_uuid_shaped_and_stable(self, default_mode):
        import re

        first = catalog_service.get_static_product_catalog()
        for pid in first["products"]:
            assert re.fullmatch(r"prod-[0-9a-f]{8}", pid), pid
        second = catalog_service.get_static_product_catalog()
        assert list(first["products"].keys()) == list(second["products"].keys())


# =============================================================================
# API surface — CSV mode
# =============================================================================


class TestCsvCatalogApi:
    @pytest.mark.asyncio
    async def test_get_products_reflects_csv_inventory(self, csv_mode, client):
        async with client:
            resp = await client.get("/products")
            assert resp.status_code == 200
            assert _wire_product_ids(resp.json()) == CSV_IDS

    @pytest.mark.asyncio
    async def test_get_product_by_csv_id(self, csv_mode, client):
        async with client:
            resp = await client.get("/products/inv-t-display-hp")
            assert resp.status_code == 200
            body = resp.json()
            assert (body.get("product_id") or body.get("productid")) == "inv-t-display-hp"

    @pytest.mark.asyncio
    async def test_avails_on_csv_product(self, csv_mode, client):
        async with client:
            resp = await client.post(
                "/products/avails",
                json={
                    "productid": "inv-t-video-preroll",
                    "startdate": "2026-08-01T00:00:00Z",
                    "enddate": "2026-08-31T00:00:00Z",
                    "requestedImpressions": 100000,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["productid"] == "inv-t-video-preroll"
            assert body["estimatedCpm"] == 24.0

    @pytest.mark.asyncio
    async def test_package_from_csv_catalog_ids_attaches_placements(self, csv_mode, client):
        """Issue #34 regression over the CSV catalog: /products-sourced ids
        must resolve to placements on POST /packages."""
        async with client:
            resp = await client.get("/products")
            assert resp.status_code == 200
            product_ids = sorted(_wire_product_ids(resp.json()))[:2]

            create = await client.post(
                "/packages",
                json={
                    "name": "CSV Coherence Pack",
                    "product_ids": product_ids,
                    "base_price": 20.0,
                    "floor_price": 12.0,
                },
            )
            assert create.status_code == 200, create.text
            body = create.json()
            assert {pl["product_id"] for pl in body["placements"]} == set(product_ids), (
                f"placements not attached for CSV catalog ids: {body['placements']}"
            )
