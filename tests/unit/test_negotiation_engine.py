# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for NegotiationEngine.

Covers:
- Counter-offer respects concession limits
- Walk-away triggers when gap exceeds threshold
- Strategy varies by buyer tier
- Multi-round concession tracking (each round concedes less)
- Package-aware counter (suggest alternative package)
"""

from unittest.mock import MagicMock

import pytest

from ad_seller.engines.negotiation_engine import NegotiationEngine
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.engines.yield_optimizer import YieldOptimizer
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.negotiation import (
    NegotiationAction,
    NegotiationStrategy,
)
from ad_seller.models.pricing_tiers import TieredPricingConfig


@pytest.fixture
def pricing_engine():
    config = TieredPricingConfig(seller_organization_id="test-seller")
    return PricingRulesEngine(config=config)


@pytest.fixture
def yield_optimizer():
    return MagicMock(spec=YieldOptimizer)


@pytest.fixture
def engine(pricing_engine, yield_optimizer):
    return NegotiationEngine(pricing_engine, yield_optimizer)


@pytest.fixture
def public_buyer():
    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


@pytest.fixture
def agency_buyer():
    return BuyerContext(
        identity=BuyerIdentity(agency_id="a1", agency_name="Agency"),
        is_authenticated=True,
    )


@pytest.fixture
def advertiser_buyer():
    return BuyerContext(
        identity=BuyerIdentity(
            agency_id="a1", agency_name="Agency",
            advertiser_id="adv1", advertiser_name="Advertiser",
        ),
        is_authenticated=True,
    )


class TestConcessionLimits:
    """Counter-offer respects per-round and total concession caps."""

    def test_counter_does_not_exceed_total_cap(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=50.0,
        )
        # Run multiple rounds with a low offer
        for _ in range(history.limits.max_rounds):
            rnd = engine.evaluate_buyer_offer(history, buyer_price=60.0, buyer_context=agency_buyer)
            history = engine.record_round(history, rnd)
            if rnd.action in (NegotiationAction.ACCEPT, NegotiationAction.REJECT):
                break

        # Cumulative concession should not exceed total cap
        if history.rounds:
            last = history.rounds[-1]
            assert last.cumulative_concession_pct <= history.limits.total_concession_cap + 0.001

    def test_counter_price_above_floor(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=82.0, buyer_context=agency_buyer)
        assert rnd.seller_price >= history.floor_price


class TestWalkAway:
    """Walk-away triggers when buyer price is below floor or max rounds exceeded."""

    def test_reject_below_floor(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=agency_buyer,
            base_price=100.0,
            floor_price=80.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=50.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.REJECT
        assert "below floor" in rnd.rationale.lower()

    def test_reject_after_max_rounds(self, engine, public_buyer):
        history = engine.start_negotiation(
            proposal_id="p1",
            product_id="prod1",
            buyer_context=public_buyer,
            base_price=100.0,
            floor_price=50.0,
        )
        # Exhaust all rounds
        for _ in range(history.limits.max_rounds):
            rnd = engine.evaluate_buyer_offer(history, buyer_price=60.0, buyer_context=public_buyer)
            history = engine.record_round(history, rnd)
            if rnd.action == NegotiationAction.REJECT:
                break

        # After max rounds, the next offer should be rejected
        rnd = engine.evaluate_buyer_offer(history, buyer_price=60.0, buyer_context=public_buyer)
        assert rnd.action == NegotiationAction.REJECT


class TestStrategyByTier:
    """Strategy varies by buyer tier."""

    def test_public_gets_aggressive(self, engine, public_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=public_buyer, base_price=100.0, floor_price=50.0,
        )
        assert history.strategy == NegotiationStrategy.AGGRESSIVE
        assert history.limits.max_rounds == 3

    def test_agency_gets_collaborative(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=agency_buyer, base_price=100.0, floor_price=50.0,
        )
        assert history.strategy == NegotiationStrategy.COLLABORATIVE
        assert history.limits.max_rounds == 5

    def test_advertiser_gets_premium(self, engine, advertiser_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=advertiser_buyer, base_price=100.0, floor_price=50.0,
        )
        assert history.strategy == NegotiationStrategy.PREMIUM
        assert history.limits.max_rounds == 6

    def test_premium_concedes_more_than_aggressive(self, engine, public_buyer, advertiser_buyer):
        pub_history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=public_buyer, base_price=100.0, floor_price=50.0,
        )
        adv_history = engine.start_negotiation(
            proposal_id="p2", product_id="prod1",
            buyer_context=advertiser_buyer, base_price=100.0, floor_price=50.0,
        )
        pub_rnd = engine.evaluate_buyer_offer(pub_history, buyer_price=70.0, buyer_context=public_buyer)
        adv_rnd = engine.evaluate_buyer_offer(adv_history, buyer_price=70.0, buyer_context=advertiser_buyer)

        # Premium strategy should concede more (lower seller price)
        if pub_rnd.action == NegotiationAction.COUNTER and adv_rnd.action == NegotiationAction.COUNTER:
            assert adv_rnd.seller_price <= pub_rnd.seller_price


class TestMultiRoundConcession:
    """Each round concedes less as negotiations proceed."""

    def test_accept_when_buyer_meets_base(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=agency_buyer, base_price=100.0, floor_price=50.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=100.0, buyer_context=agency_buyer)
        assert rnd.action == NegotiationAction.ACCEPT

    def test_multi_round_seller_price_decreases(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=agency_buyer, base_price=100.0, floor_price=50.0,
        )
        seller_prices = []
        for _ in range(3):
            rnd = engine.evaluate_buyer_offer(history, buyer_price=65.0, buyer_context=agency_buyer)
            history = engine.record_round(history, rnd)
            seller_prices.append(rnd.seller_price)
            if rnd.action in (NegotiationAction.ACCEPT, NegotiationAction.REJECT):
                break

        # Seller should be conceding (prices going down or staying same)
        for i in range(1, len(seller_prices)):
            assert seller_prices[i] <= seller_prices[i - 1]

    def test_record_round_updates_status_on_accept(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=agency_buyer, base_price=100.0, floor_price=50.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=200.0, buyer_context=agency_buyer)
        history = engine.record_round(history, rnd)
        assert history.status == "accepted"

    def test_record_round_updates_status_on_reject(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=agency_buyer, base_price=100.0, floor_price=80.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=10.0, buyer_context=agency_buyer)
        history = engine.record_round(history, rnd)
        assert history.status == "rejected"


class TestAlternativePackages:
    """Suggest alternative packages when negotiation stalls."""

    def test_suggest_packages_within_budget(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=agency_buyer, base_price=100.0, floor_price=50.0,
        )
        rnd = engine.evaluate_buyer_offer(history, buyer_price=60.0, buyer_context=agency_buyer)
        history = engine.record_round(history, rnd)

        packages = [
            {"package_id": "pkg-cheap", "base_price": 55.0},
            {"package_id": "pkg-mid", "base_price": 65.0},
            {"package_id": "pkg-expensive", "base_price": 200.0},
        ]
        suggestions = engine.suggest_alternative_packages(history, packages)
        assert "pkg-cheap" in suggestions
        assert "pkg-mid" in suggestions
        # 200 is way above buyer budget * 1.1
        assert "pkg-expensive" not in suggestions

    def test_suggest_empty_for_no_rounds(self, engine, agency_buyer):
        history = engine.start_negotiation(
            proposal_id="p1", product_id="prod1",
            buyer_context=agency_buyer, base_price=100.0, floor_price=50.0,
        )
        suggestions = engine.suggest_alternative_packages(history, [{"package_id": "pkg1", "base_price": 50.0}])
        assert suggestions == []
