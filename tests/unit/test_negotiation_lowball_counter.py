# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""ALL below-floor openers must COUNTER, not terminally REJECT
(universalized by).

The live S2 proof (docs/reports/S2_NEGOTIATION_LIVE_PROOF in agent_range)
showed the buyer's Stage 3.5 only fires when its target is below the
seller's floor — and its opening proposal equals that target. The seller
rejected every below-floor opener with no counter, so live negotiation
could structurally never converge.

Policy (pinned here; approved spec change that removed the former
0.75x deep-lowball walk-away threshold):

- EVERY below-floor offer, no matter how low, is countered on the same
  capped gap-split path as any other offer, never below the floor (#149:
  countering AT the floor handed the buyer the seller's reserve). Expected
  live shape (agency, 35 list → 31.50, floor 28): buyer opens 25 → counter
  29.93 (one 5% per-round step); buyer accepts 29.93. Extreme lowballs
  (10 vs floor 80; 1 vs floor 28) counter identically.
- Nonpositive offers are not valid prices and are rejected.
- Bounded: repeated identical lowballs terminate within the strategy's
  round bound (counter → … → final_offer → reject), no counter loops.
- Offers >= floor keep their existing accept/counter semantics unchanged.
- Both the crew path and the deterministic fallback produce the counter
  (a crew "reject" of any below-floor offer upgrades to counter), and the
  persisted NegotiationHistory records the counter round so the buyer's
  next message continues the same negotiation (continuation intact).
"""

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_negotiation_cold_start.py (no LLM call is ever made here).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

# Stub execution_activation_flow (cancel-scope leak on ad-server
# connection failure, unresolved -- issue #60 part 2).
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

from ad_seller.engines.negotiation_engine import NegotiationEngine  # noqa: E402
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine  # noqa: E402
from ad_seller.engines.yield_optimizer import YieldOptimizer  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity  # noqa: E402
from ad_seller.models.negotiation import NegotiationAction  # noqa: E402
from ad_seller.models.pricing_tiers import TieredPricingConfig  # noqa: E402
from ad_seller.services import negotiation_service  # noqa: E402

# =============================================================================
# Fixtures / helpers
# =============================================================================

# Below-floor counter for the agency buyer on the 35/28 product: 35.00 list
# -> 31.50 agency price, minus one 5% per-round step (1.575) -> 29.925.
LOWBALL_COUNTER = 29.93


@pytest.fixture
def engine():
    config = TieredPricingConfig(seller_organization_id="test-seller")
    return NegotiationEngine(PricingRulesEngine(config=config), MagicMock(spec=YieldOptimizer))


@pytest.fixture
def public_buyer():
    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


@pytest.fixture
def agency_buyer():
    return BuyerContext(
        identity=BuyerIdentity(agency_id="a1", agency_name="Agency"),
        is_authenticated=True,
    )


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


def _make_buyer_context(buyer_tier="agency", agency_id="agency-1"):
    from ad_seller.interfaces.api.deps import _build_buyer_context

    return _build_buyer_context(buyer_tier=buyer_tier, agency_id=agency_id)


def _proposal_data(price=25.0, product_id="ctv-premium-sports"):
    return {
        "product_id": product_id,
        "deal_type": "preferred_deal",
        "price": price,
        "impressions": 1_000_000,
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    }


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
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
# (1) Engine policy: below-floor openers counter on the capped path
# =============================================================================


def _agency_100_80(engine, buyer):
    # Agency tier: 100 list -> 90.00 tier price; 5% per-round cap = 4.50.
    return engine.start_negotiation(
        proposal_id="p1",
        product_id="prod1",
        buyer_context=buyer,
        base_price=100.0,
        floor_price=80.0,
    )


class TestLowballCounterPolicy:
    def test_below_floor_opener_counters_one_capped_step(self, engine, agency_buyer):
        """A near-floor lowball must COUNTER (one capped step down), not
        REJECT, and not at the floor."""
        history = _agency_100_80(engine, agency_buyer)
        rnd = engine.evaluate_buyer_offer(history, buyer_price=75.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == 85.5

    def test_counter_round_keeps_negotiation_active(self, engine, agency_buyer):
        """Recording the below-floor counter must keep the history active —
        the buyer's next message continues the same negotiation."""
        history = _agency_100_80(engine, agency_buyer)
        rnd = engine.evaluate_buyer_offer(history, buyer_price=75.0, buyer_context=agency_buyer)
        history = engine.record_round(history, rnd)
        assert history.status == "active"
        assert len(history.rounds) == 1
        assert history.rounds[0].seller_price == 85.5

    def test_live_shape_open_25_floor_28_counter_then_accept(self, engine, agency_buyer):
        """The expected live shape: buyer opens 25 against floor 28 →
        counter 29.93; buyer then meets the counter → ACCEPT."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="ctv-premium",
            buyer_context=agency_buyer,
            base_price=35.0,
            floor_price=28.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=25.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == LOWBALL_COUNTER
        history = engine.record_round(history, rnd)
        assert history.status == "active"

        rnd2 = engine.evaluate_buyer_offer(
            history, buyer_price=LOWBALL_COUNTER, buyer_context=agency_buyer
        )
        assert rnd2.action == NegotiationAction.ACCEPT
        assert rnd2.seller_price == LOWBALL_COUNTER
        history = engine.record_round(history, rnd2)
        assert history.status == "accepted"

    def test_extreme_lowball_10_vs_80_counters_like_a_near_floor_offer(self, engine, agency_buyer):
        """Spec change per (was test_deep_lowball_still_rejects):
        even a $10 offer against an $80 floor is countered, and gets the
        same counter as a $75 offer."""
        history = _agency_100_80(engine, agency_buyer)
        rnd = engine.evaluate_buyer_offer(history, buyer_price=10.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == 85.5

    def test_extreme_lowball_1_vs_28_is_countered(self, engine, agency_buyer):
        """Spec change per (was test_boundary_offer_is_countered,
        which pinned the removed 0.75x threshold): a $1 offer against a $28
        floor is countered."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="ctv-premium",
            buyer_context=agency_buyer,
            base_price=35.0,
            floor_price=28.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=1.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == LOWBALL_COUNTER

    def test_at_or_above_floor_semantics_unchanged(self, engine, agency_buyer):
        """Offers >= floor keep the existing gap-split counter semantics."""
        history = _agency_100_80(engine, agency_buyer)
        # Gap-split of (90 - 80) at 50% buyer share = 5.00, capped at 4.50.
        rnd = engine.evaluate_buyer_offer(history, buyer_price=80.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == 85.5

    def test_zero_or_negative_offer_rejects(self, engine, agency_buyer):
        history = _agency_100_80(engine, agency_buyer)
        rnd = engine.evaluate_buyer_offer(history, buyer_price=0.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.REJECT


def _public_100_80(engine, buyer):
    # Public tier: 100 list, 3% per-round cap, 8% total cap -> 92 at most.
    return engine.start_negotiation(
        proposal_id="p1",
        product_id="prod1",
        buyer_context=buyer,
        base_price=100.0,
        floor_price=80.0,
    )


class TestLowballRoundBound:
    """Repeated identical lowballs must terminate within the round bound."""

    @pytest.mark.parametrize("buyer_price", [75.0, 10.0])
    def test_repeated_lowball_terminates_within_bound(self, engine, public_buyer, buyer_price):
        """PUBLIC/AGGRESSIVE (max 3 rounds): counter, counter, final_offer,
        then reject — no counter loop, and every move stays on the capped
        path (97, 94, then the 8% total-cap final offer at 92)."""
        history = _public_100_80(engine, public_buyer)
        max_rounds = history.limits.max_rounds
        actions = []
        # Drive one more evaluation than the bound allows
        for _ in range(max_rounds + 1):
            rnd = engine.evaluate_buyer_offer(
                history, buyer_price=buyer_price, buyer_context=public_buyer
            )
            history = engine.record_round(history, rnd)
            actions.append(rnd.action)
            if rnd.action == NegotiationAction.REJECT:
                break

        assert actions == [
            NegotiationAction.COUNTER,
            NegotiationAction.COUNTER,
            NegotiationAction.FINAL_OFFER,
            NegotiationAction.REJECT,
        ]
        assert history.status == "rejected"
        assert [r.seller_price for r in history.rounds[:-1]] == [97.0, 94.0, 92.0]

    def test_final_offer_can_still_be_accepted(self, engine, public_buyer):
        """After the final offer the buyer meeting it still ACCEPTs."""
        history = _public_100_80(engine, public_buyer)
        for _ in range(history.limits.max_rounds):
            rnd = engine.evaluate_buyer_offer(history, buyer_price=75.0, buyer_context=public_buyer)
            history = engine.record_round(history, rnd)
        assert rnd.action == NegotiationAction.FINAL_OFFER

        rnd = engine.evaluate_buyer_offer(
            history, buyer_price=rnd.seller_price, buyer_context=public_buyer
        )
        assert rnd.action == NegotiationAction.ACCEPT


# =============================================================================
# (2) Flow paths: deterministic fallback AND crew both produce the counter
# =============================================================================


def _run_flow(crew_behavior, price=25.0):
    """Run ProposalHandlingFlow with the review crew stubbed.

    crew_behavior: "raise" (crew fails → deterministic fallback) or a
    ProposalReviewOutput to return (crew path).
    """
    from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow

    crew = MagicMock()
    if crew_behavior == "raise":
        crew.kickoff_async = AsyncMock(side_effect=RuntimeError("no crew in tests"))
    else:
        crew_result = MagicMock()
        crew_result.pydantic = crew_behavior
        crew.kickoff_async = AsyncMock(return_value=crew_result)

    async def _go():
        with (
            patch(
                "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                return_value=crew,
            ),
            # budget 0 = unlimited: these tests pin BOTH evaluator paths, so
            # the stubbed crew must actually run (the default 20s budget is
            # below proposal_crew_min_budget_seconds and would skip it).
            patch(
                "ad_seller.flows.proposal_handling_flow.get_settings",
                return_value=SimpleNamespace(proposal_flow_time_budget_seconds=0.0),
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.emit_event",
                new_callable=AsyncMock,
            ),
        ):
            flow = ProposalHandlingFlow()
            return await flow.handle_proposal_async(
                proposal_id="prop-lowball",
                proposal_data=_proposal_data(price=price),
                buyer_context=_make_buyer_context(),
                products={"ctv-premium-sports": _make_product()},
            )

    import asyncio

    return asyncio.new_event_loop().run_until_complete(_go())


class TestFlowPathsProduceCounter:
    def test_fallback_path_counters_below_floor(self):
        """Crew failure → deterministic fallback: below-floor opener yields
        counter_terms above the floor plus a persistable history."""
        result = _run_flow("raise", price=25.0)

        assert result["recommendation"] == "counter"
        assert result["counter_terms"] is not None
        assert result["counter_terms"]["proposed_price"] == LOWBALL_COUNTER
        assert result["counter_terms"]["action"] == "counter"

        history = result.get("_negotiation_history")
        assert history is not None, "counter round was not exposed for persistence"
        assert history["status"] == "active"
        assert history["rounds"][0]["seller_price"] == LOWBALL_COUNTER
        assert history["rounds"][0]["action"] == "counter"

    def test_crew_reject_of_counterable_lowball_is_countered(self):
        """Crew path: even when the crew says reject for a below-floor-but-
        credible opener, the seller must counter."""
        from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

        review = ProposalReviewOutput(
            decision=ProposalDecision.REJECT,
            rationale="price below floor",
            rejection_reason="below floor",
            audience_summary="no audience targeting supplied",
        )
        result = _run_flow(review, price=25.0)

        assert result["recommendation"] == "counter"
        assert result["counter_terms"] is not None
        assert result["counter_terms"]["proposed_price"] == LOWBALL_COUNTER
        history = result.get("_negotiation_history")
        assert history is not None
        assert history["status"] == "active"

    def test_crew_reject_of_deep_lowball_upgrades_to_counter(self):
        """Spec change per (was
        test_crew_reject_of_deep_lowball_stays_rejected): a crew reject of
        ANY below-floor offer — even $10 against a $28 floor — upgrades to
        a counter."""
        from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

        review = ProposalReviewOutput(
            decision=ProposalDecision.REJECT,
            rationale="price far below floor",
            rejection_reason="not credible",
            audience_summary="no audience targeting supplied",
        )
        result = _run_flow(review, price=10.0)

        assert result["recommendation"] == "counter"
        assert result["counter_terms"] is not None
        assert result["counter_terms"]["proposed_price"] == LOWBALL_COUNTER
        history = result.get("_negotiation_history")
        assert history is not None
        assert history["status"] == "active"

    def test_crew_accept_unchanged(self):
        """>= floor crew accepts stay accepted (existing semantics pinned)."""
        from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

        review = ProposalReviewOutput(
            decision=ProposalDecision.ACCEPT,
            rationale="good price",
            audience_summary="no audience targeting supplied",
        )
        result = _run_flow(review, price=35.0)

        assert result["recommendation"] == "accept"


# =============================================================================
# (3) Wire truth: the counter surfaces on the REST negotiation surface and
#     the persisted history supports cold continuation to convergence
# =============================================================================


class TestRestSurface:
    @pytest.mark.asyncio
    async def test_messages_endpoint_counters_below_floor_open(self, client, mock_storage):
        """POST /api/v1/negotiations/messages: a below-floor open must come
        back as a counter above the floor (28), not a terminal reject."""
        mock_storage._store["proposal:prop-lb1"] = {
            "proposal_id": "prop-lb1",
            "product_id": "ctv-premium-sports",
        }
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "base_cpm": 35.0,
            "floor_cpm": 28.0,
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-lowball-1",
                        "action": "counter",
                        "proposal_id": "prop-lb1",
                        "buyer_price": {"amount_micros": 25_000_000, "currency": "USD"},
                    },
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "active"
        assert body["round"]["action"] == "counter"
        # Keyless caller -> PUBLIC tier: 35.00 minus one 3% per-round step.
        assert body["round"]["seller_price"]["amount_micros"] == 33_950_000
        # Counter round persisted for the buyer's next message
        stored = mock_storage._store["negotiation:prop-lb1"]
        assert stored["status"] == "active"
        assert stored["rounds"][-1]["action"] == "counter"
        assert stored["rounds"][-1]["seller_price"] == 33.95

    @pytest.mark.asyncio
    async def test_cold_continuation_buyer_accepts_lowball_counter(self, mock_storage):
        """Round 2 off the PERSISTED history: buyer meets the counter
        and the stored negotiation converges to accepted."""
        mock_storage._store["proposal:prop-lb2"] = {
            "proposal_id": "prop-lb2",
            "product_id": "ctv-premium-sports",
        }
        mock_storage._store["product:ctv-premium-sports"] = {
            "product_id": "ctv-premium-sports",
            "base_cpm": 35.0,
            "floor_cpm": 28.0,
        }

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            r1 = await negotiation_service.counter_proposal(
                "prop-lb2", buyer_price=25.0, buyer_context=_make_buyer_context()
            )
            assert r1["action"] == "counter"
            assert r1["seller_price"] == LOWBALL_COUNTER
            assert r1["status"] == "active"

            r2 = await negotiation_service.counter_proposal(
                "prop-lb2", buyer_price=LOWBALL_COUNTER, buyer_context=_make_buyer_context()
            )

        assert r2["action"] == "accept"
        assert r2["seller_price"] == LOWBALL_COUNTER
        assert r2["status"] == "accepted"
        assert r2["negotiation_id"] == r1["negotiation_id"]
        assert r2["round_number"] == 2
        stored = mock_storage._store["negotiation:prop-lb2"]
        assert stored["status"] == "accepted"
