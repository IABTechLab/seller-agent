# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""update_package field-whitelist tests.

Defect: ``MediaKitService.update_package`` applied a blind
``if hasattr(package, key): setattr(...)`` over the raw request dict, so
callers could overwrite ``package_id``, ``layer``, ``created_at`` or set
type-invalid values with no re-validation.

Fix under test:

- Only whitelisted mutable Package fields may be updated; every other key
  (immutable, server-managed, or unknown) is rejected with a 422 that
  names the offending keys.
- The merged result is re-validated through the Pydantic ``Package``
  model, so type-invalid values are a 422 instead of corrupt state.
- The legitimate partial-patch happy path is preserved byte-identically
  (PUT /packages/{id} still 200s and emits PACKAGE_UPDATED).
"""

from datetime import datetime
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from ad_seller.engines.media_kit_service import MediaKitService
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.media_kit import Package, PackageLayer, PackageStatus
from ad_seller.models.pricing_tiers import TieredPricingConfig
from ad_seller.storage.base import StorageBackend


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


def _package(package_id="pkg-wl-001") -> Package:
    return Package(
        package_id=package_id,
        name="Original Name",
        description="Original description",
        layer=PackageLayer.CURATED,
        status=PackageStatus.ACTIVE,
        base_price=20.0,
        floor_price=10.0,
        is_featured=False,
    )


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def service(storage) -> MediaKitService:
    engine = PricingRulesEngine(config=TieredPricingConfig(seller_organization_id="test-seller"))
    return MediaKitService(storage=storage, pricing_engine=engine)


@pytest.fixture
def seeded(storage):
    pkg = _package()
    storage._data["package:pkg-wl-001"] = pkg.model_dump(mode="json")
    return pkg


async def _stored(storage, package_id="pkg-wl-001") -> dict:
    return await storage.get_package(package_id)


# =============================================================================
# Rejection: immutable / unknown keys -> 422, state untouched
# =============================================================================


class TestWhitelistRejection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "key,value",
        [
            ("package_id", "pkg-hijacked"),
            ("layer", "synced"),
            ("created_at", "1999-01-01T00:00:00"),
            ("updated_at", "1999-01-01T00:00:00"),
            ("ad_server_source", "gam"),
        ],
    )
    async def test_immutable_fields_rejected_with_422(self, service, storage, seeded, key, value):
        with pytest.raises(HTTPException) as exc_info:
            await service.update_package("pkg-wl-001", {key: value})
        assert exc_info.value.status_code == 422
        assert key in str(exc_info.value.detail)
        # State untouched
        stored = await _stored(storage)
        assert stored["package_id"] == "pkg-wl-001"
        assert stored["name"] == "Original Name"
        assert stored["updated_at"] is None

    @pytest.mark.asyncio
    async def test_unknown_key_rejected_with_422_naming_it(self, service, storage, seeded):
        with pytest.raises(HTTPException) as exc_info:
            await service.update_package("pkg-wl-001", {"frobnicate": 1, "name": "New"})
        assert exc_info.value.status_code == 422
        assert "frobnicate" in str(exc_info.value.detail)
        # Even the legitimate part of a mixed patch is NOT applied
        stored = await _stored(storage)
        assert stored["name"] == "Original Name"

    @pytest.mark.asyncio
    async def test_type_invalid_value_rejected_by_revalidation(self, service, storage, seeded):
        with pytest.raises(HTTPException) as exc_info:
            await service.update_package("pkg-wl-001", {"base_price": "not-a-number"})
        assert exc_info.value.status_code == 422
        stored = await _stored(storage)
        assert stored["base_price"] == 20.0

    @pytest.mark.asyncio
    async def test_invalid_placements_shape_rejected(self, service, storage, seeded):
        with pytest.raises(HTTPException) as exc_info:
            await service.update_package("pkg-wl-001", {"placements": [{"bogus": True}]})
        assert exc_info.value.status_code == 422
        stored = await _stored(storage)
        assert stored["placements"] == []


# =============================================================================
# Happy path preserved: legitimate partial patches
# =============================================================================


class TestPartialPatchPreserved:
    @pytest.mark.asyncio
    async def test_partial_patch_applies_and_preserves_rest(self, service, storage, seeded):
        updated = await service.update_package(
            "pkg-wl-001", {"name": "New Name", "base_price": 30.0}
        )
        assert updated is not None
        assert updated.name == "New Name"
        assert updated.base_price == 30.0
        # untouched fields preserved
        assert updated.description == "Original description"
        assert updated.floor_price == 10.0
        assert updated.layer == PackageLayer.CURATED
        assert updated.package_id == "pkg-wl-001"
        assert updated.created_at == seeded.created_at
        assert isinstance(updated.updated_at, datetime)
        # persisted
        stored = await _stored(storage)
        assert stored["name"] == "New Name"
        assert stored["base_price"] == 30.0

    @pytest.mark.asyncio
    async def test_placements_and_featured_patch(self, service, storage, seeded):
        updated = await service.update_package(
            "pkg-wl-001",
            {
                "is_featured": True,
                "placements": [
                    {
                        "product_id": "prod-1",
                        "product_name": "Prod One",
                        "ad_formats": ["video"],
                        "device_types": [3, 7],
                    }
                ],
            },
        )
        assert updated is not None
        assert updated.is_featured is True
        assert len(updated.placements) == 1
        assert updated.placements[0].product_id == "prod-1"

    @pytest.mark.asyncio
    async def test_missing_package_still_returns_none(self, service, storage):
        assert await service.update_package("pkg-nope", {"name": "X"}) is None


# =============================================================================
# HTTP surface: PUT /packages/{package_id}
# =============================================================================


@pytest.fixture
def api_client(storage):
    import httpx
    from httpx import ASGITransport

    from ad_seller.interfaces.api.main import app

    with patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)):
        with patch(
            "ad_seller.events.helpers.emit_event", new_callable=AsyncMock
        ) as mock_emit:
            transport = ASGITransport(app=app)
            client = httpx.AsyncClient(transport=transport, base_url="http://test")
            client._mock_emit = mock_emit  # expose for assertions
            yield client


class TestPutPackagesEndpoint:
    @pytest.mark.asyncio
    async def test_put_rejects_package_id_overwrite_with_422(self, api_client, seeded):
        async with api_client:
            resp = await api_client.put(
                "/packages/pkg-wl-001", json={"package_id": "pkg-hijacked"}
            )
            assert resp.status_code == 422
            assert "package_id" in str(resp.json())
            # No PACKAGE_UPDATED event for a rejected update
            assert not any(
                call.kwargs.get("event_type") is not None
                and "PACKAGE_UPDATED" in str(call.kwargs.get("event_type"))
                for call in api_client._mock_emit.call_args_list
            )

    @pytest.mark.asyncio
    async def test_put_happy_path_unchanged(self, api_client, seeded):
        async with api_client:
            resp = await api_client.put(
                "/packages/pkg-wl-001", json={"name": "Renamed", "base_price": 25.0}
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["name"] == "Renamed"
            assert body["base_price"] == 25.0
            assert body["package_id"] == "pkg-wl-001"
            assert body["description"] == "Original description"
            # PACKAGE_UPDATED still emitted on the happy path
            assert any(
                "PACKAGE_UPDATED" in str(call.kwargs.get("event_type"))
                for call in api_client._mock_emit.call_args_list
            )
