# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests for AI-14 (field feedback).

The inventory-type override API (override_inventory_type /
get_inventory_type_override) has always round-tripped correctly through
storage, but nothing on the read side ever consulted it: GET /products
and GET /products/{id} serve exclusively from the cached static
catalog, so an applied override was invisible on every read path.

Fix under test: catalog_service.apply_inventory_type_override() is the
ONE place that consults a stored override (mirroring rate_card_service's
single-resolver shape from issue #69), called by both GET /products and
GET /products/{id} so the list and the single-product read never
disagree. supported_deal_types is recomputed via the same
infer_deal_types() used when products are first built, so the override
doesn't leave ext.inventory_type and ext.deal_types self-contradicting
on the wire.
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

    async def test_override_swaps_type_and_recomputes_deal_types(self, storage):
        from ad_seller.interfaces.api import deps
        from ad_seller.services import catalog_service

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            catalog = deps.get_product_catalog()
            product = next(iter(catalog["products"].values()))

            await catalog_service.override_inventory_type(
                product_id=product.product_id, inventory_type="ctv", reason="test"
            )
            result = await catalog_service.apply_inventory_type_override(product)

        assert result.inventory_type == "ctv"
        assert result.supported_deal_types == catalog_service.infer_deal_types("ctv")
        # The cached catalog product itself must never be mutated in place.
        assert product.inventory_type != "ctv"


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
