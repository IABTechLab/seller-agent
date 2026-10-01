"""Booking paths publish ``deal.created`` on the event bus.

Only the approval flow and the negotiation service emitted events; a deal
booked over REST (quote → book, or from-template) left the event feed
empty, so an inbox polling ``GET /events`` never saw it.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_broken_flows = ["ad_seller.flows.execution_activation_flow"]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.events.bus import InMemoryEventBus  # noqa: E402
from ad_seller.events.models import EventType  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.api_key import ApiKeyRole  # noqa: E402
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402
from ad_seller.services import deal_service  # noqa: E402
from tests.unit.test_curated_deal import _catalog, _curated_request, _make_product  # noqa: E402
from tests.unit.test_deal_booking_endpoints import (  # noqa: E402
    _authenticate,
    _make_available_quote,
)
from tests.unit.test_trust_tier_verification import _mock_catalog  # noqa: E402


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


_PAYLOAD_KEYS = ("source", "deal_type", "status", "product_id", "quote_id")


def _assert_payload(event, **expected):
    """Pin every ``deal.created`` payload key (``quote_id`` is ``None`` on
    paths that book without a quote)."""
    assert set(expected) == set(_PAYLOAD_KEYS), "pin all five payload keys"
    assert event.payload == expected


class TestBookingEmitsDealCreated:
    async def test_quote_booking_emits_deal_created(self, client, mock_storage, event_bus):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        _authenticate()  # booking requires a verified buyer since #77

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={"idempotency_key": "idem-evt", "quote_id": quote["quote_id"]},
            )

        assert resp.status_code == 200
        deal_id = resp.json()["deal"]["deal_id"]
        events = await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert [e.deal_id for e in events] == [deal_id]
        _assert_payload(
            events[0],
            source="quote",
            deal_type=quote["deal_type"],
            status="proposed",
            product_id="ctv-premium-sports",
            quote_id=quote["quote_id"],
        )

    async def test_template_booking_emits_deal_created(self, client, mock_storage, event_bus):
        # from-template requires an authenticated buyer; supply a minimal key record.
        app.dependency_overrides[_get_optional_api_key_record] = lambda: SimpleNamespace(
            key_id="k-test",
            role=ApiKeyRole.BUYER,
            identity=BuyerIdentity(agency_id="agency-001", agency_name="Agency One"),
        )
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(),
            ),
        ):
            resp = await client.post(
                "/api/v1/deals/from-template",
                json={
                    "idempotency_key": "idem-tpl-evt",
                    "deal_type": "PD",
                    "product_id": "ctv-premium-sports",
                    "impressions": 1000000,
                },
            )

        assert resp.status_code == 201, resp.text
        deal_id = resp.json()["deal_id"]
        events = await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert [e.deal_id for e in events] == [deal_id]
        _assert_payload(
            events[0],
            source="template",
            deal_type="PD",
            status="confirmed",
            product_id="ctv-premium-sports",
            quote_id=None,
        )

    async def test_curated_booking_emits_deal_created(self, mock_storage, event_bus):
        catalog = _catalog(_make_product(base_cpm=45.0, floor_cpm=35.0))
        with (
            patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        ):
            result = await deal_service.create_curated_deal(
                _curated_request(product_id="ctv-premium-sports"), catalog
            )

        events = await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert [e.deal_id for e in events] == [result["deal_id"]]
        _assert_payload(
            events[0],
            source="curated",
            deal_type="PMP",
            status="confirmed",
            product_id="ctv-premium-sports",
            quote_id=None,
        )

    async def test_bulk_create_emits_deal_created(self, mock_storage, event_bus):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        op = SimpleNamespace(action="create", quote_id=quote["quote_id"], deal_id=None, notes=None)

        with (
            patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        ):
            results = await deal_service.bulk_deal_operations([op])

        assert results[0]["success"] is True, results
        events = await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert [e.deal_id for e in events] == [results[0]["deal_id"]]
        _assert_payload(
            events[0],
            source="bulk",
            deal_type=quote["deal_type"],
            status="confirmed",
            product_id="ctv-premium-sports",
            quote_id=quote["quote_id"],
        )

    async def test_migration_emits_deal_created(self, mock_storage, event_bus):
        old_id = "DEAL-ORIG"
        mock_storage._store[f"deal:{old_id}"] = {
            "deal_id": old_id,
            "deal_type": "PD",
            "status": "confirmed",
            "product_id": "ctv-premium-sports",
            "actual_price_cpm": 30.0,
            "impressions": 1_000_000,
        }
        request = SimpleNamespace(
            deal_type=None,
            product_id=None,
            max_cpm=None,
            impressions=None,
            flight_start=None,
            flight_end=None,
            buyer_seat_ids=None,
            reason="better supply path",
        )

        with (
            patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        ):
            result = await deal_service.migrate_deal(old_id, request)

        events = await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert [e.deal_id for e in events] == [result["new_deal_id"]]
        _assert_payload(
            events[0],
            source="migration",
            deal_type="PD",
            status="confirmed",
            product_id="ctv-premium-sports",
            quote_id=None,
        )

    async def test_idempotent_replay_emits_once(self, client, mock_storage, event_bus):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        _authenticate()
        body = {"idempotency_key": "idem-replay", "quote_id": quote["quote_id"]}

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        ):
            first = await client.post("/api/v1/deals", json=body)
            second = await client.post("/api/v1/deals", json=body)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        deal_id = first.json()["deal"]["deal_id"]
        assert second.json()["deal"]["deal_id"] == deal_id
        events = await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert [e.deal_id for e in events] == [deal_id]


class TestDealCreatedIsAuditClass:
    """``deal.created`` is in ``AUDIT_EVENT_TYPES``: a bus failure falls back
    to the audit log and the booking still succeeds; a fallback failure
    propagates, but only after the deal is persisted."""

    async def test_bus_failure_writes_fallback_and_booking_succeeds(self, client, mock_storage):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        _authenticate()
        broken_bus = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("bus down")))
        fallback = MagicMock()

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=broken_bus)),
            patch("ad_seller.events.helpers.write_audit_fallback", fallback),
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={"idempotency_key": "idem-bus-down", "quote_id": quote["quote_id"]},
            )

        assert resp.status_code == 200, resp.text
        fallback.assert_called_once()
        record = fallback.call_args.args[0]
        assert record["event_type"] == "deal.created"

    async def test_fallback_failure_propagates_after_persist(self, mock_storage):
        quote = _make_available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        _authenticate()
        broken_bus = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("bus down")))
        fallback = MagicMock(side_effect=OSError("disk full"))

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
                with (
                    patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
                    patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=broken_bus)),
                    patch("ad_seller.events.helpers.write_audit_fallback", fallback),
                ):
                    resp = await raw.post(
                        "/api/v1/deals",
                        json={
                            "idempotency_key": "idem-fallback-down",
                            "quote_id": quote["quote_id"],
                        },
                    )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 500, resp.text
        deal_ids = [k.removeprefix("deal:") for k in mock_storage._store if k.startswith("deal:")]
        assert len(deal_ids) == 1
        persisted = await mock_storage.get_deal(deal_ids[0])
        assert persisted["deal_id"] == deal_ids[0]
        assert persisted["quote_id"] == quote["quote_id"]
