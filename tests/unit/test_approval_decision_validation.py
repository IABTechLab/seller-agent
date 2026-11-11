"""Issue #120: approval decisions are validated, and MCP decisions record who made them.

REST and MCP run the real app, a real SQLite backend and real API keys
seeded in storage.
"""

import json
import sys
from datetime import datetime, timedelta
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

from ad_seller.events.approval import ApprovalGate, normalize_decision  # noqa: E402
from ad_seller.events.bus import InMemoryEventBus  # noqa: E402
from ad_seller.events.models import EventType  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.models.api_key import (  # noqa: E402
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    ApiKeyRole,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402
from ad_seller.storage.sqlite_backend import SQLiteBackend  # noqa: E402
from tests.unit.test_operator_auth import _fake_mcp_context  # noqa: E402

_OPERATOR_PRINCIPAL = "apikey:key-ops:seller-ops"


class TestNormalizeDecision:
    @pytest.mark.parametrize(
        "raw, canonical",
        [
            ("approve", "approve"),
            ("reject", "reject"),
            ("counter", "counter"),
            ("Approve", "approve"),
            ("REJECT", "reject"),
            ("  counter\n", "counter"),
        ],
    )
    def test_accepted_spellings(self, raw, canonical):
        assert normalize_decision(raw) == canonical

    @pytest.mark.parametrize(
        "raw",
        ["aprove", "approved", "yes", "", "   ", "approve reject", "accept", None, 1, ["approve"]],
    )
    def test_anything_else_is_rejected(self, raw):
        with pytest.raises(ValueError, match="expected one of: approve, reject, counter"):
            normalize_decision(raw)


@pytest.fixture
async def storage(tmp_path):
    backend = SQLiteBackend(f"sqlite:///{tmp_path}/approvals.db")
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


async def _seed_key(storage, key_id, role):
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(agency_id="seller-ops" if role == ApiKeyRole.OPERATOR else "ag-1"),
        role=role,
    )
    await storage.set(f"{API_KEY_STORAGE_PREFIX}{key_hash}", record.model_dump(mode="json"))
    return raw_key


@pytest.fixture
async def keys(storage):
    return {
        "operator": await _seed_key(storage, "key-ops", ApiKeyRole.OPERATOR),
        "buyer": await _seed_key(storage, "key-buyer", ApiKeyRole.BUYER),
    }


async def _pending(storage, **overrides):
    fields = dict(
        flow_id="flow-1",
        flow_type="proposal_handling",
        gate_name="proposal_decision",
        context={},
        flow_state_snapshot={},
        proposal_id="prop-1",
    )
    fields.update(overrides)
    return (await ApprovalGate(storage).request_approval(**fields)).approval_id


async def _status(storage, approval_id):
    request = await ApprovalGate(storage).get_request(approval_id)
    return request.status.value


@pytest.fixture
async def client(storage, event_bus, keys):
    with (
        patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
        patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Api-Key": keys["operator"]},
        ) as c:
            yield c


async def _decide(client, approval_id, **body):
    return await client.post(f"/approvals/{approval_id}/decide", json=body)


async def _decision_events(event_bus):
    granted = await event_bus.list_events(event_type=EventType.APPROVAL_GRANTED.value)
    denied = await event_bus.list_events(event_type=EventType.APPROVAL_DENIED.value)
    return granted + denied


class TestGate:
    @pytest.mark.parametrize("decision", ["aprove", "", "yes"])
    async def test_invalid_decision_leaves_the_approval_pending(self, storage, event_bus, decision):
        approval_id = await _pending(storage)
        gate = ApprovalGate(storage)

        with patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)):
            with pytest.raises(ValueError, match="Invalid decision"):
                await gate.submit_decision(approval_id=approval_id, decision=decision)

        assert await _status(storage, approval_id) == "pending"
        assert await gate.get_response(approval_id) is None
        assert await _decision_events(event_bus) == []

    async def test_invalid_decision_is_checked_before_expiry_is_recorded(self, storage):
        approval_id = await _pending(storage)
        raw = await storage.get(f"approval:{approval_id}")
        raw = json.loads(raw) if isinstance(raw, str) else raw
        raw["expires_at"] = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        await storage.set(f"approval:{approval_id}", raw)

        with pytest.raises(ValueError, match="Invalid decision"):
            await ApprovalGate(storage).submit_decision(approval_id=approval_id, decision="nope")

        assert await _status(storage, approval_id) == "pending"


class TestRestDecide:
    @pytest.mark.parametrize("decision", ["aprove", "approved", "yes", "", "accept"])
    async def test_invalid_decision_is_400_and_can_be_corrected(
        self, client, storage, event_bus, decision
    ):
        approval_id = await _pending(storage)

        resp = await _decide(client, approval_id, decision=decision)

        assert resp.status_code == 400, resp.text
        assert "expected one of: approve, reject, counter" in resp.json()["detail"]
        assert await _status(storage, approval_id) == "pending"
        assert await _decision_events(event_bus) == []

        fixed = await _decide(client, approval_id, decision="approve")
        assert fixed.status_code == 200, fixed.text
        assert await _status(storage, approval_id) == "approved"

    @pytest.mark.parametrize(
        "sent, stored, status",
        [
            ("approve", "approve", "approved"),
            ("Approve", "approve", "approved"),
            (" REJECT ", "reject", "rejected"),
            ("counter", "counter", "rejected"),
        ],
    )
    async def test_valid_decisions_are_stored_canonical(
        self, client, storage, sent, stored, status
    ):
        approval_id = await _pending(storage)

        resp = await _decide(client, approval_id, decision=sent)

        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"] == stored
        assert resp.json()["decided_by_principal"] == _OPERATOR_PRINCIPAL
        assert await _status(storage, approval_id) == status

    @pytest.mark.parametrize("decision", [None, 5, ["approve"]])
    async def test_non_string_decision_is_422(self, client, storage, decision):
        approval_id = await _pending(storage)

        resp = await _decide(client, approval_id, decision=decision)

        assert resp.status_code == 422, resp.text
        assert await _status(storage, approval_id) == "pending"

    async def test_capitalised_approve_still_runs_the_dsp_resolution_check(self, client, storage):
        approval_id = await _pending(
            storage,
            flow_id="ssp-distribute-deal-1",
            flow_type="ssp_distribution",
            gate_name="dsp_resolution",
            context={"candidates": [{"dsp_id": 52}, {"dsp_id": 85}]},
            flow_state_snapshot={
                "ssp_name": "index_exchange",
                "create_request": {"deal_type": "pmp", "external_deal_id": "deal-1"},
            },
            proposal_id="",
            deal_id="deal-1",
        )

        resp = await _decide(client, approval_id, decision="APPROVE")

        assert resp.status_code == 400, resp.text
        assert "selected_dsp_id" in json.dumps(resp.json())
        assert await _status(storage, approval_id) == "pending"


class TestMcpApproveOrReject:
    async def _call(self, storage, event_bus, headers, approval_id, decision="approve"):
        from ad_seller.interfaces import mcp_server

        get_context = (
            {"side_effect": LookupError}
            if headers is None
            else {"return_value": _fake_mcp_context(headers)}
        )
        with (
            patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
            patch.object(mcp_server.mcp, "get_context", **get_context),
        ):
            return json.loads(await mcp_server.approve_or_reject(approval_id, decision))

    async def test_http_operator_is_recorded_as_the_principal(self, storage, event_bus, keys):
        approval_id = await _pending(storage)

        result = await self._call(
            storage, event_bus, {"authorization": f"Bearer {keys['operator']}"}, approval_id
        )

        assert result["decided_by_principal"] == _OPERATOR_PRINCIPAL
        stored = await ApprovalGate(storage).get_response(approval_id)
        assert stored.decided_by_principal == _OPERATOR_PRINCIPAL
        [event] = await _decision_events(event_bus)
        assert _OPERATOR_PRINCIPAL in json.dumps(event.payload)

    async def test_stdio_is_recorded_as_local_access(self, storage, event_bus):
        approval_id = await _pending(storage)

        result = await self._call(storage, event_bus, None, approval_id)

        assert result["decided_by_principal"] == "local:mcp-stdio"

    @pytest.mark.parametrize(
        "headers_for, error",
        [("buyer", "operator_required"), ("none", "authentication_required")],
    )
    async def test_non_operators_are_denied_and_nothing_is_decided(
        self, storage, event_bus, keys, headers_for, error
    ):
        approval_id = await _pending(storage)
        headers = {"x-api-key": keys["buyer"]} if headers_for == "buyer" else {}

        result = await self._call(storage, event_bus, headers, approval_id)

        assert result["error"] == error
        assert await _status(storage, approval_id) == "pending"

    async def test_invalid_decision_returns_the_error_and_stays_pending(
        self, storage, event_bus, keys
    ):
        approval_id = await _pending(storage)

        result = await self._call(
            storage, event_bus, {"x-api-key": keys["operator"]}, approval_id, decision="aprove"
        )

        assert "expected one of: approve, reject, counter" in result["detail"]
        assert await _status(storage, approval_id) == "pending"


class TestOtherMcpToolsStillGated:
    @pytest.mark.parametrize(
        "headers_for, expected",
        [("operator", None), ("buyer", "operator_required"), ("none", "authentication_required")],
    )
    async def test_deny_unless_operator_is_unchanged(self, storage, keys, headers_for, expected):
        from ad_seller.interfaces import mcp_server

        headers = {"x-api-key": keys[headers_for]} if headers_for in ("operator", "buyer") else {}
        with (
            patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
            patch.object(mcp_server.mcp, "get_context", return_value=_fake_mcp_context(headers)),
        ):
            denied = await mcp_server._deny_unless_operator()

        if expected is None:
            assert denied is None
        else:
            assert json.loads(denied)["error"] == expected
