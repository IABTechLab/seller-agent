# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Order Workflow API endpoints (seller-cnd)."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

# Stub broken flow modules
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

import httpx
from httpx import ASGITransport

from ad_seller.interfaces.api.main import app, _get_optional_api_key_record


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.delete = AsyncMock(
        side_effect=lambda k: (store.pop(k, None) is not None)
    )
    storage.keys = AsyncMock(
        side_effect=lambda pattern="*": [
            k for k in store if k.startswith(pattern.rstrip("*"))
        ]
    )
    storage.get_order = AsyncMock(side_effect=lambda oid: store.get(f"order:{oid}"))
    storage.set_order = AsyncMock(
        side_effect=lambda oid, data: store.__setitem__(f"order:{oid}", data)
    )
    storage.list_orders = AsyncMock(
        side_effect=lambda filters=None: [
            v for k, v in store.items()
            if k.startswith("order:")
            and (not filters or not filters.get("status") or v.get("status") == filters["status"])
        ]
    )
    storage._store = store
    return storage


@pytest.fixture
def client(mock_storage):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# POST /api/v1/orders
# =============================================================================


class TestCreateOrder:

    async def test_create_order_returns_draft(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/orders", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["order_id"].startswith("ORD-")
        assert data["status"] == "draft"
        assert "created_at" in data
        assert "audit_log" in data

    async def test_create_order_with_deal_id(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/orders", json={
                "deal_id": "DEMO-ABC123",
                "quote_id": "qt-test456",
                "metadata": {"campaign": "spring-2026"},
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["deal_id"] == "DEMO-ABC123"
        assert data["quote_id"] == "qt-test456"
        assert data["metadata"]["campaign"] == "spring-2026"

    async def test_order_persisted_to_storage(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/orders", json={})

        order_id = resp.json()["order_id"]
        stored = mock_storage._store[f"order:{order_id}"]
        assert stored["order_id"] == order_id
        assert stored["status"] == "draft"


# =============================================================================
# GET /api/v1/orders
# =============================================================================


class TestListOrders:

    async def test_list_empty(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        assert resp.json()["orders"] == []

    async def test_list_returns_created_orders(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            await client.post("/api/v1/orders", json={"deal_id": "d1"})
            await client.post("/api/v1/orders", json={"deal_id": "d2"})
            resp = await client.get("/api/v1/orders")

        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    async def test_list_filter_by_status(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            r1 = await client.post("/api/v1/orders", json={})
            oid = r1.json()["order_id"]
            # Transition one to submitted
            await client.post(f"/api/v1/orders/{oid}/transition", json={
                "to_status": "submitted", "actor": "test",
            })
            # Create another that stays draft
            await client.post("/api/v1/orders", json={})

            resp = await client.get("/api/v1/orders?status=submitted")

        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert resp.json()["orders"][0]["status"] == "submitted"


# =============================================================================
# GET /api/v1/orders/{order_id}
# =============================================================================


class TestGetOrder:

    async def test_retrieve_order(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_resp = await client.post("/api/v1/orders", json={"deal_id": "DEMO-X"})
            order_id = create_resp.json()["order_id"]
            resp = await client.get(f"/api/v1/orders/{order_id}")

        assert resp.status_code == 200
        assert resp.json()["order_id"] == order_id
        assert resp.json()["deal_id"] == "DEMO-X"

    async def test_order_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders/ORD-NONEXISTENT")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "order_not_found"


# =============================================================================
# GET /api/v1/orders/{order_id}/history
# =============================================================================


class TestGetOrderHistory:

    async def test_new_order_has_empty_history(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_resp = await client.post("/api/v1/orders", json={})
            order_id = create_resp.json()["order_id"]
            resp = await client.get(f"/api/v1/orders/{order_id}/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["order_id"] == order_id
        assert data["current_status"] == "draft"
        assert data["transition_count"] == 0
        assert data["transitions"] == []

    async def test_history_accumulates_transitions(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_resp = await client.post("/api/v1/orders", json={})
            order_id = create_resp.json()["order_id"]

            await client.post(f"/api/v1/orders/{order_id}/transition", json={
                "to_status": "submitted", "actor": "agent:buyer",
            })
            await client.post(f"/api/v1/orders/{order_id}/transition", json={
                "to_status": "approved", "actor": "human:ops",
            })

            resp = await client.get(f"/api/v1/orders/{order_id}/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["current_status"] == "approved"
        assert data["transition_count"] == 2
        assert data["transitions"][0]["from_status"] == "draft"
        assert data["transitions"][0]["to_status"] == "submitted"
        assert data["transitions"][0]["actor"] == "agent:buyer"
        assert data["transitions"][1]["from_status"] == "submitted"
        assert data["transitions"][1]["to_status"] == "approved"
        assert data["transitions"][1]["actor"] == "human:ops"

    async def test_history_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders/ORD-NOPE/history")

        assert resp.status_code == 404


# =============================================================================
# POST /api/v1/orders/{order_id}/transition
# =============================================================================


class TestTransitionOrder:

    async def test_valid_transition(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_resp = await client.post("/api/v1/orders", json={})
            order_id = create_resp.json()["order_id"]

            resp = await client.post(f"/api/v1/orders/{order_id}/transition", json={
                "to_status": "submitted",
                "actor": "agent:buyer-001",
                "reason": "Ready for review",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "submitted"
        assert data["transition"]["from_status"] == "draft"
        assert data["transition"]["to_status"] == "submitted"
        assert data["transition"]["actor"] == "agent:buyer-001"
        assert "submitted" not in data["allowed_next"]  # can't go back
        assert "approved" in data["allowed_next"]

    async def test_invalid_transition_returns_409(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_resp = await client.post("/api/v1/orders", json={})
            order_id = create_resp.json()["order_id"]

            resp = await client.post(f"/api/v1/orders/{order_id}/transition", json={
                "to_status": "completed",
            })

        assert resp.status_code == 409
        data = resp.json()["detail"]
        assert data["error"] == "invalid_transition"
        assert data["current_status"] == "draft"
        assert "allowed_transitions" in data

    async def test_invalid_status_value_returns_400(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_resp = await client.post("/api/v1/orders", json={})
            order_id = create_resp.json()["order_id"]

            resp = await client.post(f"/api/v1/orders/{order_id}/transition", json={
                "to_status": "banana",
            })

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_status"
        assert "valid_statuses" in resp.json()["detail"]

    async def test_transition_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/orders/ORD-NOPE/transition", json={
                "to_status": "submitted",
            })

        assert resp.status_code == 404

    async def test_full_lifecycle_via_api(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            # Create
            r = await client.post("/api/v1/orders", json={"deal_id": "DEMO-LIFE"})
            oid = r.json()["order_id"]

            # Walk through the full happy path
            transitions = [
                ("submitted", "agent:buyer"),
                ("approved", "system"),
                ("in_progress", "system"),
                ("syncing", "system"),
                ("booked", "ad_server:freewheel"),
                ("completed", "system"),
            ]
            for status, actor in transitions:
                r = await client.post(f"/api/v1/orders/{oid}/transition", json={
                    "to_status": status, "actor": actor,
                })
                assert r.status_code == 200, f"Failed at {status}: {r.json()}"

            # Verify final state
            r = await client.get(f"/api/v1/orders/{oid}")
            assert r.json()["status"] == "completed"

            # Verify full history
            r = await client.get(f"/api/v1/orders/{oid}/history")
            assert r.json()["transition_count"] == 6

    async def test_transition_preserves_extra_fields(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            r = await client.post("/api/v1/orders", json={
                "deal_id": "DEMO-KEEP", "metadata": {"important": True},
            })
            oid = r.json()["order_id"]

            await client.post(f"/api/v1/orders/{oid}/transition", json={
                "to_status": "submitted",
            })

            r = await client.get(f"/api/v1/orders/{oid}")

        assert r.json()["deal_id"] == "DEMO-KEEP"
        assert r.json()["metadata"]["important"] is True
