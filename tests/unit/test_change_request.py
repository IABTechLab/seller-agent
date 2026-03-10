# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Change Request Management (seller-ju5)."""

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
from ad_seller.models.change_request import (
    ChangeRequest,
    ChangeSeverity,
    ChangeType,
    FieldDiff,
    classify_severity,
    validate_change_request,
)


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
        side_effect=lambda pattern="*": [
            k for k in store if k.startswith(pattern.rstrip("*"))
        ]
    )
    storage.get_order = AsyncMock(side_effect=lambda oid: store.get(f"order:{oid}"))
    storage.set_order = AsyncMock(
        side_effect=lambda oid, data: store.__setitem__(f"order:{oid}", data)
    )
    storage.get_change_request = AsyncMock(side_effect=lambda cid: store.get(f"change_request:{cid}"))
    storage.set_change_request = AsyncMock(
        side_effect=lambda cid, data: store.__setitem__(f"change_request:{cid}", data)
    )
    storage.list_change_requests = AsyncMock(
        side_effect=lambda filters=None: [
            v for k, v in store.items()
            if k.startswith("change_request:")
            and (not filters or (
                (not filters.get("order_id") or v.get("order_id") == filters["order_id"])
                and (not filters.get("status") or v.get("status") == filters["status"])
            ))
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


def _seed_order(mock_storage, order_id="ORD-TEST001", status="booked"):
    """Put an order into storage for change request tests."""
    mock_storage._store[f"order:{order_id}"] = {
        "order_id": order_id,
        "status": status,
        "deal_id": "DEMO-ABC123",
        "metadata": {"campaign": "spring-2026"},
        "audit_log": {"order_id": order_id, "transitions": []},
    }


# =============================================================================
# Model unit tests
# =============================================================================


class TestClassifySeverity:

    def test_creative_is_minor(self):
        assert classify_severity(ChangeType.CREATIVE, []) == ChangeSeverity.MINOR

    def test_pricing_is_critical(self):
        assert classify_severity(ChangeType.PRICING, []) == ChangeSeverity.CRITICAL

    def test_cancellation_is_critical(self):
        assert classify_severity(ChangeType.CANCELLATION, []) == ChangeSeverity.CRITICAL

    def test_small_flight_shift_is_minor(self):
        diffs = [FieldDiff(field="flight_start", old_value="2026-04-01", new_value="2026-04-02")]
        assert classify_severity(ChangeType.FLIGHT_DATES, diffs) == ChangeSeverity.MINOR

    def test_large_flight_shift_is_material(self):
        diffs = [FieldDiff(field="flight_start", old_value="2026-04-01", new_value="2026-05-01")]
        assert classify_severity(ChangeType.FLIGHT_DATES, diffs) == ChangeSeverity.MATERIAL

    def test_large_price_change_stays_critical(self):
        diffs = [FieldDiff(field="final_cpm", old_value=30.0, new_value=10.0)]
        assert classify_severity(ChangeType.PRICING, diffs) == ChangeSeverity.CRITICAL


class TestValidateChangeRequest:

    def test_valid_change_on_booked_order(self):
        cr = ChangeRequest(
            order_id="ORD-1", change_type=ChangeType.IMPRESSIONS,
            diffs=[FieldDiff(field="impressions", new_value=1000000)],
        )
        errors = validate_change_request(cr, {"status": "booked"})
        assert errors == []

    def test_cannot_modify_completed_order(self):
        cr = ChangeRequest(order_id="ORD-1", change_type=ChangeType.FLIGHT_DATES)
        errors = validate_change_request(cr, {"status": "completed"})
        assert any("completed" in e for e in errors)

    def test_cannot_modify_cancelled_order(self):
        cr = ChangeRequest(order_id="ORD-1", change_type=ChangeType.FLIGHT_DATES)
        errors = validate_change_request(cr, {"status": "cancelled"})
        assert len(errors) > 0

    def test_negative_impressions_rejected(self):
        cr = ChangeRequest(
            order_id="ORD-1", change_type=ChangeType.IMPRESSIONS,
            diffs=[FieldDiff(field="impressions", new_value=-500)],
        )
        errors = validate_change_request(cr, {"status": "booked"})
        assert any("positive" in e for e in errors)


# =============================================================================
# POST /api/v1/change-requests
# =============================================================================


class TestCreateChangeRequest:

    async def test_create_minor_auto_approved(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001",
                "change_type": "creative",
                "reason": "Swap banner creative",
                "requested_by": "agent:buyer",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["change_request_id"].startswith("CR-")
        assert data["status"] == "approved"  # auto-approved (minor)
        assert data["severity"] == "minor"
        assert data["approved_by"] == "system:auto-approve"

    async def test_create_material_needs_approval(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001",
                "change_type": "impressions",
                "diffs": [{"field": "impressions", "old_value": 5000000, "new_value": 8000000}],
                "reason": "Increase campaign reach",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending_approval"
        assert data["severity"] == "material"

    async def test_create_with_rollback_snapshot(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001",
                "change_type": "flight_dates",
                "diffs": [{"field": "flight_start", "old_value": "2026-04-01", "new_value": "2026-05-01"}],
            })

        data = resp.json()
        assert data["rollback_snapshot"]["order_id"] == "ORD-TEST001"

    async def test_order_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-NOPE",
                "change_type": "creative",
            })
        assert resp.status_code == 404

    async def test_invalid_change_type(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001",
                "change_type": "banana",
            })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_change_type"

    async def test_validation_failure_on_completed_order(self, client, mock_storage):
        _seed_order(mock_storage, status="completed")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001",
                "change_type": "impressions",
                "diffs": [{"field": "impressions", "new_value": 1000000}],
            })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "validation_failed"


# =============================================================================
# GET /api/v1/change-requests
# =============================================================================


class TestListChangeRequests:

    async def test_list_empty(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/change-requests")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_list_filtered_by_order(self, client, mock_storage):
        _seed_order(mock_storage, order_id="ORD-A")
        _seed_order(mock_storage, order_id="ORD-B")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-A", "change_type": "creative",
            })
            await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-B", "change_type": "creative",
            })
            resp = await client.get("/api/v1/change-requests?order_id=ORD-A")

        assert resp.status_code == 200
        assert resp.json()["count"] == 1


# =============================================================================
# GET /api/v1/change-requests/{cr_id}
# =============================================================================


class TestGetChangeRequest:

    async def test_retrieve(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_resp = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001", "change_type": "creative",
            })
            cr_id = create_resp.json()["change_request_id"]
            resp = await client.get(f"/api/v1/change-requests/{cr_id}")

        assert resp.status_code == 200
        assert resp.json()["change_request_id"] == cr_id

    async def test_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/change-requests/CR-NOPE")
        assert resp.status_code == 404


# =============================================================================
# POST /api/v1/change-requests/{cr_id}/review
# =============================================================================


class TestReviewChangeRequest:

    async def test_approve_pending_request(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            cr = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001", "change_type": "impressions",
                "diffs": [{"field": "impressions", "old_value": 5000000, "new_value": 8000000}],
            })
            cr_id = cr.json()["change_request_id"]
            assert cr.json()["status"] == "pending_approval"

            resp = await client.post(f"/api/v1/change-requests/{cr_id}/review", json={
                "decision": "approve", "decided_by": "human:ops-lead",
            })

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert resp.json()["approved_by"] == "human:ops-lead"

    async def test_reject_pending_request(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            cr = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001", "change_type": "cancellation",
                "reason": "Client wants out",
            })
            cr_id = cr.json()["change_request_id"]

            resp = await client.post(f"/api/v1/change-requests/{cr_id}/review", json={
                "decision": "reject", "decided_by": "human:manager",
                "reason": "Contract binding",
            })

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert resp.json()["rejection_reason"] == "Contract binding"

    async def test_cannot_review_already_approved(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            # Creative is auto-approved (minor)
            cr = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001", "change_type": "creative",
            })
            cr_id = cr.json()["change_request_id"]

            resp = await client.post(f"/api/v1/change-requests/{cr_id}/review", json={
                "decision": "approve", "decided_by": "human:ops",
            })

        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "not_pending_approval"

    async def test_invalid_decision(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            cr = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001", "change_type": "impressions",
                "diffs": [{"field": "impressions", "old_value": 5000000, "new_value": 8000000}],
            })
            cr_id = cr.json()["change_request_id"]

            resp = await client.post(f"/api/v1/change-requests/{cr_id}/review", json={
                "decision": "maybe",
            })

        assert resp.status_code == 400


# =============================================================================
# POST /api/v1/change-requests/{cr_id}/apply
# =============================================================================


class TestApplyChangeRequest:

    async def test_apply_approved_request(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            # Create material CR
            cr = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001", "change_type": "impressions",
                "diffs": [{"field": "impressions", "old_value": 5000000, "new_value": 8000000}],
                "proposed_values": {"impressions": 8000000},
            })
            cr_id = cr.json()["change_request_id"]

            # Approve
            await client.post(f"/api/v1/change-requests/{cr_id}/review", json={
                "decision": "approve", "decided_by": "human:ops",
            })

            # Apply
            resp = await client.post(f"/api/v1/change-requests/{cr_id}/apply")

        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

        # Verify order was updated
        order = mock_storage._store["order:ORD-TEST001"]
        assert order["metadata"]["impressions"] == 8000000

        # Verify CR marked as applied
        stored_cr = mock_storage._store[f"change_request:{cr_id}"]
        assert stored_cr["status"] == "applied"

    async def test_cannot_apply_unapproved(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            cr = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001", "change_type": "impressions",
                "diffs": [{"field": "impressions", "old_value": 5000000, "new_value": 8000000}],
            })
            cr_id = cr.json()["change_request_id"]

            resp = await client.post(f"/api/v1/change-requests/{cr_id}/apply")

        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "not_approved"

    async def test_apply_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests/CR-NOPE/apply")
        assert resp.status_code == 404


# =============================================================================
# End-to-End: Create → Review → Apply
# =============================================================================


class TestChangeRequestFlow:

    async def test_full_material_change_flow(self, client, mock_storage):
        _seed_order(mock_storage)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            # 1. Create change request (material → pending_approval)
            cr = await client.post("/api/v1/change-requests", json={
                "order_id": "ORD-TEST001",
                "change_type": "flight_dates",
                "diffs": [
                    {"field": "flight_start", "old_value": "2026-04-01", "new_value": "2026-05-01"},
                    {"field": "flight_end", "old_value": "2026-04-30", "new_value": "2026-05-31"},
                ],
                "proposed_values": {"flight_start": "2026-05-01", "flight_end": "2026-05-31"},
                "requested_by": "agent:buyer-001",
                "reason": "Advertiser delayed product launch",
            })
            assert cr.status_code == 200
            cr_id = cr.json()["change_request_id"]
            assert cr.json()["status"] == "pending_approval"

            # 2. Approve
            review = await client.post(f"/api/v1/change-requests/{cr_id}/review", json={
                "decision": "approve", "decided_by": "human:traffic-ops",
            })
            assert review.status_code == 200
            assert review.json()["status"] == "approved"

            # 3. Apply
            apply_resp = await client.post(f"/api/v1/change-requests/{cr_id}/apply")
            assert apply_resp.status_code == 200
            assert apply_resp.json()["status"] == "applied"

            # 4. Verify order updated
            order = await client.get("/api/v1/orders/ORD-TEST001")
            assert order.json()["metadata"]["flight_start"] == "2026-05-01"

            # 5. Verify CR in list
            crs = await client.get("/api/v1/change-requests?order_id=ORD-TEST001")
            assert crs.json()["count"] == 1
            assert crs.json()["change_requests"][0]["status"] == "applied"
