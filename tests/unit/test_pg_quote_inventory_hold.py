"""PG quotes soft-hold their impressions so capacity cannot be quoted twice.

``create_quote`` checked each PG request against the product's capacity in
isolation, so two buyers could each be quoted (and book) the same
guaranteed impressions. A PG quote now holds its volume against
``maximum_impressions`` until it expires, booking turns the hold into a
commitment that lasts until the flight ends, and a PG quote that does not
fit in what is left is refused with 409 ``insufficient_avails``.
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

from ad_seller.config import get_settings  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402
from ad_seller.models.core import DealType, PricingModel  # noqa: E402
from ad_seller.models.flow_state import ProductDefinition  # noqa: E402

CAPACITY = 5_000_000
HOLD_KEY = "inventory_hold:ctv-1"


def _catalog(maximum_impressions=CAPACITY):
    product = ProductDefinition(
        product_id="ctv-1",
        name="CTV",
        inventory_type="ctv",
        supported_deal_types=[DealType.PROGRAMMATIC_GUARANTEED, DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100_000,
        maximum_impressions=maximum_impressions,
    )
    return {"products": {"ctv-1": product}, "inventory_types": ["ctv"]}


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
    s._store = store
    return s


async def _post(storage, path, body, settings=None, catalog=None):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(
        identity=BuyerIdentity(agency_id="agency-1")
    )
    settings = settings or get_settings().model_copy(
        update={"approval_required_above_value": None, "approval_gate_enabled": False}
    )
    try:
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                new=AsyncMock(return_value=catalog or _catalog()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.config.get_settings", return_value=settings),
            patch(
                "ad_seller.services.rate_card_service.get_rate_card_entries",
                new=AsyncMock(return_value=None),
            ),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                return await c.post(path, json=body)
    finally:
        app.dependency_overrides.clear()


async def _quote(storage, impressions, deal_type="PG", key=None, **kw):
    return await _post(
        storage,
        "/api/v1/quotes",
        {
            "idempotency_key": key or f"idem-{deal_type}-{impressions}-{len(storage._store)}",
            "product_id": "ctv-1",
            "deal_type": deal_type,
            "impressions": impressions,
            "flight_start": "2027-01-01",
            "flight_end": "2027-01-31",
        },
        **kw,
    )


async def test_second_pg_quote_beyond_capacity_is_refused(storage):
    first = await _quote(storage, 3_000_000)
    assert first.status_code == 200, first.text

    second = await _quote(storage, 3_000_000)

    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["error"] == "insufficient_avails"
    assert detail["available_impressions"] == 2_000_000


async def test_remaining_capacity_can_still_be_quoted(storage):
    assert (await _quote(storage, 3_000_000)).status_code == 200
    assert (await _quote(storage, 2_000_000)).status_code == 200
    assert (await _quote(storage, 100_000)).status_code == 409


async def test_idempotent_replay_does_not_hold_twice(storage):
    first = await _quote(storage, 3_000_000, key="idem-same")
    again = await _quote(storage, 3_000_000, key="idem-same")

    assert again.status_code == 200
    assert again.json()["quote"]["quote_id"] == first.json()["quote"]["quote_id"]
    assert len(storage._store[HOLD_KEY]) == 1


async def test_expired_hold_releases_capacity(storage):
    quote_id = (await _quote(storage, 3_000_000)).json()["quote"]["quote_id"]
    past = (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z"
    storage._store[HOLD_KEY][quote_id]["until"] = past

    resp = await _quote(storage, 3_000_000)

    assert resp.status_code == 200, resp.text
    assert quote_id not in storage._store[HOLD_KEY]


async def test_booking_keeps_the_volume_held_past_quote_expiry(storage):
    quote_id = (await _quote(storage, 3_000_000)).json()["quote"]["quote_id"]

    booked = await _post(
        storage, "/api/v1/deals", {"idempotency_key": "idem-book", "quote_id": quote_id}
    )
    assert booked.status_code == 200, booked.text

    entry = storage._store[HOLD_KEY][quote_id]
    assert entry["state"] == "booked"
    assert entry["until"].startswith("2027-02-01")
    assert (await _quote(storage, 3_000_000)).status_code == 409


async def test_non_guaranteed_quotes_do_not_hold(storage):
    assert (await _quote(storage, 4_000_000, deal_type="PD")).status_code == 200
    assert HOLD_KEY not in storage._store
    assert (await _quote(storage, 4_000_000)).status_code == 200


async def test_uncapped_products_are_not_tracked(storage):
    catalog = _catalog(maximum_impressions=None)
    assert (await _quote(storage, 30_000_000, catalog=catalog)).status_code == 200
    assert (await _quote(storage, 30_000_000, catalog=catalog)).status_code == 200
    assert HOLD_KEY not in storage._store


async def test_hold_can_be_disabled(storage):
    settings = get_settings().model_copy(update={"pg_quote_inventory_hold_enabled": False})
    assert (await _quote(storage, 3_000_000, settings=settings)).status_code == 200
    assert (await _quote(storage, 3_000_000, settings=settings)).status_code == 200
