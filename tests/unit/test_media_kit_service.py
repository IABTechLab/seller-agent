# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for MediaKitService.

Covers:
- Public access returns packages without exact pricing
- Authenticated access returns exact pricing
- Featured packages appear in public listing
- Empty media kit returns graceful response
"""

from unittest.mock import AsyncMock

import pytest

from ad_seller.engines.media_kit_service import MediaKitService
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.media_kit import Package, PackageLayer, PackageStatus
from ad_seller.models.pricing_tiers import TieredPricingConfig


def _make_package(
    package_id="pkg-001",
    name="Test Package",
    base_price=20.0,
    floor_price=10.0,
    is_featured=False,
    status=PackageStatus.ACTIVE,
    layer=PackageLayer.CURATED,
) -> dict:
    """Return a package as a dict (like storage returns)."""
    pkg = Package(
        package_id=package_id,
        name=name,
        description=f"Description for {name}",
        layer=layer,
        status=status,
        base_price=base_price,
        floor_price=floor_price,
        is_featured=is_featured,
    )
    return pkg.model_dump(mode="json")


@pytest.fixture
def pricing_engine():
    config = TieredPricingConfig(seller_organization_id="test-seller")
    return PricingRulesEngine(config=config)


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    return storage


@pytest.fixture
def service(mock_storage, pricing_engine):
    return MediaKitService(storage=mock_storage, pricing_engine=pricing_engine)


@pytest.fixture
def agency_buyer():
    return BuyerContext(
        identity=BuyerIdentity(agency_id="a1", agency_name="Agency"),
        is_authenticated=True,
    )


class TestPublicAccess:
    """Public access returns packages without exact pricing."""

    @pytest.mark.asyncio
    async def test_public_listing_has_price_range(self, service, mock_storage):
        mock_storage.list_packages.return_value = [
            _make_package(package_id="pkg-001", base_price=20.0),
        ]
        results = await service.list_packages_public()
        assert len(results) == 1
        view = results[0]
        assert "price_range" in view.model_dump()
        assert "$" in view.price_range
        # Public views should NOT have exact_price attribute
        assert not hasattr(view, "exact_price")

    @pytest.mark.asyncio
    async def test_public_single_package(self, service, mock_storage):
        pkg_data = _make_package(package_id="pkg-001")
        mock_storage.get_package.return_value = pkg_data
        result = await service.get_package_public("pkg-001")
        assert result is not None
        assert result.package_id == "pkg-001"
        assert not hasattr(result, "exact_price")

    @pytest.mark.asyncio
    async def test_public_returns_none_for_archived(self, service, mock_storage):
        pkg_data = _make_package(package_id="pkg-001", status=PackageStatus.ARCHIVED)
        mock_storage.get_package.return_value = pkg_data
        result = await service.get_package_public("pkg-001")
        assert result is None


class TestAuthenticatedAccess:
    """Authenticated access returns exact pricing."""

    @pytest.mark.asyncio
    async def test_authenticated_has_exact_price(self, service, mock_storage, agency_buyer):
        mock_storage.list_packages.return_value = [
            _make_package(package_id="pkg-001", base_price=20.0),
        ]
        results = await service.list_packages_authenticated(agency_buyer)
        assert len(results) == 1
        view = results[0]
        assert hasattr(view, "exact_price")
        assert view.exact_price > 0

    @pytest.mark.asyncio
    async def test_authenticated_single_package(self, service, mock_storage, agency_buyer):
        pkg_data = _make_package(package_id="pkg-001", base_price=20.0)
        mock_storage.get_package.return_value = pkg_data
        result = await service.get_package_authenticated("pkg-001", agency_buyer)
        assert result is not None
        assert result.exact_price > 0
        assert result.negotiation_enabled is True

    @pytest.mark.asyncio
    async def test_authenticated_price_less_than_base(self, service, mock_storage, agency_buyer):
        mock_storage.list_packages.return_value = [
            _make_package(package_id="pkg-001", base_price=20.0),
        ]
        results = await service.list_packages_authenticated(agency_buyer)
        # Agency should get a discount
        assert results[0].exact_price < 20.0


class TestFeaturedPackages:
    """Featured packages appear in featured-only listing."""

    @pytest.mark.asyncio
    async def test_featured_only_filter(self, service, mock_storage):
        mock_storage.list_packages.return_value = [
            _make_package(package_id="pkg-featured", is_featured=True),
            _make_package(package_id="pkg-regular", is_featured=False),
        ]
        results = await service.list_packages_public(featured_only=True)
        assert len(results) == 1
        assert results[0].package_id == "pkg-featured"
        assert results[0].is_featured is True

    @pytest.mark.asyncio
    async def test_all_packages_includes_featured(self, service, mock_storage):
        mock_storage.list_packages.return_value = [
            _make_package(package_id="pkg-featured", is_featured=True),
            _make_package(package_id="pkg-regular", is_featured=False),
        ]
        results = await service.list_packages_public(featured_only=False)
        assert len(results) == 2


class TestEmptyMediaKit:
    """Empty media kit returns graceful response."""

    @pytest.mark.asyncio
    async def test_empty_public_listing(self, service, mock_storage):
        mock_storage.list_packages.return_value = []
        results = await service.list_packages_public()
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_authenticated_listing(self, service, mock_storage, agency_buyer):
        mock_storage.list_packages.return_value = []
        results = await service.list_packages_authenticated(agency_buyer)
        assert results == []

    @pytest.mark.asyncio
    async def test_nonexistent_package_returns_none(self, service, mock_storage):
        mock_storage.get_package.return_value = None
        result = await service.get_package_public("does-not-exist")
        assert result is None
