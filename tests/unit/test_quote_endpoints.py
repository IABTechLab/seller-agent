# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for IAB Deals API v1.0 — Quote endpoints.

Tests POST /api/v1/quotes and GET /api/v1/quotes/{quote_id}.
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
        # Add the class name that __init__.py expects to import
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from datetime import datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402

# =============================================================================
# Helpers
# =============================================================================


def _mock_product_setup_flow(products_dict):
    """Return a mock ProductSetupFlow whose state has the given products.

    Kept for backward compatibility with TestGetQuote and other call sites.
    """
    mock_flow = MagicMock()
    mock_flow.state = MagicMock()
    mock_flow.state.products = products_dict
    mock_flow.kickoff = AsyncMock()
    mock_flow.kickoff_async = AsyncMock()
    return mock_flow


def _mock_catalog(products_dict):
    """Build the dict shape returned by `_get_static_product_catalog`.

    Quote endpoint switched from `ProductSetupFlow.kickoff()` to the cached
    static catalog (ar-uwad / ar-0vtg). Tests patch the catalog accessor
    rather than the flow class.
    """
    inventory_types = sorted({p.inventory_type for p in products_dict.values()})
    return {"products": products_dict, "inventory_types": inventory_types}


def _make_product(**overrides):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

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
    storage._store = store
    return storage


@pytest.fixture
def client(mock_storage):
    """httpx AsyncClient with FastAPI dependency overrides."""
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# POST /api/v1/quotes
# =============================================================================


class TestCreateQuote:
    async def test_happy_path_pd_quote(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "PD",
                    "impressions": 5000000,
                    "flight_start": "2026-04-01",
                    "flight_end": "2026-04-30",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["quote_id"].startswith("qt-")
        assert data["status"] == "available"
        assert data["deal_type"] == "PD"
        assert data["product"]["product_id"] == "ctv-premium-sports"
        assert data["pricing"]["base_cpm"] == 35.0
        assert data["pricing"]["final_cpm"] > 0
        assert data["terms"]["flight_start"] == "2026-04-01"
        assert data["terms"]["impressions"] == 5000000
        assert data["terms"]["guaranteed"] is False
        assert "expires_at" in data

    async def test_pg_quote_sets_guaranteed_true(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "PG",
                    "impressions": 5000000,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["terms"]["guaranteed"] is True

    async def test_target_cpm_accepted_when_above_floor(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "PD",
                    "impressions": 1000000,
                    "target_cpm": 32.00,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["pricing"]["final_cpm"] == 32.0

    async def test_target_cpm_rejected_below_floor(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "PD",
                    "impressions": 1000000,
                    "target_cpm": 0.50,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["pricing"]["final_cpm"] != 0.50
        assert data["pricing"]["final_cpm"] > 0

    async def test_buyer_identity_affects_tier(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "PD",
                    "buyer_identity": {
                        "seat_id": "seat-ttd-12345",
                        "agency_id": "agency-groupm-001",
                        "advertiser_id": "adv-nike-001",
                        "dsp_platform": "ttd",
                    },
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["buyer_tier"] == "advertiser"
        assert data["pricing"]["tier_discount_pct"] == 15.0

    # Error cases

    async def test_product_not_found(self, client, mock_storage):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "nonexistent",
                    "deal_type": "PD",
                },
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "product_not_found"

    async def test_invalid_deal_type(self, client, mock_storage):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "INVALID",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_deal_type"

    async def test_pg_without_impressions(self, client, mock_storage):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "PG",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "pg_requires_impressions"

    async def test_below_minimum_impressions(self, client, mock_storage):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "PD",
                    "impressions": 50,
                },
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "below_minimum_impressions"


# =============================================================================
# GET /api/v1/quotes/{quote_id}
# =============================================================================


class TestGetQuote:
    async def test_retrieve_stored_quote(self, client, mock_storage):
        quote_data = {
            "quote_id": "qt-abc123",
            "status": "available",
            "product": {"product_id": "ctv-premium-sports", "name": "CTV", "inventory_type": "ctv"},
            "pricing": {"base_cpm": 35.0, "final_cpm": 29.75, "currency": "USD"},
            "terms": {"flight_start": "2026-04-01", "flight_end": "2026-04-30"},
            "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        }
        mock_storage._store["quote:qt-abc123"] = quote_data

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/quotes/qt-abc123")

        assert resp.status_code == 200
        assert resp.json()["quote_id"] == "qt-abc123"

    async def test_quote_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/quotes/qt-nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "quote_not_found"

    async def test_expired_quote_returns_410(self, client, mock_storage):
        quote_data = {
            "quote_id": "qt-expired1",
            "status": "available",
            "expires_at": (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z",
        }
        mock_storage._store["quote:qt-expired1"] = quote_data

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/quotes/qt-expired1")

        assert resp.status_code == 410
        assert resp.json()["detail"]["error"] == "quote_expired"
        stored = mock_storage._store["quote:qt-expired1"]
        assert stored["status"] == "expired"
