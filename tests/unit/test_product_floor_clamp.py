# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Discounts must never take the price below the product's own floor.

`PricingRulesEngine.calculate_price` used to clamp only to
`TieredPricingConfig.global_floor_cpm` (default $1.00) and was not even
passed the product's `floor_cpm`. So a product declaring "I will not go
below $14" could be quoted to an advertiser-tier buyer at $12.75.

The tell was that the engine would then reject its own quoted price:
`is_price_acceptable(12.75, product_floor=14.0)` returns False with "Below
product floor ($14.0 CPM)". Two neighbouring paths already respected the
product floor (`rate_card_service` clamps an override up to it,
`is_price_acceptable` checks it), so the discount waterfall was the outlier.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.pricing_tiers import TieredPricingConfig
from ad_seller.services import quote_service

BASE_CPM = 15.0
HIGH_FLOOR = 14.0  # above the 15% advertiser-discounted price of 12.75
LOW_FLOOR = 10.0  # below it, so the discount applies untouched
DISCOUNTED_CPM = 12.75
IMPRESSIONS = 1_666_666  # below every volume-discount threshold


def _engine():
    return PricingRulesEngine(TieredPricingConfig(seller_organization_id="default"))


def _advertiser_context():
    from ad_seller.models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity

    context = BuyerContext(
        identity=BuyerIdentity(
            seat_id="seat-meridian",
            agency_id="agency-meridian",
            advertiser_id="adv-meridian",
        ),
        is_authenticated=True,
    )
    assert context.effective_tier == AccessTier.ADVERTISER
    return context


def _make_product(floor_cpm):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        description="Premium CTV sports inventory",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=BASE_CPM,
        floor_cpm=floor_cpm,
        minimum_impressions=100_000,
    )


def _catalog(product):
    return {
        "products": {product.product_id: product},
        "inventory_types": [product.inventory_type],
    }


def _request():
    request = MagicMock()
    request.product_id = "ctv-premium-sports"
    request.deal_type = "PD"
    request.impressions = IMPRESSIONS
    request.flight_start = None
    request.flight_end = None
    request.target_cpm = None
    return request


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage._store = store
    return storage


class TestEngineRespectsTheProductFloor:
    def test_discount_is_clamped_up_to_the_product_floor(self):
        decision = _engine().calculate_price(
            product_id="ctv-premium-sports",
            base_price=BASE_CPM,
            buyer_context=_advertiser_context(),
            volume=IMPRESSIONS,
            product_floor=HIGH_FLOOR,
        )

        assert decision.final_price == HIGH_FLOOR
        assert any("Floor enforced" in rule for rule in decision.applied_rules)

    def test_a_floor_below_the_discount_leaves_the_discount_alone(self):
        """No over-clamping: the floor is a minimum, not a target."""
        decision = _engine().calculate_price(
            product_id="ctv-premium-sports",
            base_price=BASE_CPM,
            buyer_context=_advertiser_context(),
            volume=IMPRESSIONS,
            product_floor=LOW_FLOOR,
        )

        assert decision.final_price == DISCOUNTED_CPM

    def test_no_product_floor_falls_back_to_the_global_floor(self):
        """Plenty of products declare no floor; those behave as before."""
        decision = _engine().calculate_price(
            product_id="ctv-premium-sports",
            base_price=BASE_CPM,
            buyer_context=_advertiser_context(),
            volume=IMPRESSIONS,
            product_floor=None,
        )

        assert decision.final_price == DISCOUNTED_CPM

    def test_the_higher_of_the_two_floors_wins(self):
        """A product floor under the global backstop must not lower it."""
        global_floor = TieredPricingConfig.model_fields["global_floor_cpm"].default
        decision = _engine().calculate_price(
            product_id="cheap-remnant",
            base_price=global_floor,
            buyer_context=_advertiser_context(),
            volume=IMPRESSIONS,
            product_floor=global_floor / 2,
        )

        assert decision.final_price >= global_floor


class TestRuleOverridesAreAlsoFloored:
    """A rule's `base_price_override` is subject to the floor too.

    Pinning this deliberately rather than leaving it as a side effect. The
    clamp sits after the rule-override branch in `calculate_price`, so an
    operator rule saying "charge exactly $8" on a product with a $14 floor
    now yields $14. That is consistent with `rate_card_service`, which
    already clamps a rate card override UP to the product floor, and with
    the floor being a floor: the product declares a price it will not go
    below, and an override is not an exemption from it.

    It is also the behaviour an untested clamp would have introduced
    silently, which is exactly how the `target_cpm` defect survived. If the
    intent ever changes to "an explicit operator override wins over the
    floor", this test is the thing that has to change with it.
    """

    @staticmethod
    def _config_with_override(override_price):
        from ad_seller.models.pricing_tiers import PricingRule

        return TieredPricingConfig(
            seller_organization_id="default",
            rules=[
                PricingRule(
                    rule_id="rule-override-1",
                    rule_name="Flat override for this product",
                    product_ids=["ctv-premium-sports"],
                    base_price_override=override_price,
                )
            ],
        )

    def test_override_below_the_product_floor_is_clamped_up(self):
        engine = PricingRulesEngine(self._config_with_override(8.0))

        decision = engine.calculate_price(
            product_id="ctv-premium-sports",
            base_price=BASE_CPM,
            buyer_context=_advertiser_context(),
            volume=IMPRESSIONS,
            product_floor=HIGH_FLOOR,
        )

        assert decision.final_price == HIGH_FLOOR
        assert any("Price override" in rule for rule in decision.applied_rules)
        assert any("Floor enforced" in rule for rule in decision.applied_rules)

    def test_override_above_the_product_floor_is_untouched(self):
        """The override still wins wherever the floor does not bite."""
        engine = PricingRulesEngine(self._config_with_override(22.0))

        decision = engine.calculate_price(
            product_id="ctv-premium-sports",
            base_price=BASE_CPM,
            buyer_context=_advertiser_context(),
            volume=IMPRESSIONS,
            product_floor=HIGH_FLOOR,
        )

        assert decision.final_price == 22.0


class TestEngineNeverQuotesAPriceItWouldReject:
    """The invariant the old code broke, stated directly.

    `calculate_price` and `is_price_acceptable` are two halves of the same
    engine. A price the first produces must be one the second accepts, or the
    seller contradicts itself.
    """

    @pytest.mark.parametrize("product_floor", [None, LOW_FLOOR, DISCOUNTED_CPM, HIGH_FLOOR, 20.0])
    def test_quoted_price_is_acceptable_to_the_same_engine(self, product_floor):
        engine = _engine()
        decision = engine.calculate_price(
            product_id="ctv-premium-sports",
            base_price=BASE_CPM,
            buyer_context=_advertiser_context(),
            volume=IMPRESSIONS,
            product_floor=product_floor,
        )

        acceptable, reason = engine.is_price_acceptable(
            offered_price=decision.final_price,
            product_floor=product_floor if product_floor is not None else 0.0,
            buyer_context=_advertiser_context(),
        )

        assert acceptable, (
            f"engine quoted ${decision.final_price:.2f} against a "
            f"${product_floor} product floor and then rejected it: {reason}"
        )


class TestQuoteServiceRespectsTheProductFloor:
    async def test_quote_is_not_priced_below_the_product_floor(self, mock_storage):
        product = _make_product(floor_cpm=HIGH_FLOOR)

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(
                _request(), _advertiser_context(), _catalog(product)
            )

        assert quote["pricing"]["final_cpm"] == HIGH_FLOOR
        assert quote["pricing"]["final_cpm"] >= product.floor_cpm

    async def test_quote_keeps_the_full_discount_when_the_floor_is_low(self, mock_storage):
        product = _make_product(floor_cpm=LOW_FLOOR)

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(
                _request(), _advertiser_context(), _catalog(product)
            )

        assert quote["pricing"]["final_cpm"] == DISCOUNTED_CPM
