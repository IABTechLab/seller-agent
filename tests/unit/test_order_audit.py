# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Order Audit & Reporting endpoints (seller-5ks)."""

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

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api import deps as api_deps  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.keys = AsyncMock(
        side_effect=lambda pattern="*": [k for k in store if k.startswith(pattern.rstrip("*"))]
    )
    storage.get_order = AsyncMock(side_effect=lambda oid: store.get(f"order:{oid}"))
    storage.set_order = AsyncMock(
        side_effect=lambda oid, data: store.__setitem__(f"order:{oid}", data)
    )
    storage.list_orders = AsyncMock(
        side_effect=lambda filters=None: [
            v
            for k, v in store.items()
            if k.startswith("order:")
            and (not filters or not filters.get("status") or v.get("status") == filters["status"])
        ]
    )
    storage.get_change_request = AsyncMock(
        side_effect=lambda cid: store.get(f"change_request:{cid}")
    )
    storage.set_change_request = AsyncMock(
        side_effect=lambda cid, data: store.__setitem__(f"change_request:{cid}", data)
    )
    storage.list_change_requests = AsyncMock(
        side_effect=lambda filters=None: [
            v
            for k, v in store.items()
            if k.startswith("change_request:")
            and (
                not filters
                or (
                    (not filters.get("order_id") or v.get("order_id") == filters["order_id"])
                    and (not filters.get("status") or v.get("status") == filters["status"])
                )
            )
        ]
    )
    storage._store = store
    return storage


@pytest.fixture
def client(mock_storage):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    # Order transitions are operator-gated; these tests exercise audit
    # trails, not auth (covered in test_operator_auth.py).
    app.dependency_overrides[api_deps._require_operator_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


async def _create_order_with_transitions(client, transitions, deal_id=None):
    """Helper: create order and walk it through transitions."""
    payload = {"deal_id": deal_id} if deal_id else {}
    r = await client.post("/api/v1/orders", json=payload)
    oid = r.json()["order_id"]
    for status, actor in transitions:
        await client.post(
            f"/api/v1/orders/{oid}/transition",
            json={
                "to_status": status,
                "actor": actor,
            },
        )
    return oid


# =============================================================================
# GET /api/v1/orders/{order_id}/audit
# =============================================================================


class TestOrderAudit:
    async def test_audit_basic(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            oid = await _create_order_with_transitions(
                client,
                [
                    ("submitted", "agent:buyer-001"),
                    ("approved", "human:ops-lead"),
                ],
            )
            resp = await client.get(f"/api/v1/orders/{oid}/audit")

        assert resp.status_code == 200
        data = resp.json()
        assert data["order_id"] == oid
        assert data["current_status"] == "approved"
        assert data["transition_count"] == 2
        assert data["transitions"][0]["actor"] == "agent:buyer-001"
        assert data["transitions"][1]["actor"] == "human:ops-lead"

    async def test_audit_filter_by_actor(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            oid = await _create_order_with_transitions(
                client,
                [
                    ("submitted", "agent:buyer-001"),
                    ("approved", "human:ops-lead"),
                    ("in_progress", "system"),
                ],
            )
            resp = await client.get(f"/api/v1/orders/{oid}/audit?actor=human")

        assert resp.status_code == 200
        data = resp.json()
        assert data["transition_count"] == 1
        assert data["transitions"][0]["actor"] == "human:ops-lead"

    async def test_audit_includes_change_requests(self, client, mock_storage):
        mock_storage._store["order:ORD-AUDIT1"] = {
            "order_id": "ORD-AUDIT1",
            "status": "booked",
            "deal_id": "DEMO-X",
            "metadata": {},
            "audit_log": {"order_id": "ORD-AUDIT1", "transitions": []},
        }
        mock_storage._store["change_request:CR-001"] = {
            "change_request_id": "CR-001",
            "order_id": "ORD-AUDIT1",
            "status": "applied",
            "change_type": "impressions",
        }

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders/ORD-AUDIT1/audit")

        assert resp.status_code == 200
        data = resp.json()
        assert data["change_request_count"] == 1
        assert data["change_requests"][0]["change_request_id"] == "CR-001"

    async def test_audit_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders/ORD-NOPE/audit")
        assert resp.status_code == 404


# =============================================================================
# GET /api/v1/orders/report
# =============================================================================


class TestOrdersReport:
    async def test_empty_report(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders/report")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_orders"] == 0
        assert data["status_counts"] == {}
        assert data["total_transitions"] == 0
        assert data["avg_transitions_per_order"] == 0

    async def test_report_counts_by_status(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            # Create 3 orders in different states
            await _create_order_with_transitions(client, [])  # draft
            await _create_order_with_transitions(
                client,
                [
                    ("submitted", "agent:buyer"),
                ],
            )  # submitted
            await _create_order_with_transitions(
                client,
                [
                    ("submitted", "agent:buyer"),
                    ("approved", "system"),
                ],
            )  # approved

            resp = await client.get("/api/v1/orders/report")

        data = resp.json()
        assert data["total_orders"] == 3
        assert data["status_counts"]["draft"] == 1
        assert data["status_counts"]["submitted"] == 1
        assert data["status_counts"]["approved"] == 1

    async def test_report_transition_stats(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            await _create_order_with_transitions(
                client,
                [
                    ("submitted", "agent:buyer"),
                    ("approved", "human:ops"),
                ],
            )  # 2 transitions
            await _create_order_with_transitions(
                client,
                [
                    ("submitted", "agent:buyer"),
                ],
            )  # 1 transition

            resp = await client.get("/api/v1/orders/report")

        data = resp.json()
        assert data["total_transitions"] == 3
        assert data["avg_transitions_per_order"] == 1.5
        assert data["actor_type_counts"]["agent"] == 2
        assert data["actor_type_counts"]["human"] == 1

    async def test_report_includes_change_request_summary(self, client, mock_storage):
        mock_storage._store["change_request:CR-R1"] = {
            "change_request_id": "CR-R1",
            "status": "applied",
        }
        mock_storage._store["change_request:CR-R2"] = {
            "change_request_id": "CR-R2",
            "status": "pending_approval",
        }
        mock_storage._store["change_request:CR-R3"] = {
            "change_request_id": "CR-R3",
            "status": "applied",
        }

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders/report")

        data = resp.json()
        assert data["change_requests"]["total"] == 3
        assert data["change_requests"]["by_status"]["applied"] == 2
        assert data["change_requests"]["by_status"]["pending_approval"] == 1

    async def test_report_date_filter(self, client, mock_storage):
        # Seed orders with specific created_at dates
        mock_storage._store["order:ORD-OLD"] = {
            "order_id": "ORD-OLD",
            "status": "completed",
            "created_at": "2026-02-01T00:00:00Z",
            "audit_log": {"order_id": "ORD-OLD", "transitions": []},
        }
        mock_storage._store["order:ORD-NEW"] = {
            "order_id": "ORD-NEW",
            "status": "draft",
            "created_at": "2026-03-09T12:00:00Z",
            "audit_log": {"order_id": "ORD-NEW", "transitions": []},
        }

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/orders/report?from_date=2026-03-01")

        data = resp.json()
        assert data["total_orders"] == 1
        assert data["status_counts"].get("draft") == 1
