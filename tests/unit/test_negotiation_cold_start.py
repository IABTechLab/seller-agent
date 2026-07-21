# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Cold-start negotiation reachability (bead ar-alut).

The seller's REST negotiation surface was unreachable cold:

1. ``submit_proposal`` never persisted the proposal via
   ``storage.set_proposal`` and ``ProposalHandlingFlow`` kept its
   ``NegotiationHistory`` in memory only, so
   ``POST /api/v1/negotiations/messages`` 404'd ("Proposal not found")
   on ANY fresh negotiation.
2. ``NegotiationMessage`` supports ``quote_id`` but quote-led opens were
   rejected with a 400.
3. ``book_deal`` struck the stored quote price, ignoring an ACCEPTED
   negotiation on that quote.

These tests pin the fixed behavior: a submitted proposal (and its
product pricing) survives to a fresh storage read so the negotiation
surface works cold; quote-led opens negotiate off the stored quote; and
booking a quote with an accepted negotiation strikes the agreed price
while the buyer's re-quote-at-agreed-price workaround stays valid.
"""

import os
import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_modern_agentic_capabilities.py (no LLM call is ever made here).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

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

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.services import deal_service, negotiation_service  # noqa: E402

pytestmark = pytest.mark.asyncio


# =============================================================================
# Helpers
# =============================================================================


def _make_product(product_id="ctv-premium-sports", base_cpm=35.0, floor_cpm=20.0):
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


def _proposal_request(product_id="ctv-premium-sports", price=25.0):
    request = MagicMock()
    request.product_id = product_id
    request.deal_type = "preferred_deal"
    request.price = price
    request.impressions = 1_000_000
    request.start_date = "2026-08-01"
    request.end_date = "2026-08-31"
    request.buyer_id = "buyer-1"
    return request


def _available_quote(quote_id="qt-neg123456", product_id="ctv-premium-sports"):
    return {
        "quote_id": quote_id,
        "status": "available",
        "deal_type": "PD",
        "product": {
            "product_id": product_id,
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
            "rationale": "Base price: $35.00 CPM | Agency tier: -15% | Final: $28.26",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-08-01",
            "flight_end": "2026-08-31",
            "guaranteed": False,
        },
        "buyer_tier": "agency",
        "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


def _accepted_negotiation(proposal_id, product_id="ctv-premium-sports", agreed=24.0):
    return {
        "negotiation_id": "neg-agreed01",
        "proposal_id": proposal_id,
        "product_id": product_id,
        "buyer_tier": "agency",
        "strategy": "collaborative",
        "limits": {
            "max_rounds": 5,
            "per_round_concession_cap": 0.05,
            "total_concession_cap": 0.15,
            "gap_split_buyer_share": 0.5,
        },
        "base_price": 29.75,
        "floor_price": 20.0,
        "rounds": [
            {
                "round_number": 1,
                "buyer_price": 22.0,
                "seller_price": 26.0,
                "action": "counter",
                "rationale": "Counter at $26.00",
                "timestamp": datetime.utcnow().isoformat(),
            },
            {
                "round_number": 2,
                "buyer_price": agreed,
                "seller_price": agreed,
                "action": "accept",
                "rationale": "Deal accepted.",
                "timestamp": datetime.utcnow().isoformat(),
            },
        ],
        "status": "accepted",
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": datetime.utcnow().isoformat(),
        "package_id": None,
    }


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
    storage.get_proposal = AsyncMock(side_effect=lambda pid: store.get(f"proposal:{pid}"))
    storage.set_proposal = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"proposal:{pid}", data)
    )
    storage.get_product = AsyncMock(side_effect=lambda pid: store.get(f"product:{pid}"))
    storage.set_product = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"product:{pid}", data)
    )
    storage.get_negotiation = AsyncMock(side_effect=lambda pid: store.get(f"negotiation:{pid}"))
    storage.set_negotiation = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"negotiation:{pid}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _counter_flow_mock():
    """A ProposalHandlingFlow mock that recommends a counter."""
    flow = MagicMock()
    flow.handle_proposal_async = AsyncMock(
        return_value={
            "recommendation": "counter",
            "status": "counter_pending",
            "counter_terms": {"proposed_price": 29.75},
        }
    )
    return flow


# =============================================================================
# (1) Proposal persistence — the REST negotiation surface reachable cold
# =============================================================================


class TestSubmitProposalPersistence:
    async def test_submit_proposal_persists_proposal_record(self, mock_storage):
        """submit_proposal must persist the proposal via storage.set_proposal."""
        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=_counter_flow_mock()),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            result = await negotiation_service.submit_proposal(
                _proposal_request(), _make_buyer_context(), _make_catalog()
            )

        proposal_id = result["proposal_id"]
        stored = mock_storage._store.get(f"proposal:{proposal_id}")
        assert stored is not None, "proposal was not persisted via set_proposal"
        assert stored["product_id"] == "ctv-premium-sports"
        assert stored["price"] == 25.0

    async def test_submit_proposal_persists_product_pricing(self, mock_storage):
        """The proposal's product pricing must be persisted so a cold counter
        can price the negotiation (storage.get_product must hit)."""
        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=_counter_flow_mock()),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            await negotiation_service.submit_proposal(
                _proposal_request(), _make_buyer_context(), _make_catalog()
            )

        product = mock_storage._store.get("product:ctv-premium-sports")
        assert product is not None, "product pricing was not persisted"
        assert product["base_cpm"] == 35.0
        assert product["floor_cpm"] == 20.0

    async def test_submit_proposal_persists_flow_negotiation_history(self, mock_storage):
        """When the flow opened a NegotiationHistory for its counter, that
        history must be persisted — not kept in memory only."""
        history = {
            "negotiation_id": "neg-flow0001",
            "proposal_id": "will-be-overwritten",
            "product_id": "ctv-premium-sports",
            "buyer_tier": "agency",
            "strategy": "moderate",
            "limits": {
                "max_rounds": 5,
                "per_round_concession_cap": 0.05,
                "total_concession_cap": 0.15,
            },
            "base_price": 29.75,
            "floor_price": 20.0,
            "rounds": [
                {
                    "round_number": 1,
                    "buyer_price": 25.0,
                    "seller_price": 28.0,
                    "action": "counter",
                    "rationale": "Counter at $28.00",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            ],
            "status": "active",
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "package_id": None,
        }
        flow = _counter_flow_mock()
        flow.handle_proposal_async = AsyncMock(
            return_value={
                "recommendation": "counter",
                "status": "counter_pending",
                "counter_terms": {"proposed_price": 28.0},
                "_negotiation_history": history,
            }
        )

        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            result = await negotiation_service.submit_proposal(
                _proposal_request(), _make_buyer_context(), _make_catalog()
            )

        proposal_id = result["proposal_id"]
        stored = mock_storage._store.get(f"negotiation:{proposal_id}")
        assert stored is not None, "flow NegotiationHistory was not persisted"
        assert stored["rounds"][0]["seller_price"] == 28.0
        # The private payload must not leak onto the API response dict.
        assert "_negotiation_history" not in result

    async def test_flow_counter_exposes_negotiation_history(self):
        """ProposalHandlingFlow must surface the NegotiationHistory it built
        for counter_terms so the service can persist it."""
        from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow
        from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

        review = ProposalReviewOutput(
            decision=ProposalDecision.COUNTER,
            rationale="counter it",
            counter_price_cpm=30.0,
            audience_summary="no audience targeting supplied",
        )
        crew_result = MagicMock()
        crew_result.pydantic = review
        crew = MagicMock()
        crew.kickoff_async = AsyncMock(return_value=crew_result)

        with (
            patch(
                "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                return_value=crew,
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.emit_event",
                new_callable=AsyncMock,
            ),
        ):
            flow = ProposalHandlingFlow()
            result = await flow.handle_proposal_async(
                proposal_id="prop-flowtest",
                proposal_data={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "preferred_deal",
                    "price": 25.0,
                    "impressions": 1_000_000,
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-31",
                },
                buyer_context=_make_buyer_context(),
                products={"ctv-premium-sports": _make_product()},
            )

        assert result["recommendation"] == "counter"
        history = result.get("_negotiation_history")
        assert history is not None, "flow did not expose its NegotiationHistory"
        assert history["proposal_id"] == "prop-flowtest"
        assert history["status"] == "active"
        assert len(history["rounds"]) == 1
        # The exposed history must be consistent with the returned counter_terms.
        assert history["negotiation_id"] == result["counter_terms"]["negotiation_id"]

    async def test_cold_start_rest_negotiation_end_to_end(self, client, mock_storage):
        """THE bug: submit a proposal, then POST /api/v1/negotiations/messages
        for it — must succeed (was a 404 'Proposal not found')."""
        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=_counter_flow_mock()),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_make_catalog(),
            ),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with client as c:
                submit = await c.post(
                    "/proposals",
                    json={
                        "product_id": "ctv-premium-sports",
                        "deal_type": "preferred_deal",
                        "price": 25.0,
                        "impressions": 1000000,
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-31",
                        "buyer_id": "buyer-1",
                        "agency_id": "agency-1",
                    },
                )
                assert submit.status_code == 200, submit.text
                proposal_id = submit.json()["proposal_id"]

                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-cold-1",
                        "action": "counter",
                        "proposal_id": proposal_id,
                        "buyer_price": {"amount_micros": 25_000_000, "currency": "USD"},
                    },
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["negotiation_id"]
        assert body["round"]["action"] in ("counter", "accept", "final_offer")
        # And the negotiation survived to storage for the next round.
        assert f"negotiation:{proposal_id}" in mock_storage._store


# =============================================================================
# (2) Quote-led negotiation opens
# =============================================================================


class TestQuoteLedNegotiation:
    async def test_counter_proposal_falls_back_to_stored_quote(self, mock_storage):
        """A negotiation keyed by a stored quote_id must open off the quote's
        product instead of 404ing."""
        quote = _available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "base_cpm": 35.0,
            "floor_cpm": 20.0,
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            result = await negotiation_service.counter_proposal(
                quote["quote_id"], buyer_price=25.0, buyer_context=_make_buyer_context()
            )

        assert result["round_number"] == 1
        assert result["negotiation_id"]
        assert f"negotiation:{quote['quote_id']}" in mock_storage._store

    async def test_counter_proposal_unknown_key_still_404(self, mock_storage):
        """No proposal AND no quote for the key -> still a 404."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await negotiation_service.counter_proposal(
                    "nothing-here", buyer_price=10.0, buyer_context=_make_buyer_context()
                )
        assert exc.value.status_code == 404

    async def test_quote_led_open_via_rest_surface(self, client, mock_storage):
        """POST /api/v1/negotiations/messages with ONLY quote_id must open a
        negotiation (was a 400 unsupported_capability)."""
        quote = _available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "base_cpm": 35.0,
            "floor_cpm": 20.0,
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-quote-led-1",
                        "action": "counter",
                        "quote_id": quote["quote_id"],
                        "buyer_price": {"amount_micros": 25_000_000, "currency": "USD"},
                    },
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["negotiation_id"]
        assert f"negotiation:{quote['quote_id']}" in mock_storage._store


# =============================================================================
# (2b) Terminal moves must be recorded on the stored negotiation
# =============================================================================


class TestTerminalMovePersistence:
    async def test_buyer_accept_persists_accepted_status(self, client, mock_storage):
        """A buyer 'accept' must flip the STORED negotiation to accepted —
        otherwise booking can never see the agreed state."""
        quote = _available_quote()
        neg = _accepted_negotiation(quote["quote_id"])
        # Make it an active, mid-flight negotiation with one seller counter.
        neg["status"] = "active"
        neg["rounds"] = neg["rounds"][:1]
        neg["completed_at"] = None
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        mock_storage._store[f"negotiation:{quote['quote_id']}"] = neg

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-accept-1",
                        "action": "accept",
                        "quote_id": quote["quote_id"],
                    },
                )

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "accepted"
        stored = mock_storage._store[f"negotiation:{quote['quote_id']}"]
        assert stored["status"] == "accepted"
        # Accepting the seller's last counter strikes at that price.
        assert stored["rounds"][-1]["action"] == "accept"
        assert stored["rounds"][-1]["seller_price"] == 26.0


# =============================================================================
# (3) Booking honors ACCEPTED negotiation state
# =============================================================================


class TestBookingHonorsNegotiation:
    async def test_book_deal_strikes_agreed_price(self, mock_storage):
        """Booking a quote with an ACCEPTED negotiation must strike the
        agreed price, not the stale quoted price."""
        quote = _available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        mock_storage._store[f"negotiation:{quote['quote_id']}"] = _accepted_negotiation(
            quote["quote_id"], agreed=24.0
        )

        request = MagicMock()
        request.quote_id = quote["quote_id"]
        request.audience_plan = None

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.book_deal(request)

        assert deal["pricing"]["final_cpm"] == 24.0
        assert deal["openrtb_params"]["bidfloor"] == 24.0

    async def test_book_deal_without_negotiation_unchanged(self, mock_storage):
        """The buyer's re-quote-at-agreed-price workaround stays valid: a
        quote with NO negotiation books at the quoted price."""
        quote = _available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        request = MagicMock()
        request.quote_id = quote["quote_id"]
        request.audience_plan = None

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.book_deal(request)

        assert deal["pricing"]["final_cpm"] == 28.26
        assert deal["openrtb_params"]["bidfloor"] == 28.26

    async def test_book_deal_active_negotiation_books_quote_price(self, mock_storage):
        """A still-active (not accepted) negotiation must NOT change booking."""
        quote = _available_quote()
        neg = _accepted_negotiation(quote["quote_id"])
        neg["status"] = "active"
        neg["rounds"] = neg["rounds"][:1]
        neg["completed_at"] = None
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        mock_storage._store[f"negotiation:{quote['quote_id']}"] = neg

        request = MagicMock()
        request.quote_id = quote["quote_id"]
        request.audience_plan = None

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.book_deal(request)

        assert deal["pricing"]["final_cpm"] == 28.26
