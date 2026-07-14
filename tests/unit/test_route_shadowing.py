# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""EP-8.4 route-shadowing regression tests.

FastAPI matches routes in registration order, so a literal path registered
AFTER a same-prefix ``{param}`` path on the same HTTP method is shadowed: the
request is captured by the catch-all handler and never reaches the literal
endpoint.

The known shadow was ``GET /api/v1/deals/export`` being captured by
``GET /api/v1/deals/{deal_id}`` (deal_id="export"), which returned a
``deal_not_found`` 404 instead of the export payload. EP-8.4 reorders the
literal ahead of the ``{deal_id}`` route.

``GET /api/v1/orders/report`` was already registered before
``GET /api/v1/orders/{order_id}``; the test here locks that ordering in as a
regression guard.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch) — mirrors tests/unit/test_deal_booking_endpoints.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402


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
    storage._store = store
    return storage


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# GET /api/v1/deals/export  — previously shadowed by /api/v1/deals/{deal_id}
# =============================================================================


class TestDealsExportNotShadowed:
    async def test_export_route_registered_before_param_route(self):
        """The literal export path must precede the {deal_id} catch-all."""
        get_deal_paths = [
            r.path
            for r in app.routes
            if getattr(r, "path", "").startswith("/api/v1/deals")
            and "GET" in getattr(r, "methods", set())
        ]
        assert "/api/v1/deals/export" in get_deal_paths
        assert "/api/v1/deals/{deal_id}" in get_deal_paths
        assert get_deal_paths.index("/api/v1/deals/export") < get_deal_paths.index(
            "/api/v1/deals/{deal_id}"
        ), "export literal must be registered before the {deal_id} catch-all"

    async def test_export_reaches_export_handler(self, client, mock_storage):
        """GET /api/v1/deals/export returns the export payload, not a 404."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/export")

        assert resp.status_code == 200
        body = resp.json()
        # Export handler response shape (services.deal_service.export_deals) —
        # NOT the {deal_id} handler's `deal_not_found` error.
        assert body["format"] == "generic"
        assert "deals" in body
        assert "count" in body
        assert "detail" not in body

    async def test_export_honors_format_query_param(self, client, mock_storage):
        """The export handler (not the deal-lookup handler) parses ?format=."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/export?format=ttd")

        assert resp.status_code == 200
        assert resp.json()["format"] == "ttd"

    async def test_deal_id_lookup_still_works(self, client, mock_storage):
        """Reordering must not break the {deal_id} catch-all for real IDs."""
        mock_storage._store["deal:DEMO-REALDEAL123"] = {
            "deal_id": "DEMO-REALDEAL123",
            "deal_type": "PD",
            "status": "active",
            "quote_id": "qt-real",
            "product": {"product_id": "ctv-premium", "name": "CTV", "inventory_type": "ctv"},
            "pricing": {"base_cpm": 30.0, "final_cpm": 30.0, "currency": "USD"},
            "terms": {"impressions": 1000000, "flight_start": "2026-04-01", "flight_end": "2026-04-30"},
        }
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/DEMO-REALDEAL123")

        assert resp.status_code == 200
        assert resp.json()["deal"]["deal_id"] == "DEMO-REALDEAL123"


# =============================================================================
# GET /api/v1/orders/report — regression guard (already correctly ordered)
# =============================================================================


class TestOrdersReportNotShadowed:
    async def test_report_route_registered_before_param_route(self):
        get_order_paths = [
            r.path
            for r in app.routes
            if getattr(r, "path", "").startswith("/api/v1/orders")
            and "GET" in getattr(r, "methods", set())
        ]
        assert "/api/v1/orders/report" in get_order_paths
        assert "/api/v1/orders/{order_id}" in get_order_paths
        assert get_order_paths.index("/api/v1/orders/report") < get_order_paths.index(
            "/api/v1/orders/{order_id}"
        ), "report literal must be registered before the {order_id} catch-all"

    async def test_report_reaches_report_handler(self, client, mock_storage):
        """GET /api/v1/orders/report returns the report payload, not a lookup."""
        with patch(
            "ad_seller.services.order_service.get_orders_report",
            new=AsyncMock(return_value={"total_orders": 0, "by_status": {}}),
        ) as mocked:
            resp = await client.get("/api/v1/orders/report")

        assert resp.status_code == 200
        # The report handler was invoked (not get_order with order_id="report").
        mocked.assert_awaited_once()
        assert "by_status" in resp.json()
