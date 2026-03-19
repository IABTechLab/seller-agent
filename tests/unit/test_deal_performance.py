# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for GET /api/v1/deals/{deal_id}/performance endpoint.

Tests the deal performance metrics endpoint per the DealJockey seller
API contract (buyer-te6b.1.1, Section 5).

Covers:
- Authentication (401 for unauthenticated)
- Authorization (403 for non-counterparty, prevents enumeration)
- 404 for nonexistent deal when buyer would be authorized
- 200 with all contract-specified fields for valid request
- Query parameter validation (period, custom dates)
- Zero-delivery defaults
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version mismatch)
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
from ad_seller.models.api_key import ApiKeyRecord
from ad_seller.models.buyer_identity import BuyerIdentity


# =============================================================================
# Helpers
# =============================================================================


def _make_api_key_record(seat_id="seat-buyer-001", agency_id=None, advertiser_id=None):
    """Create a mock ApiKeyRecord with the given identity."""
    identity = BuyerIdentity(
        seat_id=seat_id,
        agency_id=agency_id,
        advertiser_id=advertiser_id,
    )
    return ApiKeyRecord(
        key_id="key-test-001",
        key_hash="fakehash",
        key_prefix_hint="ask_live_Ab...",
        identity=identity,
    )


def _make_deal_data(deal_id="DEMO-ABC123456789", buyer_seat_id="seat-buyer-001",
                    buyer_agency_id=None, buyer_advertiser_id=None, **overrides):
    """Create mock deal data with buyer identity embedded."""
    defaults = {
        "deal_id": deal_id,
        "deal_type": "PD",
        "status": "active",
        "quote_id": "qt-test123456",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.0,
            "final_cpm": 28.26,
            "currency": "USD",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "seat",
        "buyer_identity": {
            "seat_id": buyer_seat_id,
            "agency_id": buyer_agency_id,
            "advertiser_id": buyer_advertiser_id,
        },
        "expires_at": (datetime.utcnow() + timedelta(days=29)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def mock_storage():
    """In-memory mock storage matching the patterns from test_deal_booking_endpoints."""
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def authed_client(mock_storage):
    """HTTP client with a valid API key (seat-buyer-001)."""
    api_key_record = _make_api_key_record(seat_id="seat-buyer-001")
    app.dependency_overrides[_get_optional_api_key_record] = lambda: api_key_record
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unauthed_client(mock_storage):
    """HTTP client with no API key (anonymous)."""
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# Authentication Tests (401)
# =============================================================================


class TestPerformanceAuthentication:
    """Authentication is required: unauthenticated requests get 401."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, unauthed_client, mock_storage):
        """Requests without API key or bearer token return 401."""
        deal = _make_deal_data()
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await unauthed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance"
            )

        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"] == "authentication_required"


# =============================================================================
# Authorization Tests (403)
# =============================================================================


class TestPerformanceAuthorization:
    """Authorization: buyer must be counterparty. 403 prevents enumeration."""

    @pytest.mark.asyncio
    async def test_non_counterparty_returns_403(self, mock_storage):
        """Buyer who is NOT a counterparty to the deal gets 403."""
        # Deal belongs to seat-buyer-001
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        # Requesting as seat-OTHER-buyer
        other_key = _make_api_key_record(seat_id="seat-other-buyer")
        app.dependency_overrides[_get_optional_api_key_record] = lambda: other_key
        transport = ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
                resp = await client.get(
                    f"/api/v1/deals/{deal['deal_id']}/performance"
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error"] == "not_authorized"

    @pytest.mark.asyncio
    async def test_nonexistent_deal_non_counterparty_returns_403(self, mock_storage):
        """Non-counterparty gets 403 even for nonexistent deal (prevents enumeration)."""
        other_key = _make_api_key_record(seat_id="seat-other-buyer")
        app.dependency_overrides[_get_optional_api_key_record] = lambda: other_key
        transport = ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
                resp = await client.get(
                    "/api/v1/deals/DEMO-NONEXISTENT/performance"
                )

        app.dependency_overrides.clear()

        # Per contract: 403 regardless of deal existence for non-counterparty
        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error"] == "not_authorized"


# =============================================================================
# Not Found Tests (404)
# =============================================================================


class TestPerformanceNotFound:
    """Nonexistent deals return 403 uniformly (anti-enumeration).

    Per contract: "the seller returns 403 regardless of whether the deal
    exists (to prevent deal ID enumeration)." Since we can't verify
    counterparty status for a nonexistent deal, 403 is returned for all
    authenticated buyers when the deal doesn't exist.
    """

    @pytest.mark.asyncio
    async def test_nonexistent_deal_returns_403_for_any_buyer(
        self, authed_client, mock_storage
    ):
        """Any authenticated buyer gets 403 for nonexistent deal (anti-enumeration)."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                "/api/v1/deals/DEMO-NONEXISTENT/performance"
            )

        # 403 prevents enumeration -- buyer can't tell if deal exists or not
        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error"] == "not_authorized"


# =============================================================================
# Success Tests (200)
# =============================================================================


class TestPerformanceSuccess:
    """Successful performance data retrieval for authorized counterparty."""

    @pytest.mark.asyncio
    async def test_valid_request_returns_200(self, authed_client, mock_storage):
        """Authenticated counterparty gets 200 with all contract fields."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance"
            )

        assert resp.status_code == 200
        data = resp.json()

        # All contract-required fields must be present
        assert data["deal_id"] == deal["deal_id"]
        assert data["period"] == "last_30_days"  # default
        assert "period_start" in data
        assert "period_end" in data
        assert "impressions_delivered" in data
        assert "spend" in data
        assert "currency" in data
        assert data["currency"] == "USD"
        assert "fill_rate" in data
        assert "win_rate" in data
        assert "pacing_percentage" in data
        assert "avg_cpm" in data
        assert "last_delivery_at" in data
        assert "daily_breakdown" in data
        assert isinstance(data["daily_breakdown"], list)

    @pytest.mark.asyncio
    async def test_impressions_target_present(self, authed_client, mock_storage):
        """Response includes impressions_target from deal terms."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance"
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "impressions_target" in data

    @pytest.mark.asyncio
    async def test_default_period_is_last_30_days(self, authed_client, mock_storage):
        """Default period is last_30_days when no query param provided."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance"
            )

        assert resp.status_code == 200
        assert resp.json()["period"] == "last_30_days"

    @pytest.mark.asyncio
    async def test_explicit_period_is_echoed(self, authed_client, mock_storage):
        """Explicit period query param is echoed in response."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance?period=last_7_days"
            )

        assert resp.status_code == 200
        assert resp.json()["period"] == "last_7_days"

    @pytest.mark.asyncio
    async def test_lifetime_period(self, authed_client, mock_storage):
        """Lifetime period is accepted."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance?period=lifetime"
            )

        assert resp.status_code == 200
        assert resp.json()["period"] == "lifetime"

    @pytest.mark.asyncio
    async def test_custom_period_with_dates(self, authed_client, mock_storage):
        """Custom period with start_date and end_date returns 200."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance"
                "?period=custom&start_date=2026-03-01&end_date=2026-03-15"
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "custom"
        assert data["period_start"] == "2026-03-01"
        assert data["period_end"] == "2026-03-15"

    @pytest.mark.asyncio
    async def test_zero_delivery_defaults(self, authed_client, mock_storage):
        """Phase 1 placeholder: zero delivery returns null/zero defaults."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance"
            )

        assert resp.status_code == 200
        data = resp.json()
        # Phase 1: placeholder data -- at minimum, deal_id and period should be correct
        assert data["deal_id"] == deal["deal_id"]
        assert isinstance(data["impressions_delivered"], int)
        assert isinstance(data["spend"], (int, float))

    @pytest.mark.asyncio
    async def test_agency_counterparty_match(self, mock_storage):
        """Buyer matched by agency_id (not seat_id) is authorized."""
        deal = _make_deal_data(
            buyer_seat_id=None,
            buyer_agency_id="agency-test-001",
        )
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        agency_key = _make_api_key_record(
            seat_id=None,
            agency_id="agency-test-001",
        )
        app.dependency_overrides[_get_optional_api_key_record] = lambda: agency_key
        transport = ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
                resp = await client.get(
                    f"/api/v1/deals/{deal['deal_id']}/performance"
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200


# =============================================================================
# Query Parameter Validation Tests (400)
# =============================================================================


class TestPerformanceValidation:
    """Validation of query parameters per contract."""

    @pytest.mark.asyncio
    async def test_invalid_period_returns_400(self, authed_client, mock_storage):
        """Invalid period value returns 400."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance?period=invalid_value"
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_period"

    @pytest.mark.asyncio
    async def test_custom_period_missing_dates_returns_400(self, authed_client, mock_storage):
        """Custom period without start_date/end_date returns 400."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance?period=custom"
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "custom_period_missing_dates"

    @pytest.mark.asyncio
    async def test_custom_period_start_after_end_returns_400(self, authed_client, mock_storage):
        """Custom period where start_date > end_date returns 400."""
        deal = _make_deal_data(buyer_seat_id="seat-buyer-001")
        mock_storage._store[f"deal:{deal['deal_id']}"] = deal

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await authed_client.get(
                f"/api/v1/deals/{deal['deal_id']}/performance"
                "?period=custom&start_date=2026-03-20&end_date=2026-03-10"
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_date_range"
