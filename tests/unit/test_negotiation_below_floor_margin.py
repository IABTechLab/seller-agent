"""A below-floor offer is countered above the floor, not at it.

Countering every below-floor offer exactly at the floor told a lowballing
buyer the seller's reserve in one round. The engine now counters at
``floor + (last seller position - floor) * share``: by default the share of
the gap the tier's strategy keeps, configurable through
``NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE`` (0 restores counter-at-floor).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ad_seller.engines.negotiation_engine import NegotiationEngine
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.engines.yield_optimizer import YieldOptimizer
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.negotiation import NegotiationAction
from ad_seller.models.pricing_tiers import TieredPricingConfig

BASE = 100.0
FLOOR = 80.0


def _engine(share=None):
    return NegotiationEngine(
        PricingRulesEngine(TieredPricingConfig(seller_organization_id="test")),
        MagicMock(spec=YieldOptimizer),
        below_floor_counter_share=share,
    )


def _agency():
    return BuyerContext(identity=BuyerIdentity(agency_id="a1"), is_authenticated=True)


def _public():
    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


def _start(engine, buyer):
    return engine.start_negotiation(
        proposal_id="p1",
        product_id="prod1",
        buyer_context=buyer,
        base_price=BASE,
        floor_price=FLOOR,
    )


class TestDefaultCounterAboveFloor:
    def test_agency_lowball_counters_halfway_between_tier_price_and_floor(self):
        engine = _engine()
        history = _start(engine, _agency())  # agency price 90.00, COLLABORATIVE share 0.5

        rnd = engine.evaluate_buyer_offer(history, buyer_price=50.0, buyer_context=_agency())

        assert rnd.action == NegotiationAction.COUNTER
        assert rnd.seller_price == 85.0

    def test_public_lowball_keeps_the_aggressive_share(self):
        engine = _engine()
        history = _start(engine, _public())  # AGGRESSIVE keeps 70% of the gap

        rnd = engine.evaluate_buyer_offer(history, buyer_price=10.0, buyer_context=_public())

        assert rnd.seller_price == pytest.approx(80.0 + 20.0 * 0.7)

    def test_repeated_lowballs_approach_but_never_cross_the_floor(self):
        engine = _engine()
        history = _start(engine, _public())
        prices = []
        for _ in range(history.limits.max_rounds):
            rnd = engine.evaluate_buyer_offer(history, buyer_price=10.0, buyer_context=_public())
            history = engine.record_round(history, rnd)
            prices.append(rnd.seller_price)

        assert all(p > FLOOR for p in prices)
        assert prices == sorted(prices, reverse=True)
        assert rnd.action == NegotiationAction.FINAL_OFFER

    def test_buyer_rationale_never_names_the_floor(self):
        engine = _engine()
        history = _start(engine, _agency())
        for _ in range(history.limits.max_rounds):
            rnd = engine.evaluate_buyer_offer(history, buyer_price=50.0, buyer_context=_agency())
            history = engine.record_round(history, rnd)
            text = rnd.buyer_rationale.lower()
            assert "floor" not in text and "minimum" not in text
            assert f"{FLOOR:.2f}" not in rnd.buyer_rationale
            assert f"{rnd.seller_price:.2f}" not in rnd.buyer_rationale
            # The internal rationale keeps the full story for the seller's logs.
            assert f"{FLOOR:.2f}" in rnd.rationale


class TestConfigurableShare:
    def test_zero_share_counters_at_the_floor(self):
        engine = _engine(share=0.0)
        history = _start(engine, _agency())

        rnd = engine.evaluate_buyer_offer(history, buyer_price=50.0, buyer_context=_agency())

        assert rnd.seller_price == FLOOR

    def test_explicit_share_overrides_strategy(self):
        engine = _engine(share=0.2)
        history = _start(engine, _agency())

        rnd = engine.evaluate_buyer_offer(history, buyer_price=50.0, buyer_context=_agency())

        assert rnd.seller_price == pytest.approx(80.0 + 10.0 * 0.2)

    @pytest.mark.parametrize("share", [-0.1, 1.5])
    def test_out_of_range_share_rejected(self, share):
        with pytest.raises(ValueError):
            _engine(share=share)

    def test_service_engine_reads_the_setting(self):
        from ad_seller.services import negotiation_service

        with patch(
            "ad_seller.config.get_settings",
            return_value=SimpleNamespace(negotiation_below_floor_counter_share=0.0),
        ):
            engine = negotiation_service.build_negotiation_engine()
        history = _start(engine, _agency())

        rnd = engine.evaluate_buyer_offer(history, buyer_price=50.0, buyer_context=_agency())

        assert rnd.seller_price == FLOOR

    def test_setting_defaults_to_strategy_share(self):
        from ad_seller.config.settings import Settings

        assert Settings().negotiation_below_floor_counter_share is None
