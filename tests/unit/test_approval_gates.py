# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for EP-4.5: mandatory, authenticated approval gates.

Covers the four guarantees:

(a) Unauthenticated approval decisions are rejected (401).
(b) An authenticated decision records the VERIFIED principal derived from
    the API key — not the arbitrary free-text ``decided_by`` body field.
(c) A deal whose value is at/above ``approval_required_above_value`` is
    forced into PENDING_APPROVAL even when the global opt-in toggle is off
    (it can never auto-finalize).
(d) A below-threshold deal with the toggle off keeps the original
    auto-finalize behavior (backward compatible).
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch) before any import of ad_seller.flows triggers __init__.py.
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

from ad_seller.events.bus import InMemoryEventBus  # noqa: E402
from ad_seller.events.models import ApprovalRequest, ApprovalStatus  # noqa: E402
from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.models.api_key import (  # noqa: E402
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402
from ad_seller.models.flow_state import ExecutionStatus  # noqa: E402

# =============================================================================
# Helpers / fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage._store = store
    return storage


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _seed_key(store, *, key_id="key-approver", agency_id="agency-approve"):
    """Seed a valid API key record; return the raw key."""
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(agency_id=agency_id, agency_name="Acme Agency"),
        label="Approval test key",
    )
    store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    return raw_key


def _seed_pending_approval(store, *, proposal_id="prop-1"):
    """Seed a PENDING approval request directly into storage; return its id."""
    req = ApprovalRequest(
        event_id="evt-1",
        flow_id="flow-1",
        flow_type="proposal_handling",
        gate_name="proposal_decision",
        proposal_id=proposal_id,
        status=ApprovalStatus.PENDING,
    )
    store[f"approval:{req.approval_id}"] = req.model_dump(mode="json")
    store["approval_index:pending"] = [req.approval_id]
    return req.approval_id


def _run_execute_decision(flow, settings_ns):
    import asyncio

    with patch(
        "ad_seller.flows.proposal_handling_flow.get_settings",
        return_value=settings_ns,
    ):
        asyncio.run(flow.execute_decision())


# =============================================================================
# (a) + (b) Authenticated approval endpoints
# =============================================================================


class TestApprovalEndpointAuth:
    async def test_unauthenticated_decide_is_rejected(self, client, mock_storage):
        """POST /approvals/{id}/decide with no credential must be 401."""
        approval_id = _seed_pending_approval(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                f"/approvals/{approval_id}/decide",
                json={"decision": "approve", "decided_by": "anyone"},
            )
        assert resp.status_code == 401
        # And the approval must remain untouched (still pending).
        assert mock_storage._store[f"approval:{approval_id}"]["status"] == "pending"

    async def test_unauthenticated_list_and_resume_rejected(self, client, mock_storage):
        """The list and resume endpoints also require authentication."""
        approval_id = _seed_pending_approval(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            list_resp = await client.get("/approvals")
            resume_resp = await client.post(f"/approvals/{approval_id}/resume")
        assert list_resp.status_code == 401
        assert resume_resp.status_code == 401

    async def test_authenticated_decide_records_verified_principal(
        self, client, mock_storage
    ):
        """A valid key authorizes the decision and the audit record stamps the
        VERIFIED principal, not the arbitrary ``decided_by`` body value."""
        raw_key = _seed_key(mock_storage._store, key_id="key-approver", agency_id="agency-approve")
        approval_id = _seed_pending_approval(mock_storage._store)

        bus = InMemoryEventBus()
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage), patch(
            "ad_seller.events.bus.get_event_bus", new_callable=AsyncMock, return_value=bus
        ):
            resp = await client.post(
                f"/approvals/{approval_id}/decide",
                json={"decision": "approve", "decided_by": "attacker-claims-to-be-ceo"},
                headers={"X-Api-Key": raw_key},
            )

        assert resp.status_code == 200
        body = resp.json()
        # Display field preserved verbatim...
        assert body["decided_by"] == "attacker-claims-to-be-ceo"
        # ...but the trusted principal is derived from the authenticated key.
        assert body["decided_by_principal"] == "apikey:key-approver:agency-approve"
        # Persisted audit record carries the verified principal too.
        stored = mock_storage._store[f"approval_response:{approval_id}"]
        assert stored["decided_by_principal"] == "apikey:key-approver:agency-approve"

    async def test_invalid_key_decide_rejected(self, client, mock_storage):
        """An unknown key is rejected with 401, decision not applied."""
        approval_id = _seed_pending_approval(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                f"/approvals/{approval_id}/decide",
                json={"decision": "approve"},
                headers={"X-Api-Key": "ask_live_not-a-real-key"},
            )
        assert resp.status_code == 401
        assert mock_storage._store[f"approval:{approval_id}"]["status"] == "pending"


# =============================================================================
# (c) + (d) Threshold-driven mandatory gate
# =============================================================================


class TestThresholdGate:
    def _flow(self, price, impressions, recommendation="accept"):
        flow = ProposalHandlingFlow()
        flow.state.proposal_id = "prop-threshold"
        flow.state.proposal_data = {"price": price, "impressions": impressions}
        flow.state.recommendation = recommendation
        return flow

    def test_high_value_deal_forces_approval_with_toggle_off(self):
        """Deal value >= threshold must be gated even with the global toggle
        OFF — it cannot auto-finalize."""
        # 50.0 CPM x 2,000,000 impressions / 1000 = 100,000 gross value.
        flow = self._flow(price=50.0, impressions=2_000_000)
        settings = SimpleNamespace(
            approval_gate_enabled=False,
            approval_required_flows="",
            approval_required_above_value=50_000.0,
        )
        _run_execute_decision(flow, settings)

        assert flow.state.status == ExecutionStatus.PENDING_APPROVAL
        # Must NOT have finalized (not in accepted list).
        assert flow.state.proposal_id not in flow.state.accepted_proposals

    def test_below_threshold_deal_auto_finalizes_with_toggle_off(self):
        """Below-threshold deal with the toggle off keeps auto-finalize
        (backward compatible)."""
        # 10.0 CPM x 100,000 / 1000 = 1,000 gross value, well below threshold.
        flow = self._flow(price=10.0, impressions=100_000)
        settings = SimpleNamespace(
            approval_gate_enabled=False,
            approval_required_flows="",
            approval_required_above_value=50_000.0,
        )
        _run_execute_decision(flow, settings)

        assert flow.state.status == ExecutionStatus.ACCEPTED
        assert flow.state.proposal_id in flow.state.accepted_proposals

    def test_threshold_disabled_when_zero(self):
        """approval_required_above_value=0 disables the threshold gate; a
        large deal auto-finalizes when the toggle is also off."""
        flow = self._flow(price=100.0, impressions=10_000_000)  # 1,000,000 value
        settings = SimpleNamespace(
            approval_gate_enabled=False,
            approval_required_flows="",
            approval_required_above_value=0.0,
        )
        _run_execute_decision(flow, settings)
        assert flow.state.status == ExecutionStatus.ACCEPTED
