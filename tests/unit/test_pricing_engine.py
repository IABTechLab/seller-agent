# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for PricingRulesEngine — extends coverage beyond test_engines.py.

Focuses on:
- Tier comparison (agency < advertiser discount)
- Floor price enforcement
- Volume discount tiers
- Rate card / price display
"""

import pytest

from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity
from ad_seller.models.core import PricingModel
from ad_seller.models.pricing_tiers import PricingRule, TieredPricingConfig


@pytest.fixture
def engine():
    config = TieredPricingConfig(seller_organization_id="test-seller")
    return PricingRulesEngine(config=config)


@pytest.fixture
def seat_buyer_context():
    identity = BuyerIdentity(seat_id="seat-001")
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def agency_buyer_context():
    identity = BuyerIdentity(
        agency_id="agency-001",
        agency_name="Test Agency",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def advertiser_buyer_context():
    identity = BuyerIdentity(
        agency_id="agency-001",
        agency_name="Test Agency",
        advertiser_id="adv-001",
        advertiser_name="Test Advertiser",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


class TestTierComparison:
    """Agency tier gets better pricing than seat tier;
    advertiser tier gets the best discount."""

    def test_agency_better_than_seat(self, engine, seat_buyer_context, agency_buyer_context):
        base = 20.0
        seat_result = engine.calculate_price(
            product_id="p1", base_price=base, buyer_context=seat_buyer_context
        )
        agency_result = engine.calculate_price(
            product_id="p1", base_price=base, buyer_context=agency_buyer_context
        )
        # Agency discount >= seat discount → lower final price
        assert agency_result.final_price <= seat_result.final_price
        assert agency_result.tier_discount >= seat_result.tier_discount

    def test_advertiser_best_discount(
        self, engine, seat_buyer_context, agency_buyer_context, advertiser_buyer_context
    ):
        base = 20.0
        seat = engine.calculate_price(
            product_id="p1", base_price=base, buyer_context=seat_buyer_context
        )
        agency = engine.calculate_price(
            product_id="p1", base_price=base, buyer_context=agency_buyer_context
        )
        adv = engine.calculate_price(
            product_id="p1", base_price=base, buyer_context=advertiser_buyer_context
        )
        assert adv.final_price <= agency.final_price <= seat.final_price
        assert adv.tier_discount >= agency.tier_discount >= seat.tier_discount


class TestFloorPriceEnforcement:
    """CPM never goes below the global floor."""

    def test_floor_enforced_with_very_low_base(self, engine, advertiser_buyer_context):
        result = engine.calculate_price(
            product_id="p1",
            base_price=0.50,
            buyer_context=advertiser_buyer_context,
            volume=100_000_000,
        )
        assert result.final_price >= engine.config.global_floor_cpm

    def test_floor_enforced_even_without_buyer(self, engine):
        result = engine.calculate_price(
            product_id="p1",
            base_price=0.10,
            volume=50_000_000,
        )
        assert result.final_price >= engine.config.global_floor_cpm

    def test_floor_rule_appears_in_applied_rules(self, engine, advertiser_buyer_context):
        result = engine.calculate_price(
            product_id="p1",
            base_price=0.50,
            buyer_context=advertiser_buyer_context,
            volume=100_000_000,
        )
        assert any("Floor enforced" in r for r in result.applied_rules)


class TestVolumeDiscount:
    """Volume discount applies for large impression counts."""

    def test_no_volume_discount_below_threshold(self, engine, advertiser_buyer_context):
        result = engine.calculate_price(
            product_id="p1",
            base_price=20.0,
            buyer_context=advertiser_buyer_context,
            volume=100_000,
        )
        assert result.volume_discount == 0.0

    def test_5m_volume_discount(self, engine, advertiser_buyer_context):
        result = engine.calculate_price(
            product_id="p1",
            base_price=20.0,
            buyer_context=advertiser_buyer_context,
            volume=5_000_000,
        )
        assert result.volume_discount == 0.05

    def test_10m_volume_discount(self, engine, advertiser_buyer_context):
        result = engine.calculate_price(
            product_id="p1",
            base_price=20.0,
            buyer_context=advertiser_buyer_context,
            volume=10_000_000,
        )
        assert result.volume_discount == 0.10

    def test_50m_volume_discount(self, engine, advertiser_buyer_context):
        result = engine.calculate_price(
            product_id="p1",
            base_price=20.0,
            buyer_context=advertiser_buyer_context,
            volume=50_000_000,
        )
        assert result.volume_discount == 0.20

    def test_volume_discount_stacks_with_tier(self, engine, advertiser_buyer_context):
        no_vol = engine.calculate_price(
            product_id="p1",
            base_price=20.0,
            buyer_context=advertiser_buyer_context,
            volume=100_000,
        )
        with_vol = engine.calculate_price(
            product_id="p1",
            base_price=20.0,
            buyer_context=advertiser_buyer_context,
            volume=10_000_000,
        )
        assert with_vol.final_price < no_vol.final_price


class TestRateCardAndPriceDisplay:
    """Rate card entries and price display are respected."""

    def test_public_gets_range_display(self, engine):
        display = engine.get_price_display(base_price=20.0)
        assert display["type"] == "range"
        assert "low" in display
        assert "high" in display

    def test_agency_gets_exact_display(self, engine, agency_buyer_context):
        display = engine.get_price_display(base_price=20.0, buyer_context=agency_buyer_context)
        assert display["type"] == "exact"
        assert "price" in display

    def test_pricing_decision_has_correct_model(self, engine, agency_buyer_context):
        result = engine.calculate_price(
            product_id="p1",
            base_price=20.0,
            buyer_context=agency_buyer_context,
        )
        assert result.pricing_model == PricingModel.CPM
        assert result.currency == "USD"


class TestPriceOverrideRules:
    """A rule's base_price_override is supposed to be the final word (per
    docs/guides/pricing-rules.md: "takes precedence and stops further rule
    evaluation"), not a starting point another rule's discount can still
    chip away at.

    Regression coverage for issue #53: a higher-priority discount-only
    rule was leaving its discount in a shared accumulator that got applied
    to the override price after the loop had already moved past it.
    """

    def test_override_alone_replaces_base_price(self, engine, agency_buyer_context):
        engine.config.rules = [
            PricingRule(
                rule_id="r-override",
                rule_name="Negotiated flat rate",
                access_tier=AccessTier.AGENCY,
                base_price_override=50.0,
            ),
        ]
        result = engine.calculate_price(
            product_id="p1", base_price=100.0, buyer_context=agency_buyer_context
        )
        assert result.final_price == 50.0

    def test_override_wins_even_when_outranked_by_a_discount_rule(
        self, engine, agency_buyer_context
    ):
        """The bug: a higher-priority discount-only rule gets evaluated
        first (rules are sorted by priority), leaving its discount in the
        loop's shared accumulator. Once the lower-priority override rule
        is reached and the loop breaks, that stale discount must not be
        applied on top of it."""
        engine.config.rules = [
            PricingRule(
                rule_id="r-discount",
                rule_name="Agency-wide discount",
                priority=100,
                access_tier=AccessTier.AGENCY,
                discount_percentage=0.20,
            ),
            PricingRule(
                rule_id="r-override",
                rule_name="Negotiated flat rate",
                priority=50,
                access_tier=AccessTier.AGENCY,
                base_price_override=50.0,
            ),
        ]
        result = engine.calculate_price(
            product_id="p1", base_price=100.0, buyer_context=agency_buyer_context
        )
        assert result.final_price == 50.0
        assert not any("Rule discount" in r for r in result.applied_rules)

    def test_override_wins_when_it_outranks_the_discount_rule(self, engine, agency_buyer_context):
        """Same two rules, priority order flipped — the override was
        already reached (and the loop broken) before the discount rule
        would ever be visited, so this direction always worked."""
        engine.config.rules = [
            PricingRule(
                rule_id="r-override",
                rule_name="Negotiated flat rate",
                priority=100,
                access_tier=AccessTier.AGENCY,
                base_price_override=50.0,
            ),
            PricingRule(
                rule_id="r-discount",
                rule_name="Agency-wide discount",
                priority=50,
                access_tier=AccessTier.AGENCY,
                discount_percentage=0.20,
            ),
        ]
        result = engine.calculate_price(
            product_id="p1", base_price=100.0, buyer_context=agency_buyer_context
        )
        assert result.final_price == 50.0

    def test_discount_only_rules_still_apply_the_highest_one(self, engine, agency_buyer_context):
        """No override in play: this path is untouched by the fix."""
        engine.config.rules = [
            PricingRule(
                rule_id="r-small",
                rule_name="Small discount",
                priority=100,
                access_tier=AccessTier.AGENCY,
                discount_percentage=0.05,
            ),
            PricingRule(
                rule_id="r-big",
                rule_name="Bigger discount",
                priority=50,
                access_tier=AccessTier.AGENCY,
                discount_percentage=0.20,
            ),
        ]
        result = engine.calculate_price(
            product_id="p1", base_price=100.0, buyer_context=agency_buyer_context
        )
        # Tier discount (10%) then the higher of the two rule discounts (20%).
        assert result.final_price == pytest.approx(100.0 * 0.9 * 0.8)
