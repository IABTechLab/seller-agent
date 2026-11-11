"""Issue #123: curated deals read back with their price and a valid deal type.

Runs through the real app against a real SQLite backend: create via
``POST /api/v1/deals/curated`` (or the MCP ``create_curated_deal`` tool),
then read back via ``GET /api/v1/deals/{deal_id}`` and ``GET /api/v1/deals``.
"""

import json
import sys
from types import ModuleType
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
from ad_seller.interfaces.api import deps  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.storage.sqlite_backend import SQLiteBackend  # noqa: E402
from tests.unit.test_curated_deal import _catalog, _make_product  # noqa: E402

_PRODUCT_ID = "ctv-premium-sports"


@pytest.fixture
async def storage(tmp_path):
    backend = SQLiteBackend(f"sqlite:///{tmp_path}/curated.db")
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


@pytest.fixture
def catalog():
    return _catalog(_make_product(product_id=_PRODUCT_ID, base_cpm=45.0, floor_cpm=35.0))


@pytest.fixture
async def client(storage, event_bus, catalog):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    app.dependency_overrides[deps._require_operator_api_key_record] = lambda: MagicMock()
    with (
        patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
        patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            AsyncMock(return_value=catalog),
        ),
        patch(
            "ad_seller.services.catalog_service.get_static_product_catalog",
            AsyncMock(return_value=catalog),
        ),
    ):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    app.dependency_overrides.clear()


async def _create(client, **body):
    return await client.post("/api/v1/deals/curated", json={"curator_id": "agent-range", **body})


async def _read_back(client, deal_id):
    resp = await client.get(f"/api/v1/deals/{deal_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["deal"]


def _cpm(money):
    return None if money is None else money["amount_micros"] / 1_000_000


class TestCuratedReadBack:
    async def test_priced_deal_reads_back_with_base_and_total(self, client):
        created = await _create(client, deal_type="PD", product_id=_PRODUCT_ID)
        assert created.status_code == 201, created.text
        pricing = created.json()["pricing"]

        deal = await _read_back(client, created.json()["deal_id"])

        assert deal["deal_type"] == "PD"
        assert deal["status"] == "booked"
        assert deal["product"]["product_id"] == _PRODUCT_ID
        assert _cpm(deal["pricing"]["base_cpm"]) == pricing["base_cpm"] == 45.0
        assert _cpm(deal["pricing"]["final_cpm"]) == pytest.approx(pricing["total_cpm"])
        assert _cpm(deal["pricing"]["final_cpm"]) == pytest.approx(49.5)
        assert deal["pricing"]["final_cpm"]["currency"] == "USD"

    async def test_deal_without_product_reads_back(self, client):
        created = await _create(client, deal_type="PA")
        assert created.status_code == 201, created.text

        deal = await _read_back(client, created.json()["deal_id"])

        assert deal["product"]["product_id"] == ""
        assert _cpm(deal["pricing"]["base_cpm"]) == 12.0
        assert _cpm(deal["pricing"]["final_cpm"]) == pytest.approx(13.2)

    async def test_impressions_and_flight_still_read_back(self, client):
        created = await _create(
            client,
            deal_type="PG",
            product_id=_PRODUCT_ID,
            impressions=750_000,
            flight_start="2026-12-01",
            flight_end="2026-12-31",
        )

        deal = await _read_back(client, created.json()["deal_id"])

        assert deal["terms"]["impressions"] == 750_000
        assert deal["terms"]["flight_start"] == "2026-12-01"
        assert deal["terms"]["flight_end"] == "2026-12-31"

    async def test_list_returns_curated_deals_instead_of_skipping_them(self, client):
        ids = set()
        for body in ({}, {"deal_type": "PMP"}, {"deal_type": "pd", "product_id": _PRODUCT_ID}):
            created = await _create(client, **body)
            assert created.status_code == 201, created.text
            ids.add(created.json()["deal_id"])

        resp = await client.get("/api/v1/deals")

        assert resp.status_code == 200
        body = resp.json()
        assert body["skipped"] == []
        assert {d["deal"]["deal_id"] for d in body["deals"]} == ids


class TestCuratedDealType:
    @pytest.mark.parametrize(
        "sent, stored",
        [
            (None, "PA"),
            ("PA", "PA"),
            ("PD", "PD"),
            ("PG", "PG"),
            ("pa", "PA"),
            ("Pd", "PD"),
            ("PMP", "PA"),
            ("pmp", "PA"),
            ("PREFERRED", "PD"),
            ("preferred", "PD"),
            ("PRIVATE_AUCTION", "PA"),
            ("PREFERRED_DEAL", "PD"),
            ("PROGRAMMATIC_GUARANTEED", "PG"),
        ],
    )
    async def test_accepted_spellings_are_stored_canonical(
        self, client, storage, event_bus, sent, stored
    ):
        body = {} if sent is None else {"deal_type": sent}
        created = await _create(client, product_id=_PRODUCT_ID, impressions=1000, **body)
        assert created.status_code == 201, created.text
        deal_id = created.json()["deal_id"]

        assert created.json()["deal_type"] == stored
        assert (await storage.get_deal(deal_id))["deal_type"] == stored
        assert (await _read_back(client, deal_id))["deal_type"] == stored
        events = await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert [e.payload["deal_type"] for e in events] == [stored]

    @pytest.mark.parametrize(
        "sent",
        ["BOGUS", "", " PA ", "P A", "PA,PD", "PMPX", "PRIVATE MARKETPLACE", "X" * 10_000],
    )
    async def test_unknown_deal_type_is_400_and_nothing_is_created(
        self, client, storage, event_bus, sent
    ):
        created = await _create(client, deal_type=sent, product_id=_PRODUCT_ID)

        assert created.status_code == 400, created.text
        assert created.json()["detail"]["error"] == "invalid_deal_type"
        assert await storage.list_deals() == []
        assert await event_bus.list_events(event_type=EventType.DEAL_CREATED.value) == []

    @pytest.mark.parametrize("sent", [None, 5, ["PA"], {"code": "PA"}])
    async def test_non_string_deal_type_is_rejected_by_the_schema(self, client, storage, sent):
        created = await _create(client, deal_type=sent)

        assert created.status_code == 422, created.text
        assert await storage.list_deals() == []

    async def test_deal_type_is_checked_before_the_curator(self, client):
        created = await client.post(
            "/api/v1/deals/curated", json={"curator_id": "nobody", "deal_type": "BOGUS"}
        )

        assert created.status_code == 400
        assert created.json()["detail"]["error"] == "invalid_deal_type"

    @pytest.mark.parametrize(
        "body, status, error",
        [
            ({"curator_id": "nobody"}, 404, "curator_not_found"),
            ({"product_id": "no-such-product"}, 404, "product_not_found"),
            ({"product_id": _PRODUCT_ID, "max_cpm": 1.0}, 422, "below_curated_floor"),
        ],
    )
    async def test_existing_rejections_are_unchanged(self, client, storage, body, status, error):
        created = await client.post(
            "/api/v1/deals/curated", json={"curator_id": "agent-range", "deal_type": "PA", **body}
        )

        assert created.status_code == status, created.text
        assert created.json()["detail"]["error"] == error
        assert await storage.list_deals() == []


class TestStoredBeforeTheFix:
    @pytest.mark.parametrize(
        "stored, wire", [("PMP", "PA"), ("pmp", "PA"), ("preferred", "PD"), ("pd", "PD")]
    )
    async def test_legacy_deal_types_read_back(self, client, storage, stored, wire):
        await storage.set_deal("CUR-LEGACY", _legacy_curated_row(deal_type=stored))

        deal = await _read_back(client, "CUR-LEGACY")

        assert deal["deal_type"] == wire
        assert _cpm(deal["pricing"]["final_cpm"]) == pytest.approx(16.5)
        assert (await storage.get_deal("CUR-LEGACY"))["deal_type"] == stored

    @pytest.mark.parametrize("stored", ["BOGUS", None, 7])
    async def test_unreadable_rows_are_skipped_by_the_list(self, client, storage, stored):
        await storage.set_deal("CUR-BAD", _legacy_curated_row(stored, "CUR-BAD"))
        await storage.set_deal("CUR-GOOD", _legacy_curated_row("PMP", "CUR-GOOD"))

        resp = await client.get("/api/v1/deals")

        assert resp.status_code == 200
        assert resp.json()["skipped"] == ["CUR-BAD"]
        assert [d["deal"]["deal_id"] for d in resp.json()["deals"]] == ["CUR-GOOD"]


class TestMcpCreateCuratedDeal:
    async def test_default_deal_type_reads_back(self, client, storage):
        from ad_seller.interfaces import mcp_server

        result = json.loads(
            await mcp_server.create_curated_deal(curator_id="agent-range", product_id=_PRODUCT_ID)
        )

        assert result["deal_type"] == "PA"
        deal = await _read_back(client, result["deal_id"])
        assert deal["deal_type"] == "PA"
        assert _cpm(deal["pricing"]["final_cpm"]) == pytest.approx(49.5)

    async def test_legacy_spelling_is_normalised(self, client, storage):
        from ad_seller.interfaces import mcp_server

        result = json.loads(
            await mcp_server.create_curated_deal(curator_id="agent-range", deal_type="PMP")
        )

        assert (await storage.get_deal(result["deal_id"]))["deal_type"] == "PA"

    async def test_unknown_deal_type_returns_the_error_and_stores_nothing(self, client, storage):
        from ad_seller.interfaces import mcp_server

        result = json.loads(
            await mcp_server.create_curated_deal(curator_id="agent-range", deal_type="BOGUS")
        )

        assert result["detail"]["error"] == "invalid_deal_type"
        assert await storage.list_deals() == []


def _legacy_curated_row(deal_type, deal_id="CUR-LEGACY"):
    return {
        "deal_id": deal_id,
        "deal_type": deal_type,
        "status": "confirmed",
        "product_id": None,
        "base_cpm": 15.0,
        "curator_fee_cpm": 1.5,
        "total_cpm": 16.5,
        "currency": "USD",
        "impressions": None,
        "flight_start": "2026-11-01",
        "flight_end": "2026-12-01",
        "buyer_seat_ids": [],
        "created_at": "2026-11-01T00:00:00Z",
    }
