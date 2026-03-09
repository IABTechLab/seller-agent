# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for IAB Deals API v1.0 — Deal Booking endpoints.

Tests POST /api/v1/deals and GET /api/v1/deals/{deal_id}.
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


# =============================================================================
# Helpers
# =============================================================================


def _make_available_quote(**overrides):
    defaults = {
        "quote_id": "qt-test123456",
        "status": "available",
        "deal_type": "PD",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.0,
            "tier_discount_pct": 15.0,
            "volume_discount_pct": 5.0,
            "final_cpm": 28.26,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base price: $35.00 CPM | Advertiser tier: -15% | Final: $28.26",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "advertiser",
        "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def mock_storage():
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
def client(mock_storage):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# POST /api/v1/deals
# =============================================================================


class TestBookDeal:

    async def test_happy_path_book_deal(self, client, mock_storage):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/deals", json={"quote_id": quote["quote_id"]})

        assert resp.status_code == 200
        data = resp.json()
        assert data["deal_id"].startswith("DEMO-")
        assert data["status"] == "proposed"
        assert data["quote_id"] == quote["quote_id"]
        assert data["pricing"]["final_cpm"] == 28.26
        assert data["pricing"]["base_cpm"] == 35.0
        assert data["terms"]["impressions"] == 5000000
        assert data["product"]["product_id"] == "ctv-premium-sports"
        assert "ttd" in data["activation_instructions"]
        assert "dv360" in data["activation_instructions"]
        assert data["openrtb_params"]["id"] == data["deal_id"]
        assert data["openrtb_params"]["bidfloor"] == 28.26

    async def test_quote_status_updated_to_booked(self, client, mock_storage):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/deals", json={"quote_id": quote["quote_id"]})

        assert resp.status_code == 200
        deal_id = resp.json()["deal_id"]
        stored_quote = mock_storage._store[f"quote:{quote['quote_id']}"]
        assert stored_quote["status"] == "booked"
        assert stored_quote["deal_id"] == deal_id

    async def test_deal_stored_in_deal_storage(self, client, mock_storage):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/deals", json={"quote_id": quote["quote_id"]})

        deal_id = resp.json()["deal_id"]
        stored_deal = mock_storage._store[f"deal:{deal_id}"]
        assert stored_deal["deal_id"] == deal_id
        assert stored_deal["status"] == "proposed"

    async def test_pa_deal_sets_auction_type_3(self, client, mock_storage):
        quote = _make_available_quote(deal_type="PA")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/deals", json={"quote_id": quote["quote_id"]})

        assert resp.status_code == 200
        assert resp.json()["openrtb_params"]["at"] == 3

    # Error cases

    async def test_quote_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/deals", json={"quote_id": "qt-nonexistent"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "quote_not_found"

    async def test_expired_quote_returns_410(self, client, mock_storage):
        quote = _make_available_quote(
            expires_at=(datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z",
        )
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/deals", json={"quote_id": quote["quote_id"]})
        assert resp.status_code == 410
        assert resp.json()["detail"]["error"] == "quote_expired"

    async def test_already_booked_quote_returns_409(self, client, mock_storage):
        quote = _make_available_quote(status="booked")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/deals", json={"quote_id": quote["quote_id"]})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "quote_already_booked"


# =============================================================================
# GET /api/v1/deals/{deal_id}
# =============================================================================


class TestGetDeal:

    async def test_retrieve_stored_deal(self, client, mock_storage):
        deal_data = {
            "deal_id": "DEMO-ABC123456789",
            "deal_type": "PD",
            "status": "proposed",
            "quote_id": "qt-test123456",
            "product": {"product_id": "ctv-premium-sports", "name": "CTV", "inventory_type": "ctv"},
            "pricing": {"base_cpm": 35.0, "final_cpm": 28.26, "currency": "USD"},
            "terms": {"impressions": 5000000, "flight_start": "2026-04-01", "flight_end": "2026-04-30"},
            "expires_at": (datetime.utcnow() + timedelta(days=29)).isoformat() + "Z",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        mock_storage._store["deal:DEMO-ABC123456789"] = deal_data

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/DEMO-ABC123456789")

        assert resp.status_code == 200
        assert resp.json()["deal_id"] == "DEMO-ABC123456789"

    async def test_deal_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/DEMO-NONEXISTENT")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "deal_not_found"

    async def test_lazy_expiry_for_proposed_deal(self, client, mock_storage):
        deal_data = {
            "deal_id": "DEMO-EXPIREDONE1",
            "status": "proposed",
            "expires_at": (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z",
        }
        mock_storage._store["deal:DEMO-EXPIREDONE1"] = deal_data

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/DEMO-EXPIREDONE1")

        assert resp.status_code == 200
        assert resp.json()["status"] == "expired"
        assert mock_storage._store["deal:DEMO-EXPIREDONE1"]["status"] == "expired"

    async def test_active_deal_not_expired(self, client, mock_storage):
        deal_data = {
            "deal_id": "DEMO-ACTIVEDEAL1",
            "status": "active",
            "expires_at": (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z",
        }
        mock_storage._store["deal:DEMO-ACTIVEDEAL1"] = deal_data

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/DEMO-ACTIVEDEAL1")

        assert resp.status_code == 200
        assert resp.json()["status"] == "active"


# =============================================================================
# End-to-End: Quote → Book → Retrieve
# =============================================================================


class TestQuoteToDealFlow:

    async def test_full_quote_to_deal_flow(self, client, mock_storage):
        from ad_seller.models.flow_state import ProductDefinition
        from ad_seller.models.core import DealType, PricingModel

        products = {
            "ctv-premium-sports": ProductDefinition(
                product_id="ctv-premium-sports",
                name="Premium CTV - Sports",
                inventory_type="ctv",
                supported_deal_types=[DealType.PREFERRED_DEAL],
                supported_pricing_models=[PricingModel.CPM],
                base_cpm=35.0,
                floor_cpm=28.0,
                minimum_impressions=100000,
            ),
        }
        mock_flow = MagicMock()
        mock_flow.state = MagicMock()
        mock_flow.state.products = products
        mock_flow.kickoff = AsyncMock()

        with (
            patch("ad_seller.flows.ProductSetupFlow", return_value=mock_flow),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            # Step 1: Create quote
            quote_resp = await client.post("/api/v1/quotes", json={
                "product_id": "ctv-premium-sports",
                "deal_type": "PD",
                "impressions": 5000000,
                "flight_start": "2026-04-01",
                "flight_end": "2026-04-30",
            })
            assert quote_resp.status_code == 200
            quote_id = quote_resp.json()["quote_id"]

            # Step 2: Book deal from quote
            deal_resp = await client.post("/api/v1/deals", json={"quote_id": quote_id})
            assert deal_resp.status_code == 200
            deal_id = deal_resp.json()["deal_id"]
            assert deal_id.startswith("DEMO-")

            # Step 3: Retrieve the deal
            get_deal_resp = await client.get(f"/api/v1/deals/{deal_id}")
            assert get_deal_resp.status_code == 200
            assert get_deal_resp.json()["deal_id"] == deal_id

            # Step 4: Verify original quote is now "booked"
            get_quote_resp = await client.get(f"/api/v1/quotes/{quote_id}")
            assert get_quote_resp.status_code == 200
            assert get_quote_resp.json()["status"] == "booked"
            assert get_quote_resp.json()["deal_id"] == deal_id

            # Step 5: Double-booking returns 409
            double_book = await client.post("/api/v1/deals", json={"quote_id": quote_id})
            assert double_book.status_code == 409
