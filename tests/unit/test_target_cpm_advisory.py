# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""`target_cpm` is advisory: it must never move the quoted price.

The shared protocol spec defines `QuoteRequest.target_cpm` as "Buyer's
desired CPM ... Advisory" — the buyer's statement of what it expects to pay,
formed from its own rate card and spend history before the seller has priced
anything. The seller prices; the buyer's number informs whether to quote or
negotiate.

`create_quote` used to overwrite `final_cpm` with `request.target_cpm`
whenever that value cleared the floors, which was wrong in both directions:

* **Above** our price it overcharged, while `tier_discount_pct` and
  `rationale` went on advertising the discount we had just computed and
  discarded. A real booking quoted "Advertiser tier: -15% | Final price:
  $12.75 CPM" and billed $15.00.
* **Below** our price it conceded margin automatically to any buyer who named
  a number above the floor, bypassing negotiation, because
  `is_price_acceptable` is a floor check and not a pricing decision.

The fixtures below use the shape of that real incident: a $15.00 base, a
$10.00 product floor, and an advertiser-tier buyer whose 15% discount makes
the seller's price $12.75.
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_seller.services import quote_service

BASE_CPM = 15.0
FLOOR_CPM = 10.0
DISCOUNTED_CPM = 12.75  # 15.00 less the 15% advertiser tier discount
IMPRESSIONS = 1_666_666  # below every volume-discount threshold


def _make_product():
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
        floor_cpm=FLOOR_CPM,
        minimum_impressions=100_000,
    )


def _make_catalog():
    product = _make_product()
    return {
        "products": {product.product_id: product},
        "inventory_types": [product.inventory_type],
    }


def _advertiser_context():
    """A verified buyer at ADVERTISER tier, which earns a 15% discount."""
    from ad_seller.models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity

    context = BuyerContext(
        identity=BuyerIdentity(
            seat_id="seat-meridian",
            seat_name="Meridian Outdoor Co.",
            dsp_platform="rig",
            agency_id="agency-meridian",
            agency_name="Meridian Outdoor Co.",
            advertiser_id="adv-meridian",
            advertiser_name="Meridian Outdoor Co.",
        ),
        is_authenticated=True,
    )
    # Guard the fixture itself: if tier derivation changes, these tests would
    # silently stop exercising a discount at all and pass for the wrong reason.
    assert context.effective_tier == AccessTier.ADVERTISER
    return context


def _public_context():
    from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity

    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


def _request(target_cpm=None):
    request = MagicMock()
    request.product_id = "ctv-premium-sports"
    request.deal_type = "PD"
    request.impressions = IMPRESSIONS
    request.flight_start = None
    request.flight_end = None
    request.target_cpm = target_cpm
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


async def _quote(mock_storage, context, target_cpm=None):
    with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
        return await quote_service.create_quote(_request(target_cpm), context, _make_catalog())


def _rationale_final_price(rationale):
    """The dollar figure the rationale string claims as the final price."""
    match = re.search(r"Final price: \$([0-9]+\.[0-9]{2}) CPM", rationale)
    assert match, f"rationale did not state a final price: {rationale!r}"
    return float(match.group(1))


class TestTargetCpmDoesNotMoveThePrice:
    """The seller's computed price is the quoted price, whatever the buyer asks."""

    async def test_no_target_quotes_the_discounted_price(self, mock_storage):
        """Baseline: an advertiser gets the tier discount."""
        quote = await _quote(mock_storage, _advertiser_context())

        assert quote["pricing"]["base_cpm"] == BASE_CPM
        assert quote["pricing"]["tier_discount_pct"] == 15.0
        assert quote["pricing"]["final_cpm"] == DISCOUNTED_CPM

    async def test_target_above_our_price_does_not_overcharge(self, mock_storage):
        """The reported bug. Buyer expects $15.00; our price is $12.75.

        Previously billed the buyer's $15.00 while the rationale promised
        $12.75. The buyer's conservative estimate of its own discount must
        not become the price.
        """
        quote = await _quote(mock_storage, _advertiser_context(), target_cpm=BASE_CPM)

        assert quote["pricing"]["final_cpm"] == DISCOUNTED_CPM
        assert quote["pricing"]["final_cpm"] < BASE_CPM

    async def test_target_below_our_price_does_not_concede_margin(self, mock_storage):
        """The other direction, and the one option 2 would have kept.

        $11.00 clears the $10.00 product floor, so the old floor check
        accepted it and the seller charged $11.00 instead of its own $12.75.
        A discount is a decision the seller makes, not a number the buyer can
        assert; a buyer wanting better than $12.75 negotiates for it.
        """
        quote = await _quote(mock_storage, _advertiser_context(), target_cpm=11.0)

        assert quote["pricing"]["final_cpm"] == DISCOUNTED_CPM

    async def test_target_equal_to_our_price_is_a_no_op(self, mock_storage):
        quote = await _quote(mock_storage, _advertiser_context(), target_cpm=DISCOUNTED_CPM)

        assert quote["pricing"]["final_cpm"] == DISCOUNTED_CPM

    async def test_target_below_the_floor_does_not_price_under_the_floor(self, mock_storage):
        """A target under the floor is ignored like any other target."""
        quote = await _quote(mock_storage, _advertiser_context(), target_cpm=0.50)

        assert quote["pricing"]["final_cpm"] == DISCOUNTED_CPM
        assert quote["pricing"]["final_cpm"] >= FLOOR_CPM

    async def test_public_tier_target_does_not_move_the_price_either(self, mock_storage):
        """No discount to protect here, but the buyer still cannot set the price."""
        quote = await _quote(mock_storage, _public_context(), target_cpm=25.0)

        assert quote["pricing"]["tier_discount_pct"] == 0.0
        assert quote["pricing"]["final_cpm"] == BASE_CPM


class TestPricingEnvelopeIsSelfConsistent:
    """`final_cpm` must agree with the fields that explain it.

    The original defect was visible in the response itself: a rationale
    stating $12.75 next to a `final_cpm` of $15.00. Nothing recomputed or
    validated the rationale against the price shipped beside it.
    """

    @pytest.mark.parametrize("target_cpm", [None, 11.0, DISCOUNTED_CPM, BASE_CPM, 99.0])
    async def test_rationale_matches_final_cpm(self, mock_storage, target_cpm):
        quote = await _quote(mock_storage, _advertiser_context(), target_cpm=target_cpm)
        pricing = quote["pricing"]

        assert _rationale_final_price(pricing["rationale"]) == pricing["final_cpm"]

    @pytest.mark.parametrize("target_cpm", [None, 11.0, BASE_CPM])
    async def test_discount_percentages_explain_the_final_price(self, mock_storage, target_cpm):
        """base_cpm reduced by the advertised discounts must equal final_cpm."""
        quote = await _quote(mock_storage, _advertiser_context(), target_cpm=target_cpm)
        pricing = quote["pricing"]

        expected = pricing["base_cpm"]
        expected *= 1 - pricing["tier_discount_pct"] / 100
        expected *= 1 - pricing["volume_discount_pct"] / 100

        assert round(expected, 2) == pricing["final_cpm"]


class TestBookedDealCarriesTheQuotedPrice:
    """Booking must bill what was quoted, since it copies the quote's pricing."""

    async def test_booked_deal_price_matches_the_quote(self, mock_storage):
        quote = await _quote(mock_storage, _advertiser_context(), target_cpm=BASE_CPM)

        stored = mock_storage._store[f"quote:{quote['quote_id']}"]

        assert stored["pricing"]["final_cpm"] == DISCOUNTED_CPM
        assert _rationale_final_price(stored["pricing"]["rationale"]) == DISCOUNTED_CPM
