"""PG quotes soft-hold their impressions so capacity cannot be quoted twice.

``create_quote`` checked each PG request against the product's capacity in
isolation, so two buyers could each be quoted (and book) the same
guaranteed impressions. A PG quote now holds its volume against
``maximum_impressions`` until it expires, booking turns the hold into a
commitment that lasts until the flight ends, and a PG quote that does not
fit in what is left is refused with 409 ``insufficient_avails``.
"""

import asyncio
import json
import sys
from contextlib import asynccontextmanager, contextmanager
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

    # Real storage backends yield to the event loop on every call; without
    # that, concurrent requests in a test could never interleave.
    async def _get(k):
        await asyncio.sleep(0)
        return store.get(k)

    async def _set(k, v, ttl=None):
        await asyncio.sleep(0)
        store[k] = v

    s = AsyncMock()
    s.get = AsyncMock(side_effect=_get)
    s.set = AsyncMock(side_effect=_set)
    s.get_quote = AsyncMock(side_effect=lambda q: store.get(f"quote:{q}"))
    s.set_quote = AsyncMock(side_effect=lambda q, d, ttl=86400: store.__setitem__(f"quote:{q}", d))
    s.get_deal = AsyncMock(side_effect=lambda d: store.get(f"deal:{d}"))
    s.set_deal = AsyncMock(side_effect=lambda d, data: store.__setitem__(f"deal:{d}", data))
    s.get_negotiation = AsyncMock(return_value=None)
    s._store = store
    return s


def _settings(**overrides):
    return get_settings().model_copy(
        update={
            "approval_required_above_value": None,
            "approval_gate_enabled": False,
            **overrides,
        }
    )


@asynccontextmanager
async def _client(storage, settings=None, catalog=None, key_id="key-a"):
    """ASGI client; ``key_id=None`` makes the caller anonymous."""
    record = (
        None
        if key_id is None
        else MagicMock(identity=BuyerIdentity(agency_id="agency-1"), key_id=key_id)
    )
    app.dependency_overrides[_get_optional_api_key_record] = lambda: record
    # The per-buyer share cap has its own tests; elsewhere one buyer may
    # hold the whole product.
    settings = settings or _settings(pg_hold_max_buyer_share=1.0)
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
                yield c
    finally:
        app.dependency_overrides.clear()


async def _post(storage, path, body, **kw):
    async with _client(storage, **kw) as c:
        return await c.post(path, json=body)


def _quote_body(impressions, deal_type="PG", key="idem-quote"):
    return {
        "idempotency_key": key,
        "product_id": "ctv-1",
        "deal_type": deal_type,
        "impressions": impressions,
        "flight_start": "2027-01-01",
        "flight_end": "2027-01-31",
    }


async def _quote(storage, impressions, deal_type="PG", key=None, **kw):
    key = key or f"idem-{deal_type}-{impressions}-{len(storage._store)}"
    return await _post(
        storage, "/api/v1/quotes", _quote_body(impressions, deal_type, key=key), **kw
    )


async def _book(storage, quote_id, **kw):
    return await _post(
        storage,
        "/api/v1/deals",
        {"idempotency_key": f"idem-book-{quote_id}", "quote_id": quote_id},
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
    settings = _settings(pg_quote_inventory_hold_enabled=False)
    assert (await _quote(storage, 3_000_000, settings=settings)).status_code == 200
    assert (await _quote(storage, 3_000_000, settings=settings)).status_code == 200


async def test_concurrent_quotes_neither_oversell_nor_drop_holds(storage):
    async with _client(storage) as c:
        responses = await asyncio.gather(
            *(
                c.post("/api/v1/quotes", json=_quote_body(2_000_000, key=f"idem-race-{i}"))
                for i in range(10)
            )
        )

    accepted = [r.json()["quote"]["quote_id"] for r in responses if r.status_code == 200]
    assert len(accepted) == 2
    assert all(r.status_code in (200, 409) for r in responses)
    assert set(storage._store[HOLD_KEY]) == set(accepted)


async def test_booking_records_a_quote_missing_from_the_ledger(storage):
    kept = (await _quote(storage, 1_000_000)).json()["quote"]["quote_id"]
    lost = (await _quote(storage, 2_000_000)).json()["quote"]["quote_id"]
    del storage._store[HOLD_KEY][lost]

    assert (await _book(storage, lost)).status_code == 200, "booking failed"

    entry = storage._store[HOLD_KEY][lost]
    assert entry["state"] == "booked"
    assert entry["impressions"] == 2_000_000
    assert kept in storage._store[HOLD_KEY]


async def test_anonymous_quote_is_checked_but_holds_nothing(storage):
    assert (await _quote(storage, CAPACITY, key_id=None)).status_code == 200
    assert not storage._store.get(HOLD_KEY)

    assert (await _quote(storage, 3_000_000)).status_code == 200
    refused = await _quote(storage, 3_000_000, key_id=None)

    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "insufficient_avails"
    assert len(storage._store[HOLD_KEY]) == 1


async def test_mcp_request_quote_is_checked_but_holds_nothing(storage):
    from ad_seller.interfaces import mcp_server

    with (
        patch(
            "ad_seller.services.catalog_service.get_static_product_catalog",
            new=AsyncMock(return_value=_catalog()),
        ),
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch(
            "ad_seller.services.rate_card_service.get_rate_card_entries",
            new=AsyncMock(return_value=None),
        ),
    ):
        first = json.loads(await mcp_server.request_quote("ctv-1", "PG", CAPACITY))
        assert first.get("quote_id"), first
        assert not storage._store.get(HOLD_KEY)

        assert (await _quote(storage, 3_000_000)).status_code == 200
        refused = json.loads(await mcp_server.request_quote("ctv-1", "PG", 3_000_000))

    assert refused["detail"]["error"] == "insufficient_avails"
    assert len(storage._store[HOLD_KEY]) == 1


async def test_one_buyer_cannot_hold_more_than_its_share(storage):
    settings = _settings(pg_hold_max_buyer_share=0.5)

    assert (await _quote(storage, 2_000_000, settings=settings)).status_code == 200
    capped = await _quote(storage, 1_000_000, settings=settings)

    assert capped.status_code == 409
    assert capped.json()["detail"]["error"] == "pg_hold_limit_exceeded"
    other = await _quote(storage, 2_000_000, settings=settings, key_id="key-b")
    assert other.status_code == 200


async def test_hold_lapses_before_the_quote_expires(storage):
    quote = (await _quote(storage, 3_000_000)).json()["quote"]

    until = datetime.fromisoformat(storage._store[HOLD_KEY][quote["quote_id"]]["until"][:-1])
    assert until <= datetime.utcnow() + timedelta(seconds=3600)
    assert until < datetime.fromisoformat(quote["expires_at"].replace("Z", "+00:00")).replace(
        tzinfo=None
    )


async def test_booking_after_the_hold_lapsed_rechecks_capacity(storage):
    quote_id = (await _quote(storage, 3_000_000)).json()["quote"]["quote_id"]
    storage._store[HOLD_KEY][quote_id]["until"] = "2020-01-01T00:00:00Z"
    assert (await _quote(storage, 3_000_000, key_id="key-b")).status_code == 200

    refused = await _book(storage, quote_id)

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["error"] == "insufficient_avails"
    assert storage._store[f"quote:{quote_id}"]["status"] == "available"
    assert quote_id not in storage._store[HOLD_KEY]


async def test_booking_after_the_hold_lapsed_succeeds_while_capacity_remains(storage):
    quote_id = (await _quote(storage, 3_000_000)).json()["quote"]["quote_id"]
    storage._store[HOLD_KEY][quote_id]["until"] = "2020-01-01T00:00:00Z"

    assert (await _book(storage, quote_id)).status_code == 200

    assert storage._store[HOLD_KEY][quote_id]["state"] == "booked"


def test_hold_ttl_cannot_exceed_the_quote_ttl():
    from pydantic import ValidationError

    from ad_seller.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(pg_hold_ttl_seconds=86401)


@contextmanager
def _mcp(storage):
    from ad_seller.interfaces import mcp_server

    with (
        patch(
            "ad_seller.services.catalog_service.get_static_product_catalog",
            new=AsyncMock(return_value=_catalog()),
        ),
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch(
            "ad_seller.services.rate_card_service.get_rate_card_entries",
            new=AsyncMock(return_value=None),
        ),
        patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        # No MCP request context: treated as the in-process operator.
        patch.object(mcp_server.mcp, "get_context", side_effect=LookupError),
    ):
        yield mcp_server


async def _bulk_create(storage, quote_id):
    return await _post(
        storage, "/api/v1/deals/bulk", {"operations": [{"action": "create", "quote_id": quote_id}]}
    )


async def _from_template(storage, impressions, **kw):
    return await _post(
        storage,
        "/api/v1/deals/from-template",
        {
            "deal_type": "PG",
            "product_id": "ctv-1",
            "impressions": impressions,
            "flight_start": "2027-01-01",
            "flight_end": "2027-01-31",
        },
        **kw,
    )


async def test_bulk_create_commits_the_pg_hold(storage):
    quote_id = (await _quote(storage, 3_000_000)).json()["quote"]["quote_id"]

    result = (await _bulk_create(storage, quote_id)).json()["results"][0]

    assert result["success"], result
    entry = storage._store[HOLD_KEY][quote_id]
    assert entry["state"] == "booked"
    assert entry["until"].startswith("2027-02-01")


async def test_bulk_create_rechecks_capacity_when_the_hold_lapsed(storage):
    quote_id = (await _quote(storage, 3_000_000)).json()["quote"]["quote_id"]
    storage._store[HOLD_KEY][quote_id]["until"] = "2020-01-01T00:00:00Z"
    assert (await _quote(storage, 3_000_000, key_id="key-b")).status_code == 200

    result = (await _bulk_create(storage, quote_id)).json()["results"][0]

    assert not result["success"]
    assert result["error"].startswith("insufficient_avails")
    assert storage._store[f"quote:{quote_id}"]["status"] == "available"
    assert not any(k.startswith("deal:") for k in storage._store)


async def test_mcp_bulk_create_commits_the_pg_hold(storage):
    quote_id = (await _quote(storage, 3_000_000)).json()["quote"]["quote_id"]

    with _mcp(storage) as mcp_server:
        out = json.loads(
            await mcp_server.bulk_deal_operations(
                json.dumps([{"action": "create", "quote_id": quote_id}])
            )
        )

    assert out["succeeded"] == 1, out
    assert storage._store[HOLD_KEY][quote_id]["state"] == "booked"


async def test_from_template_pg_reserves_and_commits_capacity(storage):
    created = await _from_template(storage, 4_000_000)
    assert created.status_code == 201, created.text
    deal_id = created.json()["deal_id"]
    assert storage._store[HOLD_KEY][deal_id]["state"] == "booked"

    refused = await _from_template(storage, 2_000_000)

    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "insufficient_avails"
    assert (await _quote(storage, 2_000_000)).status_code == 409
    assert [k for k in storage._store if k.startswith("deal:")] == [f"deal:{deal_id}"]


async def test_mcp_from_template_pg_reserves_and_commits_capacity(storage):
    with _mcp(storage) as mcp_server:
        created = json.loads(
            await mcp_server.create_deal_from_template(
                "PG", "ctv-1", impressions=4_000_000, flight_end="2027-01-31"
            )
        )
        refused = json.loads(
            await mcp_server.create_deal_from_template("PG", "ctv-1", impressions=2_000_000)
        )

    assert storage._store[HOLD_KEY][created["deal_id"]]["state"] == "booked"
    assert refused["detail"]["error"] == "insufficient_avails"
