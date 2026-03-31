# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for PricingType enum and its integration with seller models.

Tests that the pricing_type field correctly supports fixed, floor, and
on_request pricing across ProductDefinition, Package, QuotePricing, and
Pricing models. Backward compatibility is verified: existing code that
creates models without pricing_type should continue to work unchanged.
"""

import pytest
from pydantic import ValidationError

from ad_seller.models.pricing_type import PricingType
from ad_seller.models.core import DealType, Pricing, PricingModel
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.media_kit import Package, PackageLayer
from ad_seller.models.quotes import QuotePricing


class TestPricingTypeEnum:
    """Tests for the PricingType enum itself."""

    def test_enum_values(self):
        """PricingType has exactly three values: fixed, floor, on_request."""
        assert PricingType.FIXED.value == "fixed"
        assert PricingType.FLOOR.value == "floor"
        assert PricingType.ON_REQUEST.value == "on_request"

    def test_enum_is_str(self):
        """PricingType is a str enum for JSON serialization."""
        assert isinstance(PricingType.FIXED, str)
        assert PricingType.ON_REQUEST == "on_request"

    def test_enum_from_string(self):
        """PricingType can be constructed from string values."""
        assert PricingType("fixed") == PricingType.FIXED
        assert PricingType("floor") == PricingType.FLOOR
        assert PricingType("on_request") == PricingType.ON_REQUEST

    def test_invalid_value_raises(self):
        """Invalid string raises ValueError."""
        with pytest.raises(ValueError):
            PricingType("negotiable")


class TestProductDefinitionPricingType:
    """Tests for PricingType on ProductDefinition."""

    def test_on_request_with_no_pricing(self):
        """ProductDefinition with pricing_type=on_request and no CPMs is valid."""
        product = ProductDefinition(
            product_id="prod-001",
            name="Premium Sponsorship",
            inventory_type="display",
            pricing_type=PricingType.ON_REQUEST,
            base_cpm=None,
            floor_cpm=None,
        )
        assert product.pricing_type == PricingType.ON_REQUEST
        assert product.base_cpm is None
        assert product.floor_cpm is None

    def test_fixed_with_pricing(self):
        """ProductDefinition with pricing_type=fixed and CPMs set is valid (backward compat)."""
        product = ProductDefinition(
            product_id="prod-002",
            name="Standard Display",
            inventory_type="display",
            pricing_type=PricingType.FIXED,
            base_cpm=25.0,
            floor_cpm=20.0,
        )
        assert product.pricing_type == PricingType.FIXED
        assert product.base_cpm == 25.0
        assert product.floor_cpm == 20.0

    def test_defaults_to_fixed(self):
        """ProductDefinition without pricing_type defaults to FIXED."""
        product = ProductDefinition(
            product_id="prod-003",
            name="Default Product",
            inventory_type="video",
            base_cpm=30.0,
            floor_cpm=25.0,
        )
        assert product.pricing_type == PricingType.FIXED

    def test_floor_pricing_type(self):
        """ProductDefinition with pricing_type=floor is valid."""
        product = ProductDefinition(
            product_id="prod-004",
            name="Floor Priced Product",
            inventory_type="ctv",
            pricing_type=PricingType.FLOOR,
            base_cpm=40.0,
            floor_cpm=35.0,
        )
        assert product.pricing_type == PricingType.FLOOR

    def test_backward_compat_existing_fixture(self, sample_product):
        """Existing sample_product fixture still works and defaults to FIXED."""
        assert sample_product.pricing_type == PricingType.FIXED
        assert sample_product.base_cpm == 15.0
        assert sample_product.floor_cpm == 10.0


class TestPackagePricingType:
    """Tests for PricingType on Package."""

    def test_on_request_package(self):
        """Package with pricing_type=on_request and no prices is valid."""
        package = Package(
            package_id="pkg-001",
            name="Custom Sponsorship Package",
            layer=PackageLayer.CURATED,
            pricing_type=PricingType.ON_REQUEST,
            base_price=None,
            floor_price=None,
        )
        assert package.pricing_type == PricingType.ON_REQUEST
        assert package.base_price is None
        assert package.floor_price is None

    def test_fixed_package(self):
        """Package with pricing_type=fixed and prices set is valid."""
        package = Package(
            package_id="pkg-002",
            name="Standard Package",
            layer=PackageLayer.CURATED,
            pricing_type=PricingType.FIXED,
            base_price=28.0,
            floor_price=22.0,
        )
        assert package.pricing_type == PricingType.FIXED
        assert package.base_price == 28.0

    def test_package_defaults_to_fixed(self):
        """Package without pricing_type defaults to FIXED."""
        package = Package(
            package_id="pkg-003",
            name="Default Package",
            layer=PackageLayer.SYNCED,
            base_price=30.0,
            floor_price=25.0,
        )
        assert package.pricing_type == PricingType.FIXED


class TestQuotePricingPricingType:
    """Tests for PricingType on QuotePricing."""

    def test_on_request_quote_pricing(self):
        """QuotePricing with pricing_type=on_request and null base_cpm is valid."""
        qp = QuotePricing(
            pricing_type=PricingType.ON_REQUEST,
            base_cpm=None,
            final_cpm=0.0,
        )
        assert qp.pricing_type == PricingType.ON_REQUEST
        assert qp.base_cpm is None

    def test_fixed_quote_pricing(self):
        """QuotePricing with pricing_type=fixed and base_cpm set is valid."""
        qp = QuotePricing(
            pricing_type=PricingType.FIXED,
            base_cpm=25.0,
            final_cpm=22.5,
        )
        assert qp.pricing_type == PricingType.FIXED
        assert qp.base_cpm == 25.0

    def test_quote_pricing_defaults_to_fixed(self):
        """QuotePricing without pricing_type defaults to FIXED."""
        qp = QuotePricing(
            base_cpm=20.0,
            final_cpm=18.0,
        )
        assert qp.pricing_type == PricingType.FIXED


class TestCorePricingPricingType:
    """Tests for PricingType on Pricing (core model)."""

    def test_pricing_with_pricing_type(self):
        """Pricing model accepts pricing_type field."""
        pricing = Pricing(
            pricingmodel=PricingModel.CPM,
            price=25.0,
            currency="USD",
            pricing_type=PricingType.FLOOR,
        )
        assert pricing.pricing_type == PricingType.FLOOR

    def test_pricing_defaults_to_fixed(self):
        """Pricing model defaults pricing_type to FIXED."""
        pricing = Pricing(
            pricingmodel=PricingModel.CPM,
            price=25.0,
        )
        assert pricing.pricing_type == PricingType.FIXED

    def test_pricing_on_request(self):
        """Pricing with on_request and no price."""
        pricing = Pricing(
            pricing_type=PricingType.ON_REQUEST,
        )
        assert pricing.pricing_type == PricingType.ON_REQUEST
        assert pricing.price is None
