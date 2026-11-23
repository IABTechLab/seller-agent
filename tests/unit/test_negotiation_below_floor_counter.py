"""A below-floor offer must not reveal the seller's floor (#149).

Countering at the floor handed a lowballing buyer the reserve in one round,
and countering at ``floor + (last - floor) * share`` with a fixed per-tier
share is just as invertible: ``floor = (c1 - share * quoted) / (1 - share)``
from one counter, or ``(c2 - share * c1) / (1 - share)`` from two. A
below-floor offer now takes the same capped gap-split as any other offer,
using the buyer's actual price, and the result is clamped at the floor. The
counters follow the quoted price and the concession caps; the floor shows
only once that capped path actually reaches it.

The products are the six from the #149 review, quoted at the agency (10%)
and advertiser (15%) tier prices.
"""

import asyncio
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

from ad_seller.engines.negotiation_engine import NegotiationEngine  # noqa: E402
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine  # noqa: E402
from ad_seller.engines.yield_optimizer import YieldOptimizer  # noqa: E402
from ad_seller.models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity  # noqa: E402
from ad_seller.models.negotiation import (  # noqa: E402
    STRATEGY_LIMITS,
    TIER_STRATEGY_MAP,
    NegotiationAction,
)
from ad_seller.models.pricing_tiers import TieredPricingConfig  # noqa: E402

LOWBALL = 1.00
# Gap shares the previous below-floor formula kept (agency, advertiser).
OLD_SHARES = (0.5, 0.35)

# (tier, product, list price, floor, quoted tier price)
REVIEW_PRODUCTS = [
    ("agency", "premium-display", 15.0, 10.0, 13.50),
    ("agency", "pre-roll-video", 25.0, 18.0, 22.50),
    ("agency", "ctv-premium", 35.0, 28.0, 31.50),
    ("agency", "nbc-primetime", 55.0, 40.0, 49.50),
    ("advertiser", "ctv-premium", 35.0, 28.0, 29.75),
    ("advertiser", "nbc-primetime", 55.0, 40.0, 46.75),
]
# One capped step from the advertiser CTV quote (29.75 - 6% = 27.965) already
# crosses its floor, so its first counter IS the floor; see
# test_floor_reached_in_one_capped_step.
FLOOR_BELOW_FIRST_TWO_STEPS = [p for p in REVIEW_PRODUCTS if p[:2] != ("advertiser", "ctv-premium")]
# Products whose floor sits below the whole capped path for the tier: here an
# honest just-above-floor offer is also held to the per-round cap.
FLOOR_BELOW_CAPPED_PATH = [
    ("agency", "premium-display", 15.0, 10.0, 13.50),
    ("agency", "pre-roll-video", 25.0, 18.0, 22.50),
    ("agency", "nbc-primetime", 55.0, 40.0, 49.50),
]


def _ids(products):
    return [f"{tier}-{name}" for tier, name, *_ in products]


def _engine():
    return NegotiationEngine(
        PricingRulesEngine(TieredPricingConfig(seller_organization_id="test")),
        MagicMock(spec=YieldOptimizer),
    )


def _buyer(tier):
    if tier == "advertiser":
        identity = BuyerIdentity(agency_id="a1", advertiser_id="adv1")
    else:
        identity = BuyerIdentity(agency_id="a1")
    return BuyerContext(identity=identity, is_authenticated=True)


def _negotiate(tier, list_price, floor, offer, rounds=None):
    """Offer the same price every round; return (history, rounds played).

    Stops at the first terminal action (accept / reject).
    """
    engine = _engine()
    buyer = _buyer(tier)
    history = engine.start_negotiation(
        proposal_id="p1",
        product_id="prod1",
        buyer_context=buyer,
        base_price=list_price,
        floor_price=floor,
    )
    played = []
    for _ in range(rounds or history.limits.max_rounds):
        rnd = engine.evaluate_buyer_offer(history, buyer_price=offer, buyer_context=buyer)
        history = engine.record_round(history, rnd)
        played.append(rnd)
        if rnd.action in (NegotiationAction.ACCEPT, NegotiationAction.REJECT):
            break
    return history, played


def _engine_limits(tier):
    return STRATEGY_LIMITS[TIER_STRATEGY_MAP[AccessTier(tier)]]


def _prices(rounds):
    return [r.seller_price for r in rounds]


@pytest.mark.parametrize("product", REVIEW_PRODUCTS, ids=_ids(REVIEW_PRODUCTS))
def test_quoted_tier_price_is_the_negotiation_anchor(product):
    tier, _, list_price, floor, quoted = product
    history, _ = _negotiate(tier, list_price, floor, LOWBALL, rounds=1)
    assert history.base_price == pytest.approx(quoted)


class TestFloorNotRecoverable:
    @pytest.mark.parametrize(
        "product", FLOOR_BELOW_FIRST_TWO_STEPS, ids=_ids(FLOOR_BELOW_FIRST_TWO_STEPS)
    )
    def test_first_two_lowball_counters_carry_no_floor_information(self, product):
        """The first two lowball counters are one and two per-round steps
        down from the quote, unchanged under any lower floor; the first one
        is also what an honest just-above-floor offer gets."""
        tier, _, list_price, floor, quoted = product
        _, lowball = _negotiate(tier, list_price, floor, LOWBALL, rounds=2)
        c1, c2 = _prices(lowball)

        step = quoted * _engine_limits(tier).per_round_concession_cap
        assert c1 == round(quoted - step, 2)
        assert c2 == round(c1 - step, 2)

        _, honest = _negotiate(tier, list_price, floor, floor + 0.01, rounds=1)
        assert honest[0].seller_price == c1
        for other_floor in (floor - 1.0, floor / 2):
            _, other = _negotiate(tier, list_price, other_floor, LOWBALL, rounds=2)
            assert _prices(other) == [c1, c2]

        for share in OLD_SHARES:
            assert (c1 - share * quoted) / (1 - share) != pytest.approx(floor, abs=0.01)
            assert (c2 - share * c1) / (1 - share) != pytest.approx(floor, abs=0.01)

    @pytest.mark.parametrize("product", REVIEW_PRODUCTS, ids=_ids(REVIEW_PRODUCTS))
    def test_floor_shows_only_once_the_capped_path_reaches_it(self, product):
        tier, _, list_price, floor, quoted = product
        limits = _engine_limits(tier)
        _, played = _negotiate(tier, list_price, floor, LOWBALL)
        previous = quoted
        for rnd in played:
            capped = max(
                previous - quoted * limits.per_round_concession_cap,
                quoted * (1 - limits.total_concession_cap),
            )
            if rnd.seller_price == floor:
                assert capped < floor + 0.005, (
                    f"counter sits on the floor {floor} although the capped path is at {capped:.3f}"
                )
            else:
                assert rnd.seller_price > floor
            previous = rnd.seller_price

    def test_floor_reached_in_one_capped_step(self):
        """Advertiser CTV: 29.75 less one 6% step is 27.965, under the 28.00
        floor, so the seller has genuinely conceded down to it in round 1."""
        _, played = _negotiate("advertiser", 35.0, 28.0, LOWBALL, rounds=1)
        assert played[0].action == NegotiationAction.COUNTER
        assert played[0].seller_price == 28.0


class TestLowballDoesNotBeatHonestOffer:
    def test_review_table_premium_display(self):
        """Agency Premium Display (quoted 13.50, floor 10.00), three rounds:
        the lowball used to get 11.75 -> 10.88 -> 10.44 against the honest
        12.82 -> 12.14 -> 11.47."""
        _, honest = _negotiate("agency", 15.0, 10.0, 10.01, rounds=3)
        _, lowball = _negotiate("agency", 15.0, 10.0, LOWBALL, rounds=3)

        assert _prices(honest) == [12.82, 12.14, 11.47]
        assert _prices(lowball) == [12.82, 12.14, 11.47]
        assert lowball[-1].action == NegotiationAction.FINAL_OFFER

    @pytest.mark.parametrize("product", FLOOR_BELOW_CAPPED_PATH, ids=_ids(FLOOR_BELOW_CAPPED_PATH))
    def test_lowball_never_below_honest_round_by_round(self, product):
        """Where the floor is below the tier's capped path, the lowball and
        the honest just-above-floor offer get the very same counters."""
        tier, _, list_price, floor, _ = product
        _, honest = _negotiate(tier, list_price, floor, floor + 0.01)
        _, lowball = _negotiate(tier, list_price, floor, LOWBALL)

        assert len(lowball) == len(honest) >= 3
        for n, (lo, hi) in enumerate(zip(lowball, honest), start=1):
            assert lo.seller_price >= hi.seller_price, f"round {n}"
            assert (lo.seller_price, lo.action) == (hi.seller_price, hi.action), f"round {n}"

    @pytest.mark.parametrize("product", REVIEW_PRODUCTS, ids=_ids(REVIEW_PRODUCTS))
    def test_lowball_path_respects_both_concession_caps(self, product):
        tier, _, list_price, floor, quoted = product
        limits = _engine_limits(tier)
        history, played = _negotiate(tier, list_price, floor, LOWBALL)

        assert len(played) >= 3
        previous = quoted
        for n, rnd in enumerate(played, start=1):
            assert previous - rnd.seller_price <= (
                quoted * limits.per_round_concession_cap + 0.005
            ), f"round {n} dropped {previous - rnd.seller_price:.2f}"
            # The engine rounds the total-cap final offer to the cent.
            assert rnd.seller_price >= (
                max(floor, quoted * (1 - limits.total_concession_cap)) - 0.005
            )
            assert rnd.seller_price >= floor
            assert rnd.cumulative_concession_pct <= limits.total_concession_cap + 1e-9
            previous = rnd.seller_price


class TestRepeatedLowballsStepFromLastCounter:
    def test_each_lowball_counter_steps_down_from_the_previous_one(self):
        """Agency Premium Display: 13.50 -> 12.82 -> 12.14, one 0.675 step at
        a time from the seller's last counter, not from the quoted price."""
        _, played = _negotiate("agency", 15.0, 10.0, LOWBALL, rounds=2)
        c1, c2 = _prices(played)
        assert c1 == 12.82
        assert c2 == 12.14
        assert c2 == round(c1 - 13.50 * 0.05, 2)

    def test_lowball_after_an_honest_round_continues_from_that_counter(self):
        engine = _engine()
        buyer = _buyer("agency")
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=buyer,
            base_price=15.0,
            floor_price=10.0,
        )
        # Above-floor opener close to the quote: gap-split 13.50 - 0.25.
        first = engine.evaluate_buyer_offer(history, buyer_price=13.0, buyer_context=buyer)
        assert first.seller_price == 13.25
        history = engine.record_round(history, first)

        second = engine.evaluate_buyer_offer(history, buyer_price=LOWBALL, buyer_context=buyer)

        assert second.seller_price == round(13.25 - 13.50 * 0.05, 2)


class TestProposalFlowSharesEngineBehaviour:
    """POST /proposals answers a below-floor price exactly as the engine
    (and so /api/v1/negotiations/messages and chat) does."""

    @staticmethod
    def _flow_counter(price):
        from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow
        from ad_seller.interfaces.api.deps import _build_buyer_context
        from ad_seller.models.core import DealType, PricingModel
        from ad_seller.models.flow_state import ProductDefinition

        product = ProductDefinition(
            product_id="premium-display",
            name="Premium Display",
            inventory_type="display",
            supported_deal_types=[DealType.PREFERRED_DEAL],
            supported_pricing_models=[PricingModel.CPM],
            base_cpm=15.0,
            floor_cpm=10.0,
            minimum_impressions=100000,
        )
        crew = MagicMock()
        crew.kickoff_async = AsyncMock(side_effect=RuntimeError("no crew in tests"))

        async def _go():
            with (
                patch(
                    "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                    return_value=crew,
                ),
                patch(
                    "ad_seller.flows.proposal_handling_flow.get_settings",
                    return_value=SimpleNamespace(proposal_flow_time_budget_seconds=0.0),
                ),
                patch(
                    "ad_seller.flows.proposal_handling_flow.emit_event",
                    new_callable=AsyncMock,
                ),
            ):
                return await ProposalHandlingFlow().handle_proposal_async(
                    proposal_id="prop-below-floor",
                    proposal_data={
                        "product_id": "premium-display",
                        "deal_type": "preferred_deal",
                        "price": price,
                        "impressions": 500_000,
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-31",
                    },
                    buyer_context=_build_buyer_context(buyer_tier="agency", agency_id="agency-1"),
                    products={"premium-display": product},
                )

        result = asyncio.new_event_loop().run_until_complete(_go())
        assert result["recommendation"] == "counter"
        return result["counter_terms"]["proposed_price"]

    def test_flow_counter_matches_engine_counter(self):
        _, engine_rounds = _negotiate("agency", 15.0, 10.0, LOWBALL, rounds=1)
        assert engine_rounds[0].seller_price == 12.82

        assert self._flow_counter(LOWBALL) == engine_rounds[0].seller_price
        assert self._flow_counter(9.0) == engine_rounds[0].seller_price


def test_blank_legacy_share_setting_does_not_break_startup(monkeypatch):
    """The removed NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE may linger, blank,
    in an operator's environment; Settings must still load."""
    from ad_seller.config.settings import Settings

    monkeypatch.setenv("NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE", "")
    settings = Settings()
    assert not hasattr(settings, "negotiation_below_floor_counter_share")
