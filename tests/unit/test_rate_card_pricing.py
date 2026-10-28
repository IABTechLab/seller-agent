# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""The operator rate card now drives pricing (issue #69).

Before this change, ``PUT /api/v1/rate-card`` (and the MCP
``update_rate_card`` tool) wrote a rate card that nothing in pricing ever
read: quotes, from-template bookings, and negotiation anchors priced off
the catalog product's ``base_cpm``/``floor_cpm`` directly, and the stored
card sat inert in storage.

These tests pin the fixed behavior end to end:

- a stored rate card entry matching a product's ``inventory_type``
  overrides the catalog base CPM as the starting price for quotes,
  from-template bookings, and negotiation anchors;
- the product's floor CPM still applies — an override below floor is
  clamped up to the floor, never priced under it;
- a product with no matching entry falls back to catalog pricing,
  unchanged;
- no stored rate card at all is byte-identical to pre-issue-#69 behavior
  (regression pairs alongside every override case);
- idempotent booking replay is unaffected by where the quoted price came
  from;
- ``GET /api/v1/rate-card`` distinguishes "no card ever stored" (generic
  reference defaults) from "here is the operator's actual card" via a
  ``source`` field, so the unset-state response can no longer be mistaken
  for a real stored card.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Stub execution_activation_flow (cancel-scope leak on ad-server
# connection failure, unresolved -- issue #60 part 2), same idiom as the
# other service-layer test files.
_broken_flows = [
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
from ad_seller.services import (  # noqa: E402
    deal_service,
    negotiation_service,
    quote_service,
    rate_card_service,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_product(
    product_id="ctv-premium-sports",
    inventory_type="ctv",
    base_cpm=35.0,
    floor_cpm=20.0,
):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id=product_id,
        name="Premium CTV - Sports",
        inventory_type=inventory_type,
        supported_deal_types=[DealType.PROGRAMMATIC_GUARANTEED, DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=base_cpm,
        floor_cpm=floor_cpm,
        minimum_impressions=100_000,
    )


def _make_unpriced_product(product_id="dooh-times-square", inventory_type="dooh"):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id=product_id,
        name="DOOH - Times Square",
        inventory_type=inventory_type,
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=None,
        floor_cpm=None,
        minimum_impressions=100_000,
    )


def _make_catalog(products=None):
    products = products or {p.product_id: p for p in [_make_product()]}
    return {
        "products": products,
        "inventory_types": sorted({p.inventory_type for p in products.values()}),
    }


def _public_context():
    from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity

    # Public tier applies a 0% tier discount and no matching rules, so
    # final_price == base_price for a small volume (below the volume
    # discount thresholds) — the cleanest lens onto base-price resolution.
    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


def _quote_request(product_id, impressions=200_000, deal_type="PD"):
    request = MagicMock()
    request.product_id = product_id
    request.deal_type = deal_type
    request.impressions = impressions
    request.flight_start = None
    request.flight_end = None
    request.target_cpm = None
    return request


def _template_request(product_id, impressions=200_000, deal_type="PD", max_cpm=None):
    request = MagicMock()
    request.product_id = product_id
    request.deal_type = deal_type
    request.impressions = impressions
    request.max_cpm = max_cpm
    request.flight_start = None
    request.flight_end = None
    request.notes = None
    return request


def _rate_card(entries, updated_at="2026-09-14T00:00:00Z"):
    return {"entries": entries, "updated_at": updated_at}


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


# =============================================================================
# (1) rate_card_service.resolve_base_cpm — the resolver itself
# =============================================================================


class TestResolveBaseCpm:
    async def test_matching_entry_overrides_catalog_base(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 50.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            price = await rate_card_service.resolve_base_cpm(product)

        assert price == 50.0

    async def test_override_below_floor_is_clamped_to_floor(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 5.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            price = await rate_card_service.resolve_base_cpm(product)

        assert price == 20.0

    async def test_no_matching_entry_falls_back_to_catalog(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0, inventory_type="ctv")
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "display", "base_cpm": 99.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            price = await rate_card_service.resolve_base_cpm(product)

        assert price == 35.0

    async def test_no_stored_rate_card_falls_back_to_catalog(self, mock_storage):
        """Regression pair: no rate card at all is identical to today."""
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            price = await rate_card_service.resolve_base_cpm(product)

        assert price == 35.0

    async def test_unpriced_product_still_422s_even_with_matching_entry(self, mock_storage):
        """A rate card entry overrides an existing catalog price; it does
        not manufacture one for a product the catalog declares unpriced."""
        product = _make_unpriced_product(inventory_type="dooh")
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "dooh", "base_cpm": 40.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await rate_card_service.resolve_base_cpm(product)

        assert exc.value.status_code == 422

    async def test_negative_entry_with_no_product_floor_is_clamped_not_negative(self, mock_storage):
        """Adversarial case (found in review of #78 by @Sirajmx): a product
        with no ``floor_cpm`` of its own paired with a negative rate card
        entry must not resolve to a negative price. ``_apply_override``
        used to skip the clamp entirely whenever ``floor_cpm`` was
        ``None``, so ``-5.0`` came straight through unclamped."""
        from ad_seller.models.pricing_tiers import TieredPricingConfig

        product = _make_product(base_cpm=35.0, floor_cpm=None)
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": -5.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            price = await rate_card_service.resolve_base_cpm(product)

        assert price >= 0
        assert price == TieredPricingConfig.model_fields["global_floor_cpm"].default


class TestReclassificationChangesRateCardSelection:
    """_find_entry matches by exact inventory_type string, so a
    classification change silently moves which price a product resolves
    to. Runs the real production pipeline, not a synthetic product, so a
    future classifier change that moves a booked price fails here."""

    async def test_ctv_preroll_row_prices_off_the_ctv_entry_not_display(self, mock_storage):
        """The real 'Sports Pre-Roll' row: declares inventory_type=ctv, must
        price off the "ctv" rate-card entry, not "display" or "video"."""
        from ad_seller.clients.ad_server_base import AdServerInventoryItem, AdServerType
        from ad_seller.services import catalog_service

        item = AdServerInventoryItem(
            id="inv-ctv-sports-preroll",
            name="Sports Pre-Roll :15/:30",
            sizes=[(1920, 1080)],
            ad_server_type=AdServerType.CSV,
        )
        item.__dict__["raw"] = {
            "ad_formats": ["video"],
            "inventory_type": "ctv",
            "floor_price_cpm": 28.0,
        }
        product = catalog_service.product_from_inventory_item(item)
        assert product.inventory_type == "ctv", (
            "classify_inventory_type regressed -- fix belongs in "
            "test_classify_inventory_type.py, not here"
        )

        mock_storage._store["rate_card:current"] = _rate_card(
            [
                {"inventory_type": "display", "base_cpm": 12.0},
                {"inventory_type": "video", "base_cpm": 25.0},
                {"inventory_type": "ctv", "base_cpm": 35.0},
            ]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            price = await rate_card_service.resolve_base_cpm(product)

        assert price == 35.0


# =============================================================================
# (2) Quoting — quote_service.create_quote
# =============================================================================


class TestQuoteUsesRateCard:
    async def test_matching_rate_card_overrides_quote_base_price(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        catalog = _make_catalog({product.product_id: product})
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 50.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(
                _quote_request(product.product_id), _public_context(), catalog
            )

        assert quote["pricing"]["base_cpm"] == 50.0
        assert quote["pricing"]["final_cpm"] == 50.0

    async def test_below_floor_rate_card_entry_clamps_quote_to_floor(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        catalog = _make_catalog({product.product_id: product})
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 5.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(
                _quote_request(product.product_id), _public_context(), catalog
            )

        assert quote["pricing"]["base_cpm"] == 20.0
        assert quote["pricing"]["final_cpm"] == 20.0

    async def test_no_matching_entry_quotes_catalog_price(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0, inventory_type="ctv")
        catalog = _make_catalog({product.product_id: product})
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "display", "base_cpm": 99.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(
                _quote_request(product.product_id), _public_context(), catalog
            )

        assert quote["pricing"]["base_cpm"] == 35.0

    async def test_no_rate_card_quotes_catalog_price_unchanged(self, mock_storage):
        """Regression pair: no rate card at all is identical to today."""
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        catalog = _make_catalog({product.product_id: product})

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(
                _quote_request(product.product_id), _public_context(), catalog
            )

        assert quote["pricing"]["base_cpm"] == 35.0
        assert quote["pricing"]["final_cpm"] == 35.0

    async def test_get_pricing_service_helper_honors_rate_card(self, mock_storage):
        """POST /pricing's service path (quote_service.get_pricing) also
        inherits the override — same resolver as create_quote."""
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 50.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            pricing = await quote_service.get_pricing(
                product_id=product.product_id,
                product=product,
                buyer_context=_public_context(),
                volume=0,
            )

        assert pricing["base_price"] == 50.0


# =============================================================================
# (3) From-template booking — deal_service.create_deal_from_template
# =============================================================================


class TestFromTemplateBookingUsesRateCard:
    async def test_matching_rate_card_overrides_template_price(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        catalog = _make_catalog({product.product_id: product})
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 50.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.create_deal_from_template(
                _template_request(product.product_id), _public_context(), catalog
            )

        assert deal["actual_price_cpm"] == 50.0

    async def test_no_rate_card_template_price_unchanged(self, mock_storage):
        """Regression pair: no rate card at all is identical to today."""
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        catalog = _make_catalog({product.product_id: product})

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.create_deal_from_template(
                _template_request(product.product_id), _public_context(), catalog
            )

        assert deal["actual_price_cpm"] == 35.0

    async def test_below_floor_entry_clamps_template_price_to_floor(self, mock_storage):
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        catalog = _make_catalog({product.product_id: product})
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 5.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.create_deal_from_template(
                _template_request(product.product_id), _public_context(), catalog
            )

        assert deal["actual_price_cpm"] == 20.0


# =============================================================================
# (4) Negotiation anchor — negotiation_service.counter_proposal
# =============================================================================


class TestNegotiationAnchorReflectsOverride:
    async def test_cold_start_anchor_uses_matching_rate_card_entry(self, mock_storage):
        """A negotiation opened cold (quote-led, no NegotiationHistory yet)
        must anchor at the rate card override, not the raw catalog base."""
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "inventory_type": "ctv",
            "base_cpm": 35.0,
            "floor_cpm": 20.0,
        }
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 50.0}]
        )
        mock_storage._store["quote:qt-neg1"] = {
            "quote_id": "qt-neg1",
            "product": {"product_id": "ctv-premium-sports"},
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                "qt-neg1", buyer_price=45.0, buyer_context=_public_context()
            )

        stored = mock_storage._store["negotiation:qt-neg1"]
        assert stored["base_price"] == 50.0
        assert stored["floor_price"] == 20.0

    async def test_cold_start_anchor_clamps_override_to_floor(self, mock_storage):
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "inventory_type": "ctv",
            "base_cpm": 35.0,
            "floor_cpm": 20.0,
        }
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 5.0}]
        )
        mock_storage._store["quote:qt-neg2"] = {
            "quote_id": "qt-neg2",
            "product": {"product_id": "ctv-premium-sports"},
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                "qt-neg2", buyer_price=15.0, buyer_context=_public_context()
            )

        stored = mock_storage._store["negotiation:qt-neg2"]
        assert stored["base_price"] == 20.0

    async def test_cold_start_anchor_unaffected_without_rate_card(self, mock_storage):
        """Regression pair: no rate card at all is identical to today."""
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "inventory_type": "ctv",
            "base_cpm": 35.0,
            "floor_cpm": 20.0,
        }
        mock_storage._store["quote:qt-neg3"] = {
            "quote_id": "qt-neg3",
            "product": {"product_id": "ctv-premium-sports"},
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                "qt-neg3", buyer_price=30.0, buyer_context=_public_context()
            )

        stored = mock_storage._store["negotiation:qt-neg3"]
        assert stored["base_price"] == 35.0
        assert stored["floor_price"] == 20.0

    async def test_negative_entry_with_no_product_floor_never_yields_negative_base_price(
        self, mock_storage
    ):
        """Adversarial case (found in review of #78 by @Sirajmx): quotes
        and bookings were incidentally protected from a negative override
        by ``PricingRulesEngine``'s unrelated ``global_floor_cpm``, but
        negotiation anchored directly off ``resolve_negotiation_anchor``
        with no such backstop — a negative rate card entry on a
        floor-less product reached ``NegotiationHistory.base_price``
        unclamped."""
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "inventory_type": "ctv",
            "base_cpm": 35.0,
            "floor_cpm": None,
        }
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": -5.0}]
        )
        mock_storage._store["quote:qt-neg4"] = {
            "quote_id": "qt-neg4",
            "product": {"product_id": "ctv-premium-sports"},
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                "qt-neg4", buyer_price=10.0, buyer_context=_public_context()
            )

        stored = mock_storage._store["negotiation:qt-neg4"]
        assert stored["base_price"] >= 0

    async def test_proposal_flow_recommended_price_reflects_override(self):
        """ProposalHandlingFlow.evaluate_pricing (the primary negotiation
        anchor for a freshly-submitted proposal) also inherits the
        override, and the counter it generates is anchored off it."""
        from unittest.mock import AsyncMock as _AsyncMock

        from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow
        from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

        review = ProposalReviewOutput(
            decision=ProposalDecision.COUNTER,
            rationale="counter it",
            counter_price_cpm=45.0,
            audience_summary="no audience targeting supplied",
        )
        crew_result = MagicMock()
        crew_result.pydantic = review
        crew = MagicMock()
        crew.kickoff_async = _AsyncMock(return_value=crew_result)

        store = {"rate_card:current": _rate_card([{"inventory_type": "ctv", "base_cpm": 50.0}])}
        storage = AsyncMock()
        storage.get = AsyncMock(side_effect=lambda k: store.get(k))

        with (
            patch(
                "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                return_value=crew,
            ),
            # budget 0 = unlimited: this test pins the CREW counter path, so
            # the stubbed crew must actually run (the default 20s budget is
            # below proposal_crew_min_budget_seconds and would skip it).
            patch(
                "ad_seller.flows.proposal_handling_flow.get_settings",
                return_value=SimpleNamespace(proposal_flow_time_budget_seconds=0.0),
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.emit_event",
                new_callable=_AsyncMock,
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
        ):
            flow = ProposalHandlingFlow()
            result = await flow.handle_proposal_async(
                proposal_id="prop-ratecard1",
                proposal_data={
                    "product_id": "ctv-premium-sports",
                    "deal_type": "preferred_deal",
                    "price": 25.0,
                    "impressions": 200_000,
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-31",
                },
                buyer_context=_public_context(),
                products={"ctv-premium-sports": _make_product()},
            )

        assert result["evaluation"]["recommended_price"] == 50.0


# =============================================================================
# (5) Idempotent replay is unaffected by where the price came from
# =============================================================================


class TestIdempotentReplayUnaffected:
    async def test_booking_replay_of_a_rate_card_priced_quote(self, client, mock_storage):
        """A quote priced via a rate card override books and replays
        idempotently exactly like any other quote (FD-12 untouched)."""
        product = _make_product(base_cpm=35.0, floor_cpm=20.0)
        catalog = _make_catalog({product.product_id: product})
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 50.0}]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(
                _quote_request(product.product_id, impressions=500_000),
                _public_context(),
                catalog,
            )
            assert quote["pricing"]["final_cpm"] == 50.0

            # #77 makes POST /api/v1/deals require a verified buyer key.
            # This quote is public-tier (_public_context() above), so any
            # authenticated buyer satisfies that check, same pattern as
            # #77's own test_public_tier_quote_bookable_by_any_authenticated_buyer.
            from ad_seller.models.buyer_identity import BuyerIdentity

            app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(
                identity=BuyerIdentity()
            )

            first = await client.post(
                "/api/v1/deals",
                json={"idempotency_key": "idem-ratecard-1", "quote_id": quote["quote_id"]},
            )
            second = await client.post(
                "/api/v1/deals",
                json={"idempotency_key": "idem-ratecard-1", "quote_id": quote["quote_id"]},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["deal"]["deal_id"] == second.json()["deal"]["deal_id"]
        assert first.json()["deal"]["pricing"]["base_cpm"]["amount_micros"] == 50_000_000


# =============================================================================
# (6) Read side — GET /api/v1/rate-card distinguishes "none set"
# =============================================================================


class TestRateCardReadSide:
    async def test_unset_rate_card_reports_defaults_source(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/rate-card")

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "defaults"
        assert body["updated_at"] is None
        assert len(body["entries"]) > 0

    async def test_stored_rate_card_reports_stored_source(self, client, mock_storage):
        mock_storage._store["rate_card:current"] = _rate_card(
            [{"inventory_type": "ctv", "base_cpm": 50.0}],
            updated_at="2026-09-14T12:00:00Z",
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/rate-card")

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "stored"
        assert body["updated_at"] == "2026-09-14T12:00:00Z"
        assert body["entries"][0]["base_cpm"] == 50.0

    async def test_put_response_reports_stored_source(self, client, mock_storage):
        op_key = _seed_operator_key(mock_storage._store)

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.put(
                "/api/v1/rate-card",
                json=[{"inventory_type": "ctv", "base_cpm": 40.0}],
                headers={"Authorization": f"Bearer {op_key}"},
            )

        assert resp.status_code == 200
        assert resp.json()["source"] == "stored"
        assert resp.json()["entries"][0]["base_cpm"] == 40.0


def _seed_operator_key(store):
    """Seed a valid OPERATOR-role API key; return the raw key.

    Mirrors ``tests/unit/test_operator_auth.py``'s ``_seed_key`` helper
    (same storage shape: ``ApiKeyRecord`` under the key-hash prefix, plus
    the key-id index and list ``require_operator_key`` reads).
    """
    from ad_seller.models.api_key import (
        API_KEY_INDEX_PREFIX,
        API_KEY_STORAGE_PREFIX,
        ApiKeyRecord,
        ApiKeyRole,
        generate_api_key,
        hash_api_key,
    )
    from ad_seller.models.buyer_identity import BuyerIdentity

    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id="key-ratecard-op",
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(),
        role=ApiKeyRole.OPERATOR,
        label="rate card test operator key",
    )
    store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    store[f"{API_KEY_INDEX_PREFIX}{record.key_id}"] = key_hash
    all_keys = store.get("api_key_list") or []
    all_keys.append(record.key_id)
    store["api_key_list"] = all_keys
    return raw_key
