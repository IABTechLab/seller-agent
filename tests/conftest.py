# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pytest configuration and fixtures for Ad Seller System tests."""

import os
import sys

import pytest

from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.pricing_tiers import TieredPricingConfig

_session_exit_status: int | None = None


def pytest_sessionfinish(session, exitstatus):
    """Record the exit status for the hard-exit in ``pytest_unconfigure``."""
    global _session_exit_status
    _session_exit_status = int(exitstatus)


def pytest_unconfigure(config):
    """Hard-exit after pytest has finished all reporting (ar-r82f.21).

    Even with ``_telemetry_shim.py`` setting every documented opt-out env
    var at import time, transitive deps (chromadb/posthog/opentelemetry)
    register atexit handlers that hang interpreter shutdown for ~5 min
    after pytest itself finishes. Our CLI entry point handles this with
    ``os._exit(0)`` after typer returns; pytest doesn't have an
    equivalent path, so this hook forces the same behaviour.

    The hard exit used to live in ``pytest_sessionfinish``, but the
    terminal reporter prints the FAILURES section, short summary, and
    stats line after the non-wrapper ``pytest_sessionfinish`` hooks run,
    so exiting there ended every failing run with a bare ``F`` and no
    diagnostics. ``pytest_unconfigure`` fires after all reporting is
    done. ``os._exit`` also skips stdio flushing, which silently drops
    any reporting still sitting in a block-buffered stream (the normal
    case when output is redirected to a file or CI log), so both
    streams are flushed first.
    """
    if _session_exit_status is not None:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_session_exit_status)


@pytest.fixture
def public_buyer_context() -> BuyerContext:
    """Create a public (unauthenticated) buyer context."""
    return BuyerContext(
        identity=BuyerIdentity(),
        is_authenticated=False,
    )


@pytest.fixture
def agency_buyer_context() -> BuyerContext:
    """Create an agency-authenticated buyer context."""
    identity = BuyerIdentity(
        agency_id="test-agency-001",
        agency_name="Test Agency",
        agency_holding_company="Test Holding Co",
    )
    return BuyerContext(
        identity=identity,
        is_authenticated=True,
    )


@pytest.fixture
def advertiser_buyer_context() -> BuyerContext:
    """Create an advertiser-level buyer context."""
    identity = BuyerIdentity(
        agency_id="test-agency-001",
        agency_name="Test Agency",
        advertiser_id="test-advertiser-001",
        advertiser_name="Test Advertiser",
    )
    return BuyerContext(
        identity=identity,
        is_authenticated=True,
    )


@pytest.fixture
def pricing_config() -> TieredPricingConfig:
    """Create a default pricing configuration."""
    return TieredPricingConfig(
        seller_organization_id="test-seller",
    )


@pytest.fixture
def sample_product() -> ProductDefinition:
    """Create a sample product definition."""
    return ProductDefinition(
        product_id="test-product-001",
        name="Test Display Product",
        description="Test display inventory",
        inventory_type="display",
        supported_deal_types=[DealType.PREFERRED_DEAL, DealType.PRIVATE_AUCTION],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=15.0,
        floor_cpm=10.0,
        minimum_impressions=10000,
    )


@pytest.fixture
def sample_products(sample_product: ProductDefinition) -> dict[str, ProductDefinition]:
    """Create a dictionary of sample products."""
    video_product = ProductDefinition(
        product_id="test-product-002",
        name="Test Video Product",
        inventory_type="video",
        supported_deal_types=[DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM, PricingModel.CPCV],
        base_cpm=25.0,
        floor_cpm=18.0,
    )

    ctv_product = ProductDefinition(
        product_id="test-product-003",
        name="Test CTV Product",
        inventory_type="ctv",
        supported_deal_types=[DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
    )

    return {
        sample_product.product_id: sample_product,
        video_product.product_id: video_product,
        ctv_product.product_id: ctv_product,
    }


@pytest.fixture
def sample_proposal_data() -> dict:
    """Create sample proposal data."""
    return {
        "product_id": "test-product-001",
        "deal_type": "preferred_deal",
        "price": 12.0,
        "impressions": 1000000,
        "start_date": "2026-02-01",
        "end_date": "2026-03-31",
        "buyer_id": "test-buyer-001",
    }
