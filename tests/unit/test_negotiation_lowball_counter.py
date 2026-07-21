# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Below-floor openers must COUNTER at the floor, not terminally REJECT
(bead ar-nj9m).

The live S2 proof (docs/reports/S2_NEGOTIATION_LIVE_PROOF in agent_range)
showed the buyer's Stage 3.5 only fires when its target is below the
seller's floor — and its opening proposal equals that target. The seller
rejected every below-floor opener with no counter, so live negotiation
could structurally never converge.

Fixed policy (pinned here):

- A below-floor offer within ``LOWBALL_COUNTER_FLOOR_RATIO`` of the floor
  is countered AT the floor — the seller invites the buyer up to its
  minimum viable price. Expected live shape: buyer opens 25, floor 28 →
  counter 28; buyer accepts 28.
- Deeper lowballs (below ratio × floor) remain walk-away REJECTs — the
  pre-existing, test-pinned walk-away behavior is preserved.
- Bounded: repeated identical lowballs terminate within the strategy's
  round bound (counter → … → final_offer → reject), no counter loops.
- Offers >= floor keep their existing accept/counter semantics unchanged.
- Both the crew path and the deterministic fallback produce the counter,
  and the persisted NegotiationHistory records the counter round so the
  buyer's next message continues the same negotiation (ar-alut intact).
"""

import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_negotiation_cold_start.py (no LLM call is ever made here).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used in test_negotiation_cold_start.py.
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

from ad_seller.engines.negotiation_engine import (  # noqa: E402
    LOWBALL_COUNTER_FLOOR_RATIO,
    NegotiationEngine,
)
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
# (1) Engine policy: below-floor openers counter at the floor
# =============================================================================


class TestLowballCounterPolicy:
    def test_below_floor_opener_counters_at_floor(self, engine, agency_buyer):
        """A near-floor lowball must COUNTER at the floor, not REJECT."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=75.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == 80.0

    def test_counter_round_keeps_negotiation_active(self, engine, agency_buyer):
        """Recording the floor counter must keep the history active — the
        buyer's next message continues the same negotiation."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=75.0, buyer_context=agency_buyer)
        history = engine.record_round(history, rnd)
        assert history.status == "active"
        assert len(history.rounds) == 1
        assert history.rounds[0].seller_price == 80.0

    def test_live_shape_open_25_floor_28_counter_28_then_accept(self, engine, agency_buyer):
        """The expected live shape: buyer opens 25, floor 28 → counter 28;
        buyer then offers 28 → ACCEPT at 28."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="ctv-premium",
            buyer_context=agency_buyer,
            base_price=35.0,
            floor_price=28.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=25.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == 28.0
        history = engine.record_round(history, rnd)
        assert history.status == "active"

        rnd2 = engine.evaluate_buyer_offer(history, buyer_price=28.0, buyer_context=agency_buyer)
        assert rnd2.action == NegotiationAction.ACCEPT
        assert rnd2.seller_price == 28.0
        history = engine.record_round(history, rnd2)
        assert history.status == "accepted"

    def test_deep_lowball_still_rejects(self, engine, agency_buyer):
        """Offers below ratio × floor stay walk-away REJECTs (pre-existing,
        test-pinned behavior preserved)."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        # Just under the counterable boundary
        below_boundary = 80.0 * LOWBALL_COUNTER_FLOOR_RATIO - 0.01
        rnd = engine.evaluate_buyer_offer(
            history, buyer_price=below_boundary, buyer_context=agency_buyer
        )
        assert rnd.action == NegotiationAction.REJECT
        assert "below floor" in rnd.rationale.lower()

    def test_boundary_offer_is_countered(self, engine, agency_buyer):
        """An offer exactly at ratio × floor is countered at the floor."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        rnd = engine.evaluate_buyer_offer(
            history, buyer_price=80.0 * LOWBALL_COUNTER_FLOOR_RATIO, buyer_context=agency_buyer
        )
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == 80.0

    def test_at_or_above_floor_semantics_unchanged(self, engine, agency_buyer):
        """Offers >= floor keep the existing gap-split counter semantics —
        the floor-counter branch must not fire for them."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        # Agency → COLLABORATIVE: base 100 → 85 tier-adjusted; gap-split of
        # (85 - 80) at 50% buyer share → counter 82.5, NOT the floor.
        rnd = engine.evaluate_buyer_offer(history, buyer_price=80.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price > 80.0

    def test_zero_or_negative_offer_rejects(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=0.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.REJECT


class TestLowballRoundBound:
    """Repeated identical lowballs must terminate within the round bound."""

    def test_repeated_lowball_terminates_within_bound(self, engine, public_buyer):
        """PUBLIC/AGGRESSIVE (max 3 rounds): counter, counter, final_offer,
        then reject — no counter loop."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=public_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        max_rounds = history.limits.max_rounds
        actions = []
        # Drive one more evaluation than the bound allows
        for _ in range(max_rounds + 1):
            rnd = engine.evaluate_buyer_offer(
                history, buyer_price=75.0, buyer_context=public_buyer
            )
            history = engine.record_round(history, rnd)
            actions.append(rnd.action)
            if rnd.action == NegotiationAction.REJECT:
                break

        assert actions[-1] == NegotiationAction.REJECT
        assert history.status == "rejected"
        # Terminates in at most max_rounds non-terminal moves + 1 reject
        assert len(actions) <= max_rounds + 1
        # Every non-terminal move held the floor (no drift below floor)
        for rnd in history.rounds[:-1]:
            assert rnd.seller_price == 80.0

    def test_last_allowed_round_is_final_offer(self, engine, public_buyer):
        """On the strategy's last round the floor counter is a FINAL_OFFER."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=public_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        for _ in range(history.limits.max_rounds - 1):
            rnd = engine.evaluate_buyer_offer(
                history, buyer_price=75.0, buyer_context=public_buyer
            )
            history = engine.record_round(history, rnd)
            assert rnd.action == NegotiationAction.COUNTER

        rnd = engine.evaluate_buyer_offer(history, buyer_price=75.0, buyer_context=public_buyer)
        assert rnd.action == NegotiationAction.FINAL_OFFER
        assert rnd.seller_price == 80.0

    def test_floor_counter_can_still_be_accepted_after_final_offer(self, engine, public_buyer):
        """After the final floor offer the buyer meeting it still ACCEPTs."""
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=public_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        for _ in range(history.limits.max_rounds):
            rnd = engine.evaluate_buyer_offer(
                history, buyer_price=75.0, buyer_context=public_buyer
            )
            history = engine.record_round(history, rnd)

        rnd = engine.evaluate_buyer_offer(history, buyer_price=80.0, buyer_context=public_buyer)
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
    def test_fallback_path_counters_below_floor_at_floor(self):
        """Crew failure → deterministic fallback: below-floor opener yields
        counter_terms at the floor plus a persistable history."""
        result = _run_flow("raise", price=25.0)

        assert result["recommendation"] == "counter"
        assert result["counter_terms"] is not None
        assert result["counter_terms"]["proposed_price"] == 28.0
        assert result["counter_terms"]["action"] == "counter"

        history = result.get("_negotiation_history")
        assert history is not None, "counter round was not exposed for persistence"
        assert history["status"] == "active"
        assert history["rounds"][0]["seller_price"] == 28.0
        assert history["rounds"][0]["action"] == "counter"

    def test_crew_reject_of_counterable_lowball_is_countered(self):
        """Crew path: even when the crew says reject for a below-floor-but-
        credible opener, the seller must counter at the floor."""
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
        assert result["counter_terms"]["proposed_price"] == 28.0
        history = result.get("_negotiation_history")
        assert history is not None
        assert history["status"] == "active"

    def test_crew_reject_of_deep_lowball_stays_rejected(self):
        """Deep lowballs (< ratio × floor) keep the crew's reject."""
        from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

        review = ProposalReviewOutput(
            decision=ProposalDecision.REJECT,
            rationale="price far below floor",
            rejection_reason="not credible",
            audience_summary="no audience targeting supplied",
        )
        result = _run_flow(review, price=10.0)

        assert result["recommendation"] == "reject"

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
        back as a counter at the floor (28), not a terminal reject."""
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
        assert body["round"]["seller_price"]["amount_micros"] == 28_000_000
        # Counter round persisted for the buyer's next message
        stored = mock_storage._store["negotiation:prop-lb1"]
        assert stored["status"] == "active"
        assert stored["rounds"][-1]["action"] == "counter"
        assert stored["rounds"][-1]["seller_price"] == 28.0

    @pytest.mark.asyncio
    async def test_cold_continuation_buyer_accepts_floor_counter(self, mock_storage):
        """Round 2 off the PERSISTED history: buyer meets the floor counter
        and the stored negotiation converges to accepted at 28."""
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
            assert r1["seller_price"] == 28.0
            assert r1["status"] == "active"

            r2 = await negotiation_service.counter_proposal(
                "prop-lb2", buyer_price=28.0, buyer_context=_make_buyer_context()
            )

        assert r2["action"] == "accept"
        assert r2["seller_price"] == 28.0
        assert r2["status"] == "accepted"
        assert r2["negotiation_id"] == r1["negotiation_id"]
        assert r2["round_number"] == 2
        stored = mock_storage._store["negotiation:prop-lb2"]
        assert stored["status"] == "accepted"
