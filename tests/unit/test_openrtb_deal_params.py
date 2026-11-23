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


@pytest.mark.parametrize("impressions", [None, 0])
def test_pg_is_guaranteed_without_impressions(impressions):
    params = build_openrtb_deal_params("D-1", "PG", 30.0, guaranteed_impressions=impressions)
    assert params["ext"] == {"guaranteed": True}


async def test_quote_booked_pg_deal_is_guaranteed():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from ad_seller.services import deal_service

    quote = {
        "quote_id": "qt-pg",
        "status": "available",
        "deal_type": "PG",
        "product": {"product_id": "ctv-1", "name": "CTV", "inventory_type": "ctv"},
        "pricing": {"base_cpm": 30.0, "final_cpm": 25.0, "currency": "USD"},
        "terms": {"impressions": 1000, "flight_start": "2026-04-01", "flight_end": "2026-04-30"},
        "buyer_tier": "public",
    }
    storage = AsyncMock()
    storage.get_quote = AsyncMock(return_value=quote)
    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch.object(deal_service, "_resolve_accepted_negotiation", AsyncMock(return_value=None)),
        patch.object(deal_service, "_emit_deal_created", AsyncMock()),
    ):
        deal = await deal_service.book_deal(SimpleNamespace(quote_id="qt-pg", audience_plan=None))

    assert deal["openrtb_params"]["ext"]["guaranteed"] is True


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
    if code == "PG":
        assert flow.state.openrtb_deal_params["ext"] == {"guaranteed": True, "impressions": 500}


# Deals booked before the fix stored the reversed ``at``; reads derive it.
_LEGACY_AT = {"PG": 1, "PD": 1, "PA": 3}


def _legacy_stored_deal(code: str) -> dict:
    deal_id = f"DEMO-LEGACY{code}"
    return {
        "deal_id": deal_id,
        "deal_type": code,
        "status": "proposed",
        "quote_id": f"qt-{code}",
        "product": {"product_id": "ctv-1", "name": "CTV", "inventory_type": "ctv"},
        "pricing": {"base_cpm": 30.0, "final_cpm": 25.0, "currency": "USD"},
        "terms": {"impressions": 1000, "flight_start": "2026-04-01", "flight_end": "2026-04-30"},
        "buyer_tier": "public",
        "expires_at": "2099-01-01T00:00:00Z",
        "openrtb_params": {
            "id": deal_id,
            "bidfloor": 25.0,
            "bidfloorcur": "USD",
            "at": _LEGACY_AT[code],
            "wseat": [],
        },
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def legacy_storage():
    from unittest.mock import AsyncMock

    store = {f"deal:{d['deal_id']}": d for d in map(_legacy_stored_deal, EXPECTED_AT)}
    storage = AsyncMock()
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.list_deals = AsyncMock(side_effect=lambda: list(store.values()))
    storage._store = store
    return storage


@pytest.fixture
async def api_client():
    import httpx

    from ad_seller.interfaces.api import deps
    from ad_seller.interfaces.api.main import app

    app.dependency_overrides[deps._require_operator_api_key_record] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize("code", ["PG", "PD", "PA"])
async def test_get_deal_derives_auction_type_for_legacy_record(code, legacy_storage, api_client):
    from unittest.mock import patch

    with patch("ad_seller.storage.factory.get_storage", return_value=legacy_storage):
        resp = await api_client.get(f"/api/v1/deals/DEMO-LEGACY{code}")

    assert resp.status_code == 200
    assert resp.json()["deal"]["openrtb_params"]["at"] == EXPECTED_AT[code]
    stored = legacy_storage._store[f"deal:DEMO-LEGACY{code}"]
    assert stored["openrtb_params"]["at"] == _LEGACY_AT[code]


async def test_list_deals_derives_auction_type_for_legacy_records(legacy_storage, api_client):
    from unittest.mock import patch

    with patch("ad_seller.storage.factory.get_storage", return_value=legacy_storage):
        resp = await api_client.get("/api/v1/deals")

    assert resp.status_code == 200
    got = {d["deal"]["deal_type"]: d["deal"]["openrtb_params"]["at"] for d in resp.json()["deals"]}
    assert got == EXPECTED_AT


async def test_generic_export_derives_auction_type_for_legacy_records(legacy_storage):
    from unittest.mock import patch

    from ad_seller.services import deal_service

    with patch("ad_seller.storage.factory.get_storage", return_value=legacy_storage):
        result = await deal_service.export_deals(format="generic")

    got = {d["deal_type"]: d["openrtb_params"]["at"] for d in result["deals"]}
    assert got == EXPECTED_AT
    assert legacy_storage._store["deal:DEMO-LEGACYPG"]["openrtb_params"]["at"] == 1


# Push takes deal_type as a free string: every accepted spelling normalizes
# to one code, so pricing fields and ``at`` agree; unknown types are rejected.
_SPELLINGS = [
    ("pd", "PD"),
    ("pg", "PG"),
    ("pa", "PA"),
    ("programmaticguaranteed", "PG"),
    ("programmatic_guaranteed", "PG"),
]
_IAB_TYPE = {"PG": "programmaticguaranteed", "PD": "preferreddeal", "PA": "privateauction"}


@pytest.mark.parametrize("deal_type, code", _SPELLINGS)
def test_builder_normalizes_deal_type_spellings(deal_type, code):
    params = build_openrtb_deal_params("D-1", deal_type, 20.0, guaranteed_impressions=10)
    assert params["at"] == EXPECTED_AT[code]
    assert ("ext" in params) == (code == "PG")


@pytest.mark.parametrize("deal_type, code", _SPELLINGS)
def test_deal_object_pricing_agrees_with_auction_type(deal_type, code):
    deal = DealsAPIService().build_deal_object("D-1", deal_type, 20.0)
    assert deal.deal_type == _IAB_TYPE[code]
    assert deal.openrtb_params["at"] == EXPECTED_AT[code]
    if code == "PA":
        assert (deal.floor_price_cpm, deal.fixed_price_cpm) == (20.0, None)
    else:
        assert (deal.floor_price_cpm, deal.fixed_price_cpm) == (None, 20.0)


def test_unknown_deal_type_is_rejected_by_builders():
    with pytest.raises(ValueError):
        build_openrtb_deal_params("D-1", "XYZ", 20.0)
    with pytest.raises(ValueError):
        DealsAPIService().build_deal_object("D-1", "XYZ", 20.0)


async def _push(api_client, deal_type):
    from unittest.mock import AsyncMock, patch

    storage = AsyncMock()
    storage.get_deal = AsyncMock(return_value=None)
    push = AsyncMock(return_value=[])
    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch.object(DealsAPIService, "push_deal_to_multiple_buyers", push),
    ):
        resp = await api_client.post(
            "/api/v1/deals/push",
            json={
                "deal_id": "D-PUSH",
                "buyer_urls": ["https://buyer.example.test/deals"],
                "deal_type": deal_type,
                "price": 20.0,
            },
        )
    return resp, push


@pytest.mark.parametrize("deal_type, code", _SPELLINGS)
async def test_push_normalizes_deal_type(deal_type, code, api_client):
    resp, push = await _push(api_client, deal_type)
    assert resp.status_code == 200
    deal = push.await_args.args[0]
    assert deal.deal_type == _IAB_TYPE[code]
    assert deal.openrtb_params["at"] == EXPECTED_AT[code]
    assert (deal.fixed_price_cpm is not None) == (code != "PA")
    assert (deal.floor_price_cpm is not None) == (code == "PA")


async def test_push_rejects_unknown_deal_type(api_client):
    resp, push = await _push(api_client, "XYZ")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_deal_type"
    push.assert_not_awaited()


@pytest.mark.parametrize("code", ["PG", "PD", "PA"])
def test_agentcore_direct_deal_uses_the_builder(code, monkeypatch):
    import json
    import sys
    from types import ModuleType, SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    http_main = ModuleType("ad_seller.interfaces.agentcore.http_main")
    http_main._chat = SimpleNamespace(
        _products={
            "prod-1": SimpleNamespace(
                name="CTV", base_cpm=25.0, floor_cpm=10.0, inventory_type="ctv"
            )
        }
    )
    http_main._get_chat = AsyncMock()
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", MagicMock())
    monkeypatch.setitem(sys.modules, "bedrock_agentcore.runtime", MagicMock())
    monkeypatch.setitem(sys.modules, "ad_seller.interfaces.agentcore.http_main", http_main)
    from ad_seller.interfaces.agentcore.crew_tools import CreateDealTool

    deal = json.loads(CreateDealTool()._create_deal_direct("prod-1", code.lower(), 30.0, 5_000))

    assert deal["openrtb_params"] == build_openrtb_deal_params(
        deal["deal_id"], code, 30.0, guaranteed_impressions=5_000
    )
