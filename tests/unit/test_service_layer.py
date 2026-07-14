# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the extracted service layer (EP-3.1 / bead ar-6u86).

One happy path + at least one edge case per service:
catalog_service, quote_service, deal_service, order_service,
negotiation_service, approval_service, session_service.

These exercise the services DIRECTLY (no HTTP), complementing the
existing endpoint tests which now act as the characterization net for
the thin routers.
"""

import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used in test_deal_booking_endpoints.py.
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

from ad_seller.services import (  # noqa: E402
    approval_service,
    catalog_service,
    deal_service,
    negotiation_service,
    order_service,
    quote_service,
    session_service,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_product(product_id="ctv-premium-sports", base_cpm=35.0, floor_cpm=28.0):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id=product_id,
        name="Premium CTV - Sports",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=base_cpm,
        floor_cpm=floor_cpm,
        minimum_impressions=100000,
    )


def _make_catalog(products=None):
    products = products or {p.product_id: p for p in [_make_product()]}
    return {
        "products": products,
        "inventory_types": sorted({p.inventory_type for p in products.values()}),
    }


def _make_buyer_context(buyer_tier="agency", agency_id="agency-1"):
    from ad_seller.interfaces.api.deps import _build_buyer_context

    return _build_buyer_context(buyer_tier=buyer_tier, agency_id=agency_id)


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.delete = AsyncMock(side_effect=lambda k: store.pop(k, None))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage.get_order = AsyncMock(side_effect=lambda oid: store.get(f"order:{oid}"))
    storage.set_order = AsyncMock(
        side_effect=lambda oid, data: store.__setitem__(f"order:{oid}", data)
    )
    storage.get_negotiation = AsyncMock(side_effect=lambda pid: store.get(f"negotiation:{pid}"))
    storage.set_negotiation = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"negotiation:{pid}", data)
    )
    storage.get_proposal = AsyncMock(side_effect=lambda pid: store.get(f"proposal:{pid}"))
    storage.get_product = AsyncMock(side_effect=lambda pid: store.get(f"product:{pid}"))
    storage.list_sessions = AsyncMock(return_value=[])
    storage._store = store
    return storage


# =============================================================================
# catalog_service
# =============================================================================


class TestCatalogService:
    def test_catalog_is_cached_with_stable_product_ids(self):
        """Happy: repeated reads return the SAME catalog object and ids."""
        catalog_service.reset_catalog_cache()
        first = catalog_service.get_static_product_catalog()
        second = catalog_service.get_static_product_catalog()

        assert first is second
        assert len(first["products"]) == len(catalog_service.DEFAULT_PRODUCT_CONFIGS)
        assert set(first["inventory_types"]) == {
            p["inventory_type"] for p in catalog_service.DEFAULT_PRODUCT_CONFIGS
        }
        catalog_service.reset_catalog_cache()

    def test_reset_generates_fresh_product_ids(self):
        """Edge: resetting the cache regenerates product ids."""
        catalog_service.reset_catalog_cache()
        ids_before = set(catalog_service.get_static_product_catalog()["products"])
        catalog_service.reset_catalog_cache()
        ids_after = set(catalog_service.get_static_product_catalog()["products"])

        assert ids_before.isdisjoint(ids_after)
        catalog_service.reset_catalog_cache()

    def test_flow_consumes_the_same_default_product_data(self):
        """The ONE catalog source: ProductSetupFlow has no duplicate list."""
        import inspect

        from ad_seller.flows import product_setup_flow

        source = inspect.getsource(product_setup_flow.ProductSetupFlow.create_default_products)
        assert "DEFAULT_PRODUCT_CONFIGS" in source
        # The old duplicated literal is gone.
        assert '"Premium Display - Homepage"' not in source


# =============================================================================
# quote_service
# =============================================================================


class TestQuoteService:
    async def test_create_quote_happy_path(self, mock_storage):
        """Happy: PD quote is priced, persisted with TTL, and recorded."""
        request = AsyncMock()
        request.product_id = "ctv-premium-sports"
        request.deal_type = "PD"
        request.impressions = 5_000_000
        request.flight_start = None
        request.flight_end = None
        request.target_cpm = None

        context = _make_buyer_context()
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(request, context, _make_catalog())

        assert quote["quote_id"].startswith("qt-")
        assert quote["status"] == "available"
        assert quote["deal_type"] == "PD"
        assert quote["pricing"]["final_cpm"] > 0
        mock_storage.set_quote.assert_awaited()
        _, kwargs = mock_storage.set_quote.await_args
        assert kwargs.get("ttl") == 86400

    async def test_create_quote_rejects_invalid_deal_type(self, mock_storage):
        """Edge: unknown deal type -> 400 invalid_deal_type."""
        request = AsyncMock()
        request.deal_type = "ZZ"

        with pytest.raises(HTTPException) as exc:
            await quote_service.create_quote(request, _make_buyer_context(), _make_catalog())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_deal_type"

    async def test_get_quote_expired_returns_410(self, mock_storage):
        """Edge: lazily-expired quote -> 410 and status flipped to expired."""
        expired = {
            "quote_id": "qt-old",
            "status": "available",
            "expires_at": (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z",
        }
        mock_storage._store["quote:qt-old"] = expired

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await quote_service.get_quote("qt-old")

        assert exc.value.status_code == 410
        assert mock_storage._store["quote:qt-old"]["status"] == "expired"


# =============================================================================
# deal_service
# =============================================================================


def _available_quote(quote_id="qt-test123456"):
    return {
        "quote_id": quote_id,
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
            "rationale": "test",
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


class TestDealService:
    async def test_book_deal_happy_path(self, mock_storage):
        """Happy: booking an available quote mints a deal and books the quote."""
        quote = _available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        request = AsyncMock()
        request.quote_id = quote["quote_id"]
        request.audience_plan = None

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.book_deal(request)

        assert deal["deal_id"].startswith("DEMO-")
        assert deal["status"] == "proposed"
        assert deal["quote_id"] == quote["quote_id"]
        assert mock_storage._store[f"quote:{quote['quote_id']}"]["status"] == "booked"
        assert mock_storage._store[f"deal:{deal['deal_id']}"]["deal_id"] == deal["deal_id"]

    async def test_book_deal_conflict_when_already_booked(self, mock_storage):
        """Edge: booking a non-available quote -> 409 quote_already_booked."""
        quote = _available_quote()
        quote["status"] = "booked"
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        request = AsyncMock()
        request.quote_id = quote["quote_id"]
        request.audience_plan = None

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await deal_service.book_deal(request)

        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "quote_already_booked"

    def test_deterministic_score_and_labels_are_stable(self):
        """Edge: scoring helpers are deterministic and bucket correctly."""
        s1 = deal_service.deterministic_score("urn:test:audience")
        s2 = deal_service.deterministic_score("urn:test:audience")
        assert s1 == s2
        assert 0.0 <= s1 <= 1.0
        assert deal_service.agentic_match_quality(0.9) == "STRONG"
        assert deal_service.agentic_match_quality(0.1) == "POOR"
        assert deal_service.booking_match_label(0.1) == "NONE"


# =============================================================================
# order_service
# =============================================================================


class TestOrderService:
    async def test_create_and_transition_order_happy_path(self, mock_storage):
        """Happy: create -> draft, then a valid draft->submitted transition."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            order = await order_service.create_order(deal_id="DEMO-1", metadata={"k": "v"})
            assert order["status"] == "draft"
            assert order["deal_id"] == "DEMO-1"

            result = await order_service.transition_order(
                order["order_id"], "submitted", actor="test", reason="go"
            )

        assert result["status"] == "submitted"
        assert result["transition"]["to_status"] == "submitted"
        assert "allowed_next" in result
        # Extra fields survive the round-trip through the state machine.
        stored = mock_storage._store[f"order:{order['order_id']}"]
        assert stored["deal_id"] == "DEMO-1"
        assert stored["metadata"] == {"k": "v"}

    async def test_invalid_transition_returns_409_with_allowed(self, mock_storage):
        """Edge: draft->completed is rejected with allowed_transitions listed."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            order = await order_service.create_order()

            with pytest.raises(HTTPException) as exc:
                await order_service.transition_order(order["order_id"], "completed")

        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "invalid_transition"
        assert "submitted" in exc.value.detail["allowed_transitions"]
        assert "cancelled" in exc.value.detail["allowed_transitions"]


# =============================================================================
# negotiation_service
# =============================================================================


class TestNegotiationService:
    async def test_counter_proposal_starts_negotiation(self, mock_storage):
        """Happy: first counter creates a negotiation, records round 1."""
        mock_storage._store["proposal:prop-1"] = {"product_id": "prod-1"}
        mock_storage._store["product:prod-1"] = {"base_cpm": 20.0, "floor_cpm": 10.0}

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            result = await negotiation_service.counter_proposal(
                "prop-1", buyer_price=15.0, buyer_context=_make_buyer_context()
            )

        assert result["round_number"] == 1
        assert result["negotiation_id"]
        assert result["buyer_price"] == 15.0
        assert "negotiation:prop-1" in mock_storage._store

    async def test_counter_proposal_unknown_proposal_404(self, mock_storage):
        """Edge: countering a nonexistent proposal -> 404."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await negotiation_service.counter_proposal(
                    "prop-missing", buyer_price=1.0, buyer_context=_make_buyer_context()
                )
        assert exc.value.status_code == 404


# =============================================================================
# approval_service
# =============================================================================


class TestApprovalService:
    async def test_list_pending_approvals_empty(self, mock_storage):
        """Happy: with no pending index, returns an empty list."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            result = await approval_service.list_pending_approvals()
        assert result == {"approvals": []}

    async def test_get_approval_not_found(self, mock_storage):
        """Edge: unknown approval id -> 404."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await approval_service.get_approval("apr-missing")
        assert exc.value.status_code == 404


# =============================================================================
# session_service
# =============================================================================


class TestSessionService:
    async def test_list_sessions_empty(self, mock_storage):
        """Happy: no stored sessions -> empty list."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            result = await session_service.list_sessions()
        assert result == {"sessions": []}

    async def test_get_session_not_found(self, mock_storage):
        """Edge: unknown session id -> 404."""
        mock_storage.get_session = AsyncMock(return_value=None)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await session_service.get_session("sess-missing")
        assert exc.value.status_code == 404
