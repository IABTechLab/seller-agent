"""Deals carry OpenRTB ``wseat``/``wadomain`` from the verified buyer.

Booked deals always went out with ``wseat: []`` (and no ``wadomain`` at
all on the quote booking path), so any DSP seat could bid on a buyer's
private deal. Seller-issued API keys can now carry ``seat_ids`` and
``advertiser_domains``; the booking caller's verified seat IDs (its
``seat_id`` plus ``seat_ids``) and domains become the deal's allow-lists.
Self-asserted request fields never populate them.
"""

import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

_broken_flows = ["ad_seller.flows.execution_activation_flow"]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.auth.api_key_service import ApiKeyService  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.api_key import ApiKeyCreateRequest  # noqa: E402
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity  # noqa: E402
from ad_seller.services.deals_api import verified_deal_allow_lists  # noqa: E402

IDENTITY = BuyerIdentity(
    seat_id="seat-1",
    seat_ids=["seat-2", "seat-1"],
    agency_id="agency-1",
    advertiser_id="adv-1",
    advertiser_domains=["Example-Brand.test", " "],
)


def _quote():
    return {
        "quote_id": "qt-allow00001",
        "status": "available",
        "deal_type": "PD",
        "product": {"product_id": "ctv-1", "name": "CTV", "inventory_type": "ctv"},
        "pricing": {
            "base_cpm": 35.0,
            "tier_discount_pct": 15.0,
            "volume_discount_pct": 0.0,
            "final_cpm": 29.75,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base price: $35.00 CPM",
        },
        "terms": {
            "impressions": 1_000_000,
            "flight_start": "2027-01-01",
            "flight_end": "2027-01-31",
            "guaranteed": False,
        },
        "buyer_tier": "advertiser",
        "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


@pytest.fixture
def storage():
    store = {}
    s = AsyncMock()
    s.get = AsyncMock(side_effect=lambda k: store.get(k))
    s.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    s.get_quote = AsyncMock(side_effect=lambda q: store.get(f"quote:{q}"))
    s.set_quote = AsyncMock(side_effect=lambda q, d, ttl=86400: store.__setitem__(f"quote:{q}", d))
    s.get_deal = AsyncMock(side_effect=lambda d: store.get(f"deal:{d}"))
    s.set_deal = AsyncMock(side_effect=lambda d, data: store.__setitem__(f"deal:{d}", data))
    s.get_negotiation = AsyncMock(return_value=None)
    s.get_proposal = AsyncMock(side_effect=lambda p: store.get(f"proposal:{p}"))
    s.set_proposal = AsyncMock(side_effect=lambda p, d: store.__setitem__(f"proposal:{p}", d))
    s._store = store
    return s


def test_only_api_key_identities_populate_allow_lists():
    keyed = BuyerContext(identity=IDENTITY, is_authenticated=True, authentication_method="api_key")
    claimed = BuyerContext(identity=IDENTITY, is_authenticated=True)

    assert verified_deal_allow_lists(keyed) == (["seat-1", "seat-2"], ["example-brand.test"])
    assert verified_deal_allow_lists(claimed) == ([], [])
    assert verified_deal_allow_lists(None) == ([], [])


async def test_quote_booking_stamps_wseat_and_wadomain(storage):
    storage._store["quote:qt-allow00001"] = _quote()
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(identity=IDENTITY)
    try:
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/v1/deals",
                    json={
                        "idempotency_key": "idem-allow",
                        "quote_id": "qt-allow00001",
                        "buyer_identity": {"seat_id": "spoofed-seat"},
                    },
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    params = resp.json()["deal"]["openrtb_params"]
    assert params["wseat"] == ["seat-1", "seat-2"]
    assert params["wadomain"] == ["example-brand.test"]


async def test_key_without_allow_lists_keeps_deal_open(storage):
    storage._store["quote:qt-allow00001"] = _quote()
    identity = BuyerIdentity(agency_id="agency-1", advertiser_id="adv-1")
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(identity=identity)
    try:
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/v1/deals",
                    json={"idempotency_key": "idem-open", "quote_id": "qt-allow00001"},
                )
    finally:
        app.dependency_overrides.clear()

    params = resp.json()["deal"]["openrtb_params"]
    assert params["wseat"] == [] and params["wadomain"] == []


async def test_api_keys_carry_seat_ids_and_domains(storage):
    service = ApiKeyService(storage)
    created = await service.create_key(
        ApiKeyCreateRequest(
            seat_id="seat-1", seat_ids=["seat-2"], advertiser_domains=["example-brand.test"]
        )
    )

    record = await service.validate_key(created.api_key)

    assert record.identity.seat_ids == ["seat-2"]
    assert record.identity.advertiser_domains == ["example-brand.test"]
