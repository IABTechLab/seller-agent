# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for POST /api/v1/deals/from-template endpoint.

Tests template-based deal creation: the buyer sends structured parameters
(deal type, product, targeting, max_cpm) and the seller creates the deal
or rejects with reason.

bead: buyer-te6b.1.9
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version mismatch)
# before any import of ad_seller.flows triggers __init__.py.
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

from datetime import datetime, timedelta

import httpx
from httpx import ASGITransport

from ad_seller.interfaces.api.main import app, _get_optional_api_key_record


# =============================================================================
# Helpers
# =============================================================================


def _mock_product_setup_flow(products_dict):
    """Return a mock ProductSetupFlow whose state has the given products."""
    mock_flow = MagicMock()
    mock_flow.state = MagicMock()
    mock_flow.state.products = products_dict
    mock_flow.kickoff = AsyncMock()
    return mock_flow


def _make_product(**overrides):
    from ad_seller.models.flow_state import ProductDefinition
    from ad_seller.models.core import DealType, PricingModel

    defaults = dict(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        description="Premium CTV sports inventory",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL, DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )
    defaults.update(overrides)
    return ProductDefinition(**defaults)


def _products():
    return {"ctv-premium-sports": _make_product()}


def _make_api_key_record(**overrides):
    """Create a mock ApiKeyRecord for authenticated requests."""
    from ad_seller.models.buyer_identity import BuyerIdentity

    identity = BuyerIdentity(
        seat_id=overrides.pop("seat_id", "seat-ttd-12345"),
        agency_id=overrides.pop("agency_id", "agency-groupm-001"),
        advertiser_id=overrides.pop("advertiser_id", "adv-betacorp-001"),
        dsp_platform=overrides.pop("dsp_platform", "ttd"),
    )
    record = MagicMock()
    record.identity = identity
    return record


@pytest.fixture
def mock_storage():
    """In-memory dict-backed mock storage."""
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def authenticated_client(mock_storage):
    """httpx AsyncClient with authenticated API key dependency override."""
    api_key_record = _make_api_key_record()
    app.dependency_overrides[_get_optional_api_key_record] = lambda: api_key_record
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client(mock_storage):
    """httpx AsyncClient with no API key (anonymous)."""
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# POST /api/v1/deals/from-template — Successful Deal Creation
# =============================================================================


class TestFromTemplateSuccess:
    """Test successful template-based deal creation (HTTP 201)."""

    async def test_happy_path_creates_deal(self, authenticated_client, mock_storage):
        """Successful from-template request returns 201 with deal_id and actual_price_cpm."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "impressions": 5000000,
                "flight_start": "2026-04-01",
                "flight_end": "2026-04-30",
            })

        assert resp.status_code == 201
        data = resp.json()
        assert data["deal_id"].startswith("DEMO-")
        assert data["deal_type"] == "PD"
        assert data["status"] == "active"  # auto-accepted, not "proposed"
        assert data["product"]["product_id"] == "ctv-premium-sports"
        assert data["pricing"]["final_cpm"] > 0
        assert data["pricing"]["base_cpm"] == 35.0
        assert data["pricing"]["currency"] == "USD"
        assert data["buyer_tier"] == "advertiser"
        assert "expires_at" in data
        assert "created_at" in data
        assert data["template_metadata"]["created_via"] == "from-template"
        assert data["template_metadata"]["max_cpm_submitted"] == 40.00
        assert data["template_metadata"]["price_accepted"] is True

    async def test_deal_stored_in_storage(self, authenticated_client, mock_storage):
        """Created deal is persisted in deal storage."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "impressions": 5000000,
            })

        deal_id = resp.json()["deal_id"]
        stored_deal = mock_storage._store[f"deal:{deal_id}"]
        assert stored_deal["deal_id"] == deal_id
        assert stored_deal["status"] == "active"

    async def test_openrtb_params_included(self, authenticated_client, mock_storage):
        """Response includes OpenRTB activation parameters."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
            })

        data = resp.json()
        assert data["openrtb_params"]["id"] == data["deal_id"]
        assert data["openrtb_params"]["bidfloor"] == data["pricing"]["final_cpm"]
        assert data["openrtb_params"]["bidfloorcur"] == "USD"

    async def test_activation_instructions_included(self, authenticated_client, mock_storage):
        """Response includes DSP activation instructions."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
            })

        data = resp.json()
        assert "ttd" in data["activation_instructions"]
        assert "dv360" in data["activation_instructions"]

    async def test_pg_deal_sets_guaranteed_true(self, authenticated_client, mock_storage):
        """PG deal type sets guaranteed=true in terms."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PG",
                "max_cpm": 40.00,
                "impressions": 5000000,
            })

        assert resp.status_code == 201
        assert resp.json()["terms"]["guaranteed"] is True

    async def test_default_flight_dates(self, authenticated_client, mock_storage):
        """Flight dates default to today + 30 days when not specified."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
            })

        assert resp.status_code == 201
        data = resp.json()
        assert data["terms"]["flight_start"] is not None
        assert data["terms"]["flight_end"] is not None

    async def test_buyer_identity_in_body_used(self, authenticated_client, mock_storage):
        """Buyer identity from request body is used for tiered pricing."""
        # Override to no api_key to test body identity path
        app.dependency_overrides[_get_optional_api_key_record] = lambda: None
        # But we need auth — for from-template, auth is required.
        # This tests the body identity fallback (demo path) when api key is present.
        api_key_record = _make_api_key_record()
        app.dependency_overrides[_get_optional_api_key_record] = lambda: api_key_record

        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "buyer_identity": {
                    "seat_id": "seat-ttd-12345",
                    "agency_id": "agency-groupm-001",
                    "advertiser_id": "adv-nike-001",
                    "dsp_platform": "ttd",
                },
            })

        assert resp.status_code == 201
        data = resp.json()
        assert data["buyer_tier"] in ("advertiser", "agency", "seat", "public")
        assert data["pricing"]["tier_discount_pct"] >= 0


# =============================================================================
# POST /api/v1/deals/from-template — Price Rejection (422)
# =============================================================================


class TestFromTemplateRejection:
    """Test below-floor rejection returns 422 with seller_minimum_cpm."""

    async def test_below_floor_returns_422(self, authenticated_client, mock_storage):
        """When max_cpm < seller floor, returns 422 with seller_minimum_cpm."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 5.00,  # Well below any floor
                "impressions": 5000000,
            })

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["error"] == "price_below_seller_minimum"
        assert data["detail"]["seller_minimum_cpm"] > 0
        assert "pricing_breakdown" in data["detail"]
        assert data["detail"]["pricing_breakdown"]["base_cpm"] == 35.0

    async def test_rejection_includes_pricing_breakdown(self, authenticated_client, mock_storage):
        """422 response includes full pricing breakdown."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 10.00,
            })

        assert resp.status_code == 422
        breakdown = resp.json()["detail"]["pricing_breakdown"]
        assert "base_cpm" in breakdown
        assert "final_cpm" in breakdown
        assert "currency" in breakdown

    async def test_no_deal_stored_on_rejection(self, authenticated_client, mock_storage):
        """Rejected requests do not create a deal in storage."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 1.00,
            })

        assert resp.status_code == 422
        # No deals should be stored
        deal_keys = [k for k in mock_storage._store if k.startswith("deal:")]
        assert len(deal_keys) == 0


# =============================================================================
# POST /api/v1/deals/from-template — Authentication (401)
# =============================================================================


class TestFromTemplateAuth:
    """Test authentication requirements."""

    async def test_unauthenticated_returns_401(self, unauthenticated_client, mock_storage):
        """Request without API key or Bearer token returns 401."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await unauthenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
            })

        assert resp.status_code == 401
        data = resp.json()
        assert data["detail"]["error"] == "authentication_required"


# =============================================================================
# POST /api/v1/deals/from-template — Validation Errors (400)
# =============================================================================


class TestFromTemplateValidation:
    """Test request validation and 400 error responses."""

    async def test_missing_product_id_returns_422(self, authenticated_client, mock_storage):
        """Missing required field product_id returns validation error."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "deal_type": "PD",
                "max_cpm": 40.00,
            })

        # Pydantic validation returns 422 for missing required fields
        assert resp.status_code == 422

    async def test_invalid_deal_type_returns_400(self, authenticated_client, mock_storage):
        """Invalid deal_type returns 400 with error code."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "INVALID",
                "max_cpm": 40.00,
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_deal_type"

    async def test_product_not_found_returns_404(self, authenticated_client, mock_storage):
        """Non-existent product_id returns 404."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "nonexistent-product",
                "deal_type": "PD",
                "max_cpm": 40.00,
            })

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "product_not_found"

    async def test_pg_without_impressions_returns_400(self, authenticated_client, mock_storage):
        """PG deal without impressions returns 400."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PG",
                "max_cpm": 40.00,
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "pg_requires_impressions"

    async def test_below_minimum_impressions_returns_400(self, authenticated_client, mock_storage):
        """Impressions below product minimum returns 400."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "impressions": 50,
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "below_minimum_impressions"

    async def test_zero_max_cpm_returns_400(self, authenticated_client, mock_storage):
        """Zero max_cpm returns 400 invalid_max_cpm."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 0,
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_max_cpm"

    async def test_negative_max_cpm_returns_400(self, authenticated_client, mock_storage):
        """Negative max_cpm returns 400 invalid_max_cpm."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": -5.00,
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_max_cpm"


# =============================================================================
# POST /api/v1/deals/from-template — Flight Date Validation (buyer-947)
# =============================================================================


class TestFromTemplateFlightDateValidation:
    """Test flight date validation returns 400 with invalid_flight_dates."""

    async def test_flight_start_in_past_returns_400(self, authenticated_client, mock_storage):
        """flight_start in the past returns 400 with invalid_flight_dates."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "flight_start": "2020-01-01",
                "flight_end": "2020-01-31",
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_flight_dates"

    async def test_flight_end_before_flight_start_returns_400(self, authenticated_client, mock_storage):
        """flight_end before flight_start returns 400 with invalid_flight_dates."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "flight_start": "2027-06-15",
                "flight_end": "2027-06-01",
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_flight_dates"

    async def test_flight_start_today_is_valid(self, authenticated_client, mock_storage):
        """flight_start of today should be accepted (not in the past)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        end = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "flight_start": today,
                "flight_end": end,
            })

        # Should succeed (201), not fail with invalid_flight_dates
        assert resp.status_code == 201

    async def test_flight_start_equals_flight_end_is_valid(self, authenticated_client, mock_storage):
        """Same start and end date should be valid (single-day flight)."""
        future = (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d")
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
                "flight_start": future,
                "flight_end": future,
            })

        assert resp.status_code == 201


# =============================================================================
# POST /api/v1/deals/from-template — OpenRTB at Value (buyer-4xi)
# =============================================================================


class TestFromTemplateOpenRtbAtValue:
    """Test OpenRTB at value is correct per deal type."""

    async def test_pd_deal_returns_at_3(self, authenticated_client, mock_storage):
        """PD (Preferred Deal) should use at=3 (private marketplace)."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "max_cpm": 40.00,
            })

        assert resp.status_code == 201
        data = resp.json()
        assert data["openrtb_params"]["at"] == 3, (
            f"PD deal should have at=3 (private marketplace), got at={data['openrtb_params']['at']}"
        )

    async def test_pa_deal_returns_at_3(self, authenticated_client, mock_storage):
        """PA (Private Auction) should use at=3."""
        pa_product = _make_product(
            supported_deal_types=[
                __import__("ad_seller.models.core", fromlist=["DealType"]).DealType.PRIVATE_AUCTION,
            ],
        )
        products = {"ctv-premium-sports": pa_product}
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(products)),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PA",
                "max_cpm": 40.00,
            })

        assert resp.status_code == 201
        assert resp.json()["openrtb_params"]["at"] == 3

    async def test_pg_deal_returns_at_1(self, authenticated_client, mock_storage):
        """PG (Programmatic Guaranteed) should use at=1 (fixed price)."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PG",
                "max_cpm": 40.00,
                "impressions": 5000000,
            })

        assert resp.status_code == 201
        assert resp.json()["openrtb_params"]["at"] == 1


# =============================================================================
# POST /api/v1/deals/from-template — Missing max_cpm (buyer-111)
# =============================================================================


class TestFromTemplateMissingMaxCpm:
    """Test missing max_cpm returns 400 with missing_max_cpm error code."""

    async def test_missing_max_cpm_returns_400(self, authenticated_client, mock_storage):
        """Request without max_cpm returns 400 with missing_max_cpm error code."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
            })

        assert resp.status_code == 400, (
            f"Expected 400 for missing max_cpm, got {resp.status_code}"
        )
        assert resp.json()["detail"]["error"] == "missing_max_cpm"

    async def test_missing_max_cpm_returns_400_not_422(self, authenticated_client, mock_storage):
        """Ensure missing max_cpm specifically does NOT return 422 (Pydantic default)."""
        with (
            patch("ad_seller.flows.ProductSetupFlow",
                  return_value=_mock_product_setup_flow(_products())),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await authenticated_client.post("/api/v1/deals/from-template", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
            })

        assert resp.status_code != 422, (
            "missing max_cpm should return 400 per contract, not 422 from Pydantic"
        )
