# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests for AI-14 (field feedback).

The inventory-type override API (override_inventory_type /
get_inventory_type_override) has always round-tripped correctly through
storage, but nothing on the read side ever consulted it: GET /products
and GET /products/{id} serve exclusively from the cached static
catalog, so an applied override was invisible on every read path.

Fix under test: catalog_service.apply_inventory_type_override() applies a
stored override on read, called by both GET /products (via the
batch-efficient apply_inventory_type_overrides_batch, one storage probe
instead of one read per product) and GET /products/{id}, so the list and
the single-product read never disagree.

Only ``inventory_type`` is swapped. A maintainer review of an earlier
version of this fix caught that recomputing ``supported_deal_types`` via
``infer_deal_types(new_type)`` is wrong for this catalog's hand-curated
products: that mapping is the canonical default for products BUILT from
an ad-server/CSV item, a different set of products with independently
declared values -- e.g. "Premium Display - Homepage" declares
``[PROGRAMMATIC_GUARANTEED, PREFERRED_DEAL]``, while
``infer_deal_types("display")`` (or "ctv", the type used below) returns a
different set entirely. Recomputing would silently grant a deal type the
seller never offered and withdraw one they did, so every other declared
field (deal types, pricing, targeting) is now left exactly as the catalog
declares it.

Scope: this only covers GET /products and GET /products/{id}. Extending
it to MCP's list_products tool, avails, quotes, and
create_deal_from_template needs the catalog accessor to be the one place
every consumer calls through, which lands separately alongside AI-6.
"""

from typing import Any, Optional
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.storage.base import StorageBackend


class InMemoryStorage(StorageBackend):
    """Fully in-memory storage backend for tests (mirrors test_issue34_catalog_fixes.py)."""

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
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def client(storage):
    from ad_seller.interfaces.api.main import app

    with patch("ad_seller.storage.factory.get_storage", return_value=storage):
        transport = ASGITransport(app=app)
        yield httpx.AsyncClient(transport=transport, base_url="http://test")


def _first_product_id() -> str:
    from ad_seller.interfaces.api import deps

    catalog = deps.get_product_catalog()
    return next(iter(catalog["products"]))


class TestApplyInventoryTypeOverrideHelper:
    """catalog_service.apply_inventory_type_override() directly."""

    async def test_no_override_returns_the_same_product_unchanged(self, storage):
        from ad_seller.interfaces.api import deps
        from ad_seller.services import catalog_service

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            catalog = deps.get_product_catalog()
            product = next(iter(catalog["products"].values()))
            original_type = product.inventory_type

            result = await catalog_service.apply_inventory_type_override(product)

        assert result.inventory_type == original_type
        assert result.supported_deal_types == product.supported_deal_types

    async def test_override_swaps_type_only_declared_fields_survive(self, storage):
        """Deal types, pricing, and every other declared field must survive
        an override unchanged -- only inventory_type may differ. Uses the
        catalog's actual first product ("Premium Display - Homepage",
        declaring [PROGRAMMATIC_GUARANTEED, PREFERRED_DEAL]) overridden to
        "ctv", whose infer_deal_types() output ([PROGRAMMATIC_GUARANTEED]
        only) differs from the declared set -- so this fails if deal types
        are ever recomputed again instead of preserved."""
        from ad_seller.interfaces.api import deps
        from ad_seller.services import catalog_service

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            catalog = deps.get_product_catalog()
            product = next(iter(catalog["products"].values()))
            original_deal_types = product.supported_deal_types
            original_base_cpm = product.base_cpm
            original_floor_cpm = product.floor_cpm
            assert original_deal_types != catalog_service.infer_deal_types("ctv"), (
                "test product must declare deal types that differ from infer_deal_types "
                "output for this assertion to be meaningful"
            )

            await catalog_service.override_inventory_type(
                product_id=product.product_id, inventory_type="ctv", reason="test"
            )
            result = await catalog_service.apply_inventory_type_override(product)

        assert result.inventory_type == "ctv"
        assert result.supported_deal_types == original_deal_types
        assert result.base_cpm == original_base_cpm
        assert result.floor_cpm == original_floor_cpm
        # The cached catalog product itself must never be mutated in place.
        assert product.inventory_type != "ctv"

    async def test_no_override_returns_the_exact_same_dict_no_wasted_reads(self, storage):
        """apply_inventory_type_overrides_batch must short-circuit to the
        SAME dict (identity, not just equality) when nothing has ever been
        overridden -- one keys() probe, zero per-product storage reads."""
        from ad_seller.interfaces.api import deps
        from ad_seller.services import catalog_service

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            catalog = deps.get_product_catalog()
            result = await catalog_service.apply_inventory_type_overrides_batch(catalog["products"])

        assert result is catalog["products"]

    async def test_batch_applies_only_to_overridden_products(self, storage):
        from ad_seller.interfaces.api import deps
        from ad_seller.services import catalog_service

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            catalog = deps.get_product_catalog()
            product_ids = list(catalog["products"].keys())
            overridden_id, untouched_id = product_ids[0], product_ids[1]
            untouched_original_type = catalog["products"][untouched_id].inventory_type

            await catalog_service.override_inventory_type(
                product_id=overridden_id, inventory_type="ctv", reason="test"
            )
            result = await catalog_service.apply_inventory_type_overrides_batch(catalog["products"])

        assert result[overridden_id].inventory_type == "ctv"
        assert result[untouched_id].inventory_type == untouched_original_type
        assert result[untouched_id] is catalog["products"][untouched_id]


class TestGetProductAppliesOverride:
    async def test_get_product_reflects_the_override(self, client, storage):
        product_id = _first_product_id()

        async with client as c:
            with patch("ad_seller.storage.factory.get_storage", return_value=storage):
                from ad_seller.services import catalog_service

                await catalog_service.override_inventory_type(
                    product_id=product_id, inventory_type="ctv", reason="test"
                )
            resp = await c.get(f"/products/{product_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ext"]["inventory_type"] == "ctv"
        assert body["ad_formats"] == ["ctv"]


class TestListProductsAppliesOverride:
    async def test_list_products_reflects_the_override_for_the_overridden_product_only(
        self, client, storage
    ):
        from ad_seller.interfaces.api import deps

        catalog = deps.get_product_catalog()
        product_ids = list(catalog["products"].keys())
        assert len(product_ids) >= 2, "static catalog unexpectedly small"
        overridden_id, untouched_id = product_ids[0], product_ids[1]
        untouched_original_type = catalog["products"][untouched_id].inventory_type

        async with client as c:
            with patch("ad_seller.storage.factory.get_storage", return_value=storage):
                from ad_seller.services import catalog_service

                await catalog_service.override_inventory_type(
                    product_id=overridden_id, inventory_type="ctv", reason="test"
                )
            resp = await c.get("/products", params={"limit": 500})

        assert resp.status_code == 200
        by_id = {p["product_id"]: p for p in resp.json()["products"]}
        assert by_id[overridden_id]["ext"]["inventory_type"] == "ctv"
        assert by_id[untouched_id]["ext"]["inventory_type"] == untouched_original_type
