"""OpenRTB deal parameters come from one builder with the right auction type.

Quote booking emitted ``at=1`` for PG/PD and ``at=3`` for PA, the reverse
of OpenRTB (``at=3`` is the fixed-price deal, ``at=1`` the first-price
auction), while the proposal and Deals API paths each hand-rolled their own
copy. ``build_openrtb_deal_params`` is now the single source: PG/PD
``at=3``, PA ``at=1``, with ``wseat``/``wadomain`` always present.
"""

import pytest

from ad_seller.flows.deal_generation_flow import DealGenerationFlow
from ad_seller.models.core import DealType
from ad_seller.services.deals_api import DealsAPIService, build_openrtb_deal_params

EXPECTED_AT = {"PG": 3, "PD": 3, "PA": 1}


@pytest.mark.parametrize(
    "deal_type, code",
    [
        ("PG", "PG"),
        ("PD", "PD"),
        ("PA", "PA"),
        ("programmatic_guaranteed", "PG"),
        ("preferreddeal", "PD"),
        ("private_auction", "PA"),
        (DealType.PROGRAMMATIC_GUARANTEED, "PG"),
        (DealType.PRIVATE_AUCTION, "PA"),
    ],
)
def test_auction_type_by_deal_type(deal_type, code):
    params = build_openrtb_deal_params("D-1", deal_type, 12.5)
    assert params["at"] == EXPECTED_AT[code]
    assert params["bidfloor"] == 12.5
    assert params["wseat"] == [] and params["wadomain"] == []


def test_pg_carries_guaranteed_impressions():
    params = build_openrtb_deal_params("D-1", "PG", 30.0, guaranteed_impressions=1_000)
    assert params["ext"] == {"guaranteed": True, "impressions": 1_000}
    assert "ext" not in build_openrtb_deal_params("D-1", "PD", 30.0, guaranteed_impressions=1_000)


@pytest.mark.parametrize("code", ["PG", "PD", "PA"])
def test_deals_api_path_uses_the_builder(code):
    deal = DealsAPIService().build_deal_object("D-1", code, 20.0, buyer_seat_ids=["seat-9"])
    assert deal.openrtb_params["at"] == EXPECTED_AT[code]
    assert deal.openrtb_params["wseat"] == ["seat-9"]


@pytest.mark.parametrize(
    "deal_type, code",
    [
        (DealType.PROGRAMMATIC_GUARANTEED, "PG"),
        (DealType.PREFERRED_DEAL, "PD"),
        (DealType.PRIVATE_AUCTION, "PA"),
    ],
)
async def test_proposal_deal_path_uses_the_builder(deal_type, code):
    from ad_seller.models.core import PricingModel
    from ad_seller.models.flow_state import DealOutput

    flow = DealGenerationFlow()
    flow.state.deal_output = DealOutput(
        deal_id="D-1",
        deal_type=deal_type,
        proposal_id="p",
        product_id="x",
        price=20.0,
        pricing_model=PricingModel.CPM,
        currency="USD",
        buyer_organization_id="",
        seller_organization_id="",
        flight_start="",
        flight_end="",
        activation_type="traditional_dsp",
        guaranteed_impressions=500 if code == "PG" else None,
    )
    await flow.generate_openrtb_params()
    assert flow.state.openrtb_deal_params["at"] == EXPECTED_AT[code]
