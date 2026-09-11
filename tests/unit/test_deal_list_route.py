"""GET /api/v1/deals lists stored deals; export reads the same set.

Deals are persisted under ``deal:<id>`` keys by both booking paths, but no
route enumerated them: ``export_deals`` read a ``deal_index`` key that nothing
writes, so it always returned an empty list. These tests pin a list route and
make export read the stored deals.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

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

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402


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

    async def test_export_returns_stored_deals(self, client, mock_storage):
        mock_storage._store["deal:DEMO-A"] = _deal("DEMO-A", "confirmed")
        mock_storage._store["deal:DEMO-B"] = _deal("DEMO-B", "proposed", "PG")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/export?format=generic")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert sorted(d["deal_id"] for d in body["deals"]) == ["DEMO-A", "DEMO-B"]
