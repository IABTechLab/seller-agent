# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Proposal evaluation grounds availability in catalog avails — and volume
shortfalls COUNTER with the available volume instead of terminal-rejecting.

The live negotiation rig proof showed two runs dying on a hardcoded
placeholder: proposal evaluation set ``available_impressions=1000000``
("would come from avails"), so any budget-derived volume above exactly 1M
(e.g. 1,142,857) tripped ``impressions_available=False`` and a terminal
REJECT before the price logic (lowball counter-at-floor) could fire.

Policy pinned here:

- Availability comes from ``catalog_service.check_avails`` — the SAME
  source of truth the quote path / POST /products/avails use (honest-
  availability: uncapped products report requested-as-available; capped
  products cap at ``maximum_impressions``). No second availability
  opinion, no fabricated 1,000,000.
- Requested volume above available with partial availability → COUNTER
  carrying the available volume (``max_impressions``) and consistent
  pricing (the NegotiationEngine's price for the buyer's offer: agreeable
  prices are kept, below-floor prices counter at the floor), with a
  truthful volume reason.
- Genuinely zero availability → terminal REJECT (unchanged).
- Requested volume within availability → semantics unchanged.
- Both evaluators normalize: a crew reject OR accept of a volume-short
  proposal becomes the volume counter (the seller cannot accept volume it
  cannot deliver); a crew accept at zero availability becomes a reject.
"""

import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_negotiation_lowball_counter.py (no LLM call is ever made here).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used in test_negotiation_lowball_counter.py.
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


# =============================================================================
# Helpers
# =============================================================================


def _make_product(
    product_id="ctv-premium-sports",
    base_cpm=35.0,
    floor_cpm=28.0,
    maximum_impressions=None,
):
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
        maximum_impressions=maximum_impressions,
    )


def _make_buyer_context(buyer_tier="agency", agency_id="agency-1"):
    from ad_seller.interfaces.api.deps import _build_buyer_context

    return _build_buyer_context(buyer_tier=buyer_tier, agency_id=agency_id)


def _proposal_data(price=35.0, impressions=1_142_857, product_id="ctv-premium-sports"):
    return {
        "product_id": product_id,
        "deal_type": "preferred_deal",
        "price": price,
        "impressions": impressions,
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    }


def _run_flow(crew_behavior, price=35.0, impressions=1_142_857, product=None):
    """Run ProposalHandlingFlow with the review crew stubbed.

    crew_behavior: "raise" (crew fails → deterministic fallback) or a
    ProposalReviewOutput to return (crew path).
    """
    from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow

    if product is None:
        product = _make_product()

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
                proposal_id="prop-avails",
                proposal_data=_proposal_data(price=price, impressions=impressions),
                buyer_context=_make_buyer_context(),
                products={product.product_id: product},
            )

    import asyncio

    return asyncio.new_event_loop().run_until_complete(_go())


def _crew_review(decision, rationale="crew opinion"):
    from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

    return ProposalReviewOutput(
        decision=ProposalDecision(decision),
        rationale=rationale,
        rejection_reason=rationale if decision == "reject" else None,
        audience_summary="no audience targeting supplied",
    )


# =============================================================================
# (1) Grounding: availability comes from check_avails, not a 1,000,000
#     placeholder — test a product whose availability differs from 1M
#     both ways.
# =============================================================================


class TestAvailabilityGrounding:
    def test_capped_below_placeholder_grounds_available(self):
        """Cap 500k (< the old 1M placeholder): evaluation must report the
        REAL 500,000 available — not 1,000,000."""
        result = _run_flow(
            "raise",
            impressions=1_142_857,
            product=_make_product(maximum_impressions=500_000),
        )
        ev = result["evaluation"]
        assert ev["available_impressions"] == 500_000
        assert ev["impressions_available"] is False

    def test_uncapped_product_reports_requested_as_available(self):
        """No capacity cap declared → requested-as-available (honest-
        availability policy, same as check_avails / the quote path).
        5M requested would have terminal-rejected under the 1M placeholder."""
        result = _run_flow(
            "raise",
            impressions=5_000_000,
            product=_make_product(maximum_impressions=None),
        )
        ev = result["evaluation"]
        assert ev["available_impressions"] == 5_000_000
        assert ev["impressions_available"] is True
        # Price 35.0 >= base → clean accept, no volume interference
        assert result["recommendation"] == "accept"

    def test_capped_above_placeholder_requested_within_cap_accepts(self):
        """Cap 2M, requested 1.5M (> the old 1M placeholder but within the
        cap): grounded availability accepts what the placeholder killed."""
        result = _run_flow(
            "raise",
            impressions=1_500_000,
            product=_make_product(maximum_impressions=2_000_000),
        )
        ev = result["evaluation"]
        assert ev["available_impressions"] == 1_500_000
        assert ev["impressions_available"] is True
        assert result["recommendation"] == "accept"


# =============================================================================
# (2) Volume shortfall is negotiable: COUNTER with the available volume.
# =============================================================================


class TestVolumeShortfallCounters:
    def test_live_shape_budget_derived_volume_counters_not_rejects(self):
        """The live rig shape: buyer's budget-derived 1,142,857 impressions
        vs 1,000,000 actually available, price agreeable → COUNTER carrying
        the available volume and the agreeable price — NOT a terminal
        reject."""
        result = _run_flow(
            "raise",
            price=35.0,
            impressions=1_142_857,
            product=_make_product(maximum_impressions=1_000_000),
        )
        assert result["recommendation"] == "counter"
        ct = result["counter_terms"]
        assert ct is not None
        assert ct["max_impressions"] == 1_000_000
        # Price was agreeable — countering on volume must not move it.
        assert ct["proposed_price"] == 35.0
        assert ct["action"] == "counter"
        # Truthful reason: names the volume shortfall.
        assert "1,000,000" in ct["reason"]
        assert "1,142,857" in ct["reason"]
        # Counter round persisted as active for the buyer's next message.
        history = result.get("_negotiation_history")
        assert history is not None
        assert history["status"] == "active"
        assert history["rounds"][-1]["action"] == "counter"

    def test_volume_shortfall_with_below_floor_price_counters_both(self):
        """Volume over available AND price below floor: one counter fixes
        both — floor price (lowball convention) + available volume."""
        result = _run_flow(
            "raise",
            price=25.0,
            impressions=1_142_857,
            product=_make_product(maximum_impressions=800_000),
        )
        assert result["recommendation"] == "counter"
        ct = result["counter_terms"]
        assert ct is not None
        assert ct["proposed_price"] == 28.0  # counter-at-floor convention
        assert ct["max_impressions"] == 800_000
        assert ct["action"] == "counter"
        history = result.get("_negotiation_history")
        assert history is not None
        assert history["status"] == "active"

    def test_zero_availability_still_terminal_rejects(self):
        """Genuinely zero availability (sold-out placement, cap 0) is the
        one volume case that stays a terminal reject."""
        result = _run_flow(
            "raise",
            price=35.0,
            impressions=500_000,
            product=_make_product(maximum_impressions=0),
        )
        assert result["recommendation"] == "reject"
        assert result["status"] == "rejected"
        assert result["counter_terms"] is None

    def test_volume_within_availability_unchanged(self):
        """Requested within the cap keeps existing semantics (accept when
        the price is agreeable)."""
        result = _run_flow(
            "raise",
            price=35.0,
            impressions=900_000,
            product=_make_product(maximum_impressions=1_000_000),
        )
        assert result["recommendation"] == "accept"
        assert result["evaluation"]["impressions_available"] is True

    def test_nonpositive_price_with_volume_shortfall_still_rejects(self):
        """A nonpositive price is not a valid offer — no volume counter can
        be built on it (engine convention: reject)."""
        result = _run_flow(
            "raise",
            price=0.0,
            impressions=1_142_857,
            product=_make_product(maximum_impressions=500_000),
        )
        assert result["recommendation"] == "reject"


# =============================================================================
# (3) Both evaluators normalize to the deterministic availability truth.
# =============================================================================


class TestCrewPathNormalization:
    def test_crew_reject_of_volume_short_proposal_counters(self):
        """Crew path: a crew reject of a volume-short (but partially
        available) proposal upgrades to the volume counter — mirror of the
        lowball reject→counter normalization."""
        result = _run_flow(
            _crew_review("reject", "volume exceeds availability"),
            price=35.0,
            impressions=1_142_857,
            product=_make_product(maximum_impressions=1_000_000),
        )
        assert result["recommendation"] == "counter"
        ct = result["counter_terms"]
        assert ct is not None
        assert ct["max_impressions"] == 1_000_000
        assert ct["proposed_price"] == 35.0

    def test_crew_accept_of_volume_short_proposal_counters(self):
        """The seller cannot ACCEPT volume it cannot deliver: a crew accept
        of a volume-short proposal is normalized to the volume counter."""
        result = _run_flow(
            _crew_review("accept", "looks good"),
            price=35.0,
            impressions=1_142_857,
            product=_make_product(maximum_impressions=1_000_000),
        )
        assert result["recommendation"] == "counter"
        ct = result["counter_terms"]
        assert ct is not None
        assert ct["max_impressions"] == 1_000_000

    def test_crew_accept_at_zero_availability_rejects(self):
        """A crew accept at genuinely zero availability is normalized to a
        terminal reject — nothing can be delivered."""
        result = _run_flow(
            _crew_review("accept", "looks good"),
            price=35.0,
            impressions=500_000,
            product=_make_product(maximum_impressions=0),
        )
        assert result["recommendation"] == "reject"

    def test_crew_accept_within_availability_unchanged(self):
        """Crew accepts of deliverable proposals stay accepted."""
        result = _run_flow(
            _crew_review("accept", "good price"),
            price=35.0,
            impressions=900_000,
            product=_make_product(maximum_impressions=1_000_000),
        )
        assert result["recommendation"] == "accept"
