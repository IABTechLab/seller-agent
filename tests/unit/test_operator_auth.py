# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Operator-key authentication for the admin surface.

Covers:

(a) API key records carry a role; pre-existing records (no role field)
    deserialize as BUYER — old keys are never silently promoted.
(b) ``require_operator_key``: anonymous → 401, buyer key → 403,
    operator key → allowed.
(c) The admin REST surface is gated: /auth/api-keys, /events, rate-card
    writes, registry mutations, package mutations, inventory-sync
    trigger, deal push/distribute, curator registration.
(d) The CLI bootstrap (``ad-seller create-operator-key``) mints an
    OPERATOR-role key directly in storage.
(e) Admin MCP tools deny non-operator HTTP callers but allow local
    stdio access (no HTTP request context).
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

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

from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.models.api_key import (  # noqa: E402
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    ApiKeyRole,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402

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


def _seed_key(store, *, role=ApiKeyRole.BUYER, key_id="key-test"):
    """Seed a valid API key record with the given role; return the raw key."""
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(agency_id="agy-1", agency_name="Acme"),
        role=role,
        label=f"{role.value} test key",
    )
    store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    return raw_key


def _auth(raw_key: str) -> dict:
    return {"Authorization": f"Bearer {raw_key}"}


# =============================================================================
# (a) Role model
# =============================================================================


class TestApiKeyRole:
    def test_legacy_record_without_role_deserializes_as_buyer(self):
        """Stored records from before the role field must load as BUYER."""
        raw_key = generate_api_key()
        legacy = ApiKeyRecord(
            key_id="key-legacy",
            key_hash=hash_api_key(raw_key),
            key_prefix_hint=raw_key[:12] + "...",
            identity=BuyerIdentity(seat_id="seat-1"),
        ).model_dump(mode="json")
        legacy.pop("role", None)  # simulate pre-role stored JSON

        record = ApiKeyRecord(**legacy)
        assert record.role == ApiKeyRole.BUYER

    def test_operator_role_round_trips_through_storage_shape(self):
        raw_key = generate_api_key()
        record = ApiKeyRecord(
            key_id="key-op",
            key_hash=hash_api_key(raw_key),
            key_prefix_hint=raw_key[:12] + "...",
            identity=BuyerIdentity(),
            role=ApiKeyRole.OPERATOR,
        )
        reloaded = ApiKeyRecord(**record.model_dump(mode="json"))
        assert reloaded.role == ApiKeyRole.OPERATOR


# =============================================================================
# (b) + (c) REST admin surface enforcement
# =============================================================================


class TestApiKeyEndpointsRequireOperator:
    async def test_anonymous_create_key_is_401(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/auth/api-keys", json={"label": "x"})
        assert resp.status_code == 401

    async def test_buyer_key_create_key_is_403(self, client, mock_storage):
        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/auth/api-keys", json={"label": "x"}, headers=_auth(raw_key)
            )
        assert resp.status_code == 403
        assert "Operator credential required" in resp.text

    async def test_operator_key_creates_buyer_and_operator_keys(self, client, mock_storage):
        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            buyer_resp = await client.post(
                "/auth/api-keys",
                json={"label": "buyer key", "agency_id": "agy-2"},
                headers=_auth(raw_key),
            )
            op_resp = await client.post(
                "/auth/api-keys",
                json={"label": "second operator", "role": "operator"},
                headers=_auth(raw_key),
            )
        assert buyer_resp.status_code == 200
        assert buyer_resp.json()["role"] == "buyer"
        assert op_resp.status_code == 200
        assert op_resp.json()["role"] == "operator"

    async def test_invalid_role_is_400(self, client, mock_storage):
        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/auth/api-keys",
                json={"label": "x", "role": "superadmin"},
                headers=_auth(raw_key),
            )
        assert resp.status_code == 400

    async def test_list_and_revoke_require_operator(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER, key_id="key-b")
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            anon_list = await client.get("/auth/api-keys")
            buyer_list = await client.get("/auth/api-keys", headers=_auth(buyer_key))
            anon_revoke = await client.delete("/auth/api-keys/key-b")
        assert anon_list.status_code == 401
        assert buyer_list.status_code == 403
        assert anon_revoke.status_code == 401


class TestAdminSurfaceGated:
    """Spot-checks across the rest of the operator surface."""

    async def test_events_require_operator(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/events")
        assert resp.status_code == 401

    async def test_rate_card_write_requires_operator(self, client, mock_storage):
        entries = [{"inventory_type": "ctv", "base_cpm": 40.0}]
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            anon = await client.put("/api/v1/rate-card", json=entries)
            op_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
            allowed = await client.put(
                "/api/v1/rate-card", json=entries, headers=_auth(op_key)
            )
        assert anon.status_code == 401
        assert allowed.status_code == 200
        assert allowed.json()["entries"][0]["base_cpm"] == 40.0

    async def test_rate_card_read_stays_public(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/rate-card")
        assert resp.status_code == 200

    async def test_registry_trust_mutation_requires_operator(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            anon = await client.put(
                "/registry/agents/agent-1/trust", json={"trust_status": "preferred"}
            )
            buyer = await client.put(
                "/registry/agents/agent-1/trust",
                json={"trust_status": "preferred"},
                headers=_auth(buyer_key),
            )
        assert anon.status_code == 401
        assert buyer.status_code == 403

    async def test_registry_reads_stay_public(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/registry/agents")
        assert resp.status_code == 200

    async def test_package_mutations_require_operator(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create = await client.post("/packages", json={"name": "P", "product_ids": []})
            update = await client.put("/packages/pkg-x", json={"name": "Q"})
            delete = await client.delete("/packages/pkg-x")
            sync = await client.post("/packages/sync")
        assert {create.status_code, update.status_code, delete.status_code,
                sync.status_code} == {401}

    async def test_inventory_sync_trigger_requires_operator(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/inventory-sync/trigger")
        assert resp.status_code == 401

    async def test_deal_push_distribute_curators_require_operator(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            push = await client.post(
                "/api/v1/deals/push", json={"deal_id": "d-1", "buyer_urls": []}
            )
            distribute = await client.post(
                "/api/v1/deals/distribute", json={"deal_id": "d-1"}
            )
            curator = await client.post(
                "/api/v1/curators", json={"name": "C", "curator_id": "c-1", "fee_cpm": 1.0}
            )
        assert push.status_code == 401
        assert distribute.status_code == 401
        assert curator.status_code == 401


# =============================================================================
# (d) CLI bootstrap
# =============================================================================


class TestCliBootstrap:
    def test_create_operator_key_mints_operator_role(self, mock_storage):
        from ad_seller.interfaces.cli.main import create_operator_key

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            create_operator_key(label="bootstrap test", expires_in_days=None)

        records = [
            v
            for k, v in mock_storage._store.items()
            if k.startswith(API_KEY_STORAGE_PREFIX)
        ]
        assert len(records) == 1
        assert records[0]["role"] == "operator"
        assert records[0]["label"] == "bootstrap test"


# =============================================================================
# (e) MCP tool gating
# =============================================================================


def _fake_mcp_context(headers: dict | None):
    """Build a fake FastMCP context carrying an HTTP request (or none)."""
    ctx = MagicMock()
    if headers is None:
        ctx.request_context.request = None
    else:
        request = MagicMock()
        request.headers = headers
        ctx.request_context.request = request
    return ctx


class TestMcpOperatorGate:
    async def test_stdio_no_request_is_allowed(self):
        from ad_seller.interfaces import mcp_server

        with patch.object(mcp_server.mcp, "get_context", side_effect=LookupError):
            assert await mcp_server._deny_unless_operator() is None

    async def test_http_without_key_is_denied(self):
        from ad_seller.interfaces import mcp_server

        ctx = _fake_mcp_context(headers={})
        with patch.object(mcp_server.mcp, "get_context", return_value=ctx):
            denied = await mcp_server._deny_unless_operator()
        assert denied is not None
        assert "authentication_required" in denied

    async def test_http_with_buyer_key_is_denied(self, mock_storage):
        from ad_seller.interfaces import mcp_server

        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        ctx = _fake_mcp_context(headers={"authorization": f"Bearer {raw_key}"})
        with (
            patch.object(mcp_server.mcp, "get_context", return_value=ctx),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            denied = await mcp_server._deny_unless_operator()
        assert denied is not None
        assert "operator_required" in denied

    async def test_http_with_operator_key_is_allowed(self, mock_storage):
        from ad_seller.interfaces import mcp_server

        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        ctx = _fake_mcp_context(headers={"x-api-key": raw_key})
        with (
            patch.object(mcp_server.mcp, "get_context", return_value=ctx),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            assert await mcp_server._deny_unless_operator() is None

    async def test_gated_tool_returns_denial_over_http(self, mock_storage):
        """An admin tool short-circuits with the denial payload."""
        from ad_seller.interfaces import mcp_server

        ctx = _fake_mcp_context(headers={})
        with (
            patch.object(mcp_server.mcp, "get_context", return_value=ctx),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            result = await mcp_server.create_api_key(seat_id="seat-x")
        assert "authentication_required" in result
        # Nothing was minted.
        assert not [
            k for k in mock_storage._store if k.startswith(API_KEY_STORAGE_PREFIX)
        ]
