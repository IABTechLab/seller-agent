"""GET /api/v1/deals lists stored deals; export reads the same set.

Deals are persisted under ``deal:<id>`` keys by both booking paths, but no
route enumerated them: ``export_deals`` read a ``deal_index`` key that nothing
writes, so it always returned an empty list. These tests pin a list route and
make export read the stored deals.
"""

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

from ad_seller.interfaces.api import deps  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.storage.sqlite_backend import SQLiteBackend  # noqa: E402
from tests.unit.test_deal_booking_endpoints import (  # noqa: E402
    _authenticate,
    _make_available_quote,
)
from tests.unit.test_trust_tier_verification import _mock_catalog  # noqa: E402


def _deal(deal_id: str, status: str, deal_type: str = "PD") -> dict:
    return {
        "deal_id": deal_id,
        "deal_type": deal_type,
        "status": status,
        "quote_id": f"qt-{deal_id}",
        "product": {"product_id": "ctv-premium", "name": "CTV", "inventory_type": "ctv"},
        "pricing": {"base_cpm": 30.0, "final_cpm": 30.0, "currency": "USD"},
        "terms": {
            "impressions": 1000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
        },
    }


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage.list_deals = AsyncMock(
        side_effect=lambda: [v for k, v in store.items() if k.startswith("deal:")]
    )
    storage._store = store
    return storage


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    app.dependency_overrides[deps._require_operator_api_key_record] = lambda: MagicMock()
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


class TestDealList:
    async def test_list_route_registered(self):
        get_paths = [
            r.path
            for r in app.routes
            if getattr(r, "path", "") == "/api/v1/deals" and "GET" in getattr(r, "methods", set())
        ]
        assert get_paths == ["/api/v1/deals"]

    async def test_list_requires_operator_key(self, client, mock_storage):
        app.dependency_overrides.pop(deps._require_operator_api_key_record)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals")

        assert resp.status_code == 401

    async def test_list_returns_every_stored_deal(self, client, mock_storage):
        mock_storage._store["deal:DEMO-A"] = _deal("DEMO-A", "confirmed")
        mock_storage._store["deal:DEMO-B"] = _deal("DEMO-B", "proposed", "PG")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert sorted(d["deal"]["deal_id"] for d in body["deals"]) == ["DEMO-A", "DEMO-B"]

    async def test_list_filters_by_status(self, client, mock_storage):
        mock_storage._store["deal:DEMO-A"] = _deal("DEMO-A", "confirmed")
        mock_storage._store["deal:DEMO-B"] = _deal("DEMO-B", "proposed", "PG")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals?status=proposed")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["deals"][0]["deal"]["deal_id"] == "DEMO-B"

    async def test_list_filters_on_wire_status(self, client, mock_storage):
        mock_storage._store["deal:DEMO-A"] = _deal("DEMO-A", "confirmed")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            booked = await client.get("/api/v1/deals?status=booked")
            internal = await client.get("/api/v1/deals?status=confirmed")

        assert booked.status_code == 200
        assert booked.json()["count"] == 1
        assert booked.json()["deals"][0]["deal"]["deal_id"] == "DEMO-A"
        assert internal.status_code == 422

    async def test_list_skips_unserializable_record(self, client, mock_storage):
        mock_storage._store["deal:DEMO-A"] = _deal("DEMO-A", "confirmed")
        mock_storage._store["deal:DEMO-B"] = _deal("DEMO-B", "weird")
        mock_storage._store["deal:DEMO-C"] = _deal("DEMO-C", "confirmed", deal_type=None)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["deals"][0]["deal"]["deal_id"] == "DEMO-A"
        assert sorted(body["skipped"]) == ["DEMO-B", "DEMO-C"]

    async def test_export_returns_stored_deals(self, client, mock_storage):
        mock_storage._store["deal:DEMO-A"] = _deal("DEMO-A", "confirmed")
        mock_storage._store["deal:DEMO-B"] = _deal("DEMO-B", "proposed", "PG")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/export?format=generic")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert sorted(d["deal_id"] for d in body["deals"]) == ["DEMO-A", "DEMO-B"]

    async def test_real_backend_scan_lists_stored_deals(self, client, tmp_path):
        storage = SQLiteBackend(f"sqlite:///{tmp_path}/t.db")
        await storage.connect()
        await storage.set_deal("DEMO-A", _deal("DEMO-A", "confirmed"))
        await storage.set_deal("DEMO-B", _deal("DEMO-B", "proposed", "PG"))
        await storage.set("idempotency:x", {"deal_id": "DEMO-A", "payload_hash": "h"})
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            resp = await client.get("/api/v1/deals")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert sorted(d["deal"]["deal_id"] for d in body["deals"]) == ["DEMO-A", "DEMO-B"]

    async def test_list_empty_store(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals")

        assert resp.status_code == 200
        assert resp.json() == {"deals": [], "count": 0, "skipped": []}

    async def test_list_non_matching_filter(self, client, mock_storage):
        mock_storage._store["deal:DEMO-A"] = _deal("DEMO-A", "confirmed")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals?status=cancelled")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_export_lists_deals_booked_through_both_routes(self, client, tmp_path):
        storage = SQLiteBackend(f"sqlite:///{tmp_path}/t.db")
        await storage.connect()
        quote = _make_available_quote()
        await storage.set_quote(quote["quote_id"], quote)
        _authenticate()
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(),
            ),
        ):
            booked = await client.post(
                "/api/v1/deals",
                json={"idempotency_key": "idem-list", "quote_id": quote["quote_id"]},
            )
            templated = await client.post(
                "/api/v1/deals/from-template",
                json={
                    "deal_type": "PD",
                    "product_id": "ctv-premium-sports",
                    "impressions": 1000000,
                },
            )
            exported = await client.get("/api/v1/deals/export?format=generic")

        assert booked.status_code == 200, booked.text
        assert templated.status_code == 201, templated.text
        expected = sorted([booked.json()["deal"]["deal_id"], templated.json()["deal_id"]])
        assert exported.status_code == 200
        body = exported.json()
        assert body["count"] == 2
        assert sorted(d["deal_id"] for d in body["deals"]) == expected
