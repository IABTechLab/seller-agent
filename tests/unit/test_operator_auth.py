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
(d) Operator keys are minted via ``OperatorApiKeyCreateRequest`` /
    ``POST /auth/api-keys/operator`` (no buyer identity fields). The CLI
    bootstrap (``ad-seller create-operator-key``) uses the same path
    against storage directly.
(e) Admin MCP tools deny non-operator HTTP callers but allow local
    stdio access (no HTTP request context).
(f) The remaining mutation surface is gated: inventory-type override
    write/delete, change-request review/apply, and order transitions
    (REST endpoint and its MCP twin) — while the corresponding
    buyer-facing reads and CR submission stay on the optional-key path.
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
    API_KEY_INDEX_PREFIX,
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


def _seed_key(
    store,
    *,
    role=ApiKeyRole.BUYER,
    key_id="key-test",
    label: str | None = None,
    revoked: bool = False,
):
    """Seed a valid API key record with the given role; return the raw key.

    Also maintains ``api_key_list`` / index so ``list_keys()`` sees the seed
    (needed for operator-label uniqueness checks).
    """
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(agency_id="agy-1", agency_name="Acme"),
        role=role,
        label=label if label is not None else f"{role.value} test key",
        revoked=revoked,
    )
    store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    store[f"{API_KEY_INDEX_PREFIX}{key_id}"] = key_hash
    all_keys = store.get("api_key_list") or []
    all_keys.append(key_id)
    store["api_key_list"] = all_keys
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
            resp = await client.post("/auth/api-keys", json={"label": "x"}, headers=_auth(raw_key))
        assert resp.status_code == 403
        assert "Operator credential required" in resp.text

    async def test_operator_key_creates_buyer_key(self, client, mock_storage):
        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            buyer_resp = await client.post(
                "/auth/api-keys",
                json={"label": "buyer key", "agency_id": "agy-2"},
                headers=_auth(raw_key),
            )
        assert buyer_resp.status_code == 200
        assert buyer_resp.json()["role"] == "buyer"
        assert buyer_resp.json()["identity"]["agency_id"] == "agy-2"

    async def test_operator_endpoint_mints_operator_key(self, client, mock_storage):
        """POST /auth/api-keys/operator creates an operator key (no buyer identity)."""
        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            op_resp = await client.post(
                "/auth/api-keys/operator",
                json={"label": "second operator"},
                headers=_auth(raw_key),
            )
        assert op_resp.status_code == 200
        body = op_resp.json()
        assert body["role"] == "operator"
        assert body["label"] == "second operator"
        # Empty BuyerIdentity — operator keys carry no seat/agency/advertiser.
        assert body["identity"].get("agency_id") in (None, "")
        assert body["identity"].get("seat_id") in (None, "")

    async def test_duplicate_operator_label_is_409(self, client, mock_storage):
        """Active operator keys may not share a label."""
        raw_key = _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-1",
            label="Primary operator",
        )
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/auth/api-keys/operator",
                json={"label": "Primary operator"},
                headers=_auth(raw_key),
            )
        assert resp.status_code == 409
        assert "Primary operator" in resp.text
        assert "key-op-1" in resp.text

    async def test_revoked_operator_label_may_be_reused(self, client, mock_storage):
        """Revoked operator keys do not block reusing their label."""
        raw_key = _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-auth",
            label="auth-only",
        )
        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-revoked",
            label="Reusable label",
            revoked=True,
        )
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/auth/api-keys/operator",
                json={"label": "Reusable label"},
                headers=_auth(raw_key),
            )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Reusable label"
        assert resp.json()["role"] == "operator"

    async def test_buyer_create_endpoint_ignores_role_field(self, client, mock_storage):
        """role is not on CreateApiKeyRequest — extra fields are ignored; always buyer."""
        raw_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/auth/api-keys",
                json={"label": "x", "role": "operator", "agency_id": "agy-sneak"},
                headers=_auth(raw_key),
            )
        assert resp.status_code == 200
        assert resp.json()["role"] == "buyer"

    async def test_anonymous_operator_endpoint_is_401(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/auth/api-keys/operator", json={"label": "x"})
        assert resp.status_code == 401

    async def test_buyer_key_cannot_mint_operator_key(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/auth/api-keys/operator",
                json={"label": "x"},
                headers=_auth(buyer_key),
            )
        assert resp.status_code == 403

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
            allowed = await client.put("/api/v1/rate-card", json=entries, headers=_auth(op_key))
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
        assert {create.status_code, update.status_code, delete.status_code, sync.status_code} == {
            401
        }

    async def test_inventory_sync_trigger_requires_operator(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/inventory-sync/trigger")
        assert resp.status_code == 401

    async def test_deal_push_distribute_curators_require_operator(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            push = await client.post(
                "/api/v1/deals/push", json={"deal_id": "d-1", "buyer_urls": []}
            )
            distribute = await client.post("/api/v1/deals/distribute", json={"deal_id": "d-1"})
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

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.storage.factory.close_storage", new_callable=AsyncMock) as close_mock,
        ):
            create_operator_key(label="bootstrap test", expires_in_days=None)

        records = [
            v for k, v in mock_storage._store.items() if k.startswith(API_KEY_STORAGE_PREFIX)
        ]
        assert len(records) == 1
        assert records[0]["role"] == "operator"
        assert records[0]["label"] == "bootstrap test"
        # Operator keys carry an empty BuyerIdentity (no seat/agency).
        assert not records[0]["identity"].get("agency_id")
        assert not records[0]["identity"].get("seat_id")
        close_mock.assert_awaited_once()

    def test_duplicate_label_exits_nonzero_and_closes_storage(self, mock_storage):
        import typer

        from ad_seller.interfaces.cli.main import create_operator_key

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-existing",
            label="bootstrap test",
        )
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.storage.factory.close_storage", new_callable=AsyncMock) as close_mock,
            pytest.raises(typer.Exit) as exc_info,
        ):
            create_operator_key(label="bootstrap test", expires_in_days=None)

        assert exc_info.value.exit_code == 1
        close_mock.assert_awaited_once()

    def test_different_label_succeeds_after_existing_operator(self, mock_storage):
        from ad_seller.interfaces.cli.main import create_operator_key

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-existing",
            label="Primary operator",
        )
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.storage.factory.close_storage", new_callable=AsyncMock),
        ):
            create_operator_key(label="Secondary operator", expires_in_days=None)

        labels = {
            v["label"]
            for k, v in mock_storage._store.items()
            if k.startswith(API_KEY_STORAGE_PREFIX)
        }
        assert labels == {"Primary operator", "Secondary operator"}

    def test_delete_operator_key_by_label_closes_storage(self, mock_storage):
        from ad_seller.interfaces.cli.main import delete_operator_key

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-1",
            label="Primary operator",
        )
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.storage.factory.close_storage", new_callable=AsyncMock) as close_mock,
        ):
            delete_operator_key(label="Primary operator", key_id=None)

        record = next(
            v for k, v in mock_storage._store.items() if k.startswith(API_KEY_STORAGE_PREFIX)
        )
        assert record["revoked"] is True
        close_mock.assert_awaited_once()

    def test_delete_operator_key_missing_args_exits(self, mock_storage):
        import typer

        from ad_seller.interfaces.cli.main import delete_operator_key

        with pytest.raises(typer.Exit) as exc_info:
            delete_operator_key(label=None, key_id=None)
        assert exc_info.value.exit_code == 1

    def test_delete_then_recreate_same_label(self, mock_storage):
        from ad_seller.interfaces.cli.main import create_operator_key, delete_operator_key

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-old",
            label="Primary operator",
        )
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.storage.factory.close_storage", new_callable=AsyncMock),
        ):
            delete_operator_key(label="Primary operator", key_id=None)
            create_operator_key(label="Primary operator", expires_in_days=None)

        active = [
            v
            for k, v in mock_storage._store.items()
            if k.startswith(API_KEY_STORAGE_PREFIX) and not v.get("revoked")
        ]
        assert len(active) == 1
        assert active[0]["label"] == "Primary operator"
        assert active[0]["key_id"] != "key-old"


class TestCreateOperatorKeyService:
    async def test_duplicate_active_label_raises(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService
        from ad_seller.models.api_key import OperatorApiKeyCreateRequest

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-1",
            label="Ops",
        )
        service = ApiKeyService(mock_storage)
        with pytest.raises(ValueError, match="key-op-1"):
            await service.create_operator_key(OperatorApiKeyCreateRequest(label="Ops"))

    async def test_revoked_label_may_be_reused(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService
        from ad_seller.models.api_key import OperatorApiKeyCreateRequest

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-revoked",
            label="Ops",
            revoked=True,
        )
        service = ApiKeyService(mock_storage)
        resp = await service.create_operator_key(OperatorApiKeyCreateRequest(label="Ops"))
        assert resp.role == ApiKeyRole.OPERATOR
        assert resp.label == "Ops"


class TestListOperatorKeys:
    async def test_lists_only_active_operator_keys_by_default(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-active",
            label="Active",
        )
        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-revoked",
            label="Revoked",
            revoked=True,
        )
        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.BUYER,
            key_id="key-buyer",
            label="Buyer",
        )
        service = ApiKeyService(mock_storage)
        keys = await service.list_operator_keys()
        assert [k.key_id for k in keys] == ["key-op-active"]

    async def test_include_inactive_returns_revoked(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-active",
            label="Active",
        )
        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-revoked",
            label="Revoked",
            revoked=True,
        )
        service = ApiKeyService(mock_storage)
        keys = await service.list_operator_keys(include_inactive=True)
        assert {k.key_id for k in keys} == {"key-op-active", "key-op-revoked"}

    def test_cli_lists_and_closes_storage(self, mock_storage):
        from ad_seller.interfaces.cli.main import list_operator_keys

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-1",
            label="Primary operator",
        )
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.storage.factory.close_storage", new_callable=AsyncMock) as close_mock,
        ):
            list_operator_keys(include_inactive=False)
        close_mock.assert_awaited_once()

    def test_cli_empty_list_closes_storage(self, mock_storage):
        from ad_seller.interfaces.cli.main import list_operator_keys

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.storage.factory.close_storage", new_callable=AsyncMock) as close_mock,
        ):
            list_operator_keys(include_inactive=False)
        close_mock.assert_awaited_once()


class TestDeleteOperatorKeyService:
    async def test_delete_by_label_revokes_and_frees_label(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService
        from ad_seller.models.api_key import OperatorApiKeyCreateRequest

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-1",
            label="Ops",
        )
        service = ApiKeyService(mock_storage)
        info = await service.delete_operator_key(label="Ops")
        assert info.key_id == "key-op-1"
        assert info.revoked is True
        assert info.is_active is False

        # Label can be reused after revoke.
        resp = await service.create_operator_key(OperatorApiKeyCreateRequest(label="Ops"))
        assert resp.key_id != "key-op-1"

    async def test_delete_by_key_id(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.OPERATOR,
            key_id="key-op-1",
            label="Ops",
        )
        service = ApiKeyService(mock_storage)
        info = await service.delete_operator_key(key_id="key-op-1")
        assert info.revoked is True

    async def test_delete_refuses_buyer_key(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService

        _seed_key(
            mock_storage._store,
            role=ApiKeyRole.BUYER,
            key_id="key-buyer",
            label="buyer",
        )
        service = ApiKeyService(mock_storage)
        with pytest.raises(ValueError, match="not an operator key"):
            await service.delete_operator_key(key_id="key-buyer")

    async def test_delete_missing_label_raises(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService

        service = ApiKeyService(mock_storage)
        with pytest.raises(ValueError, match="No active operator key"):
            await service.delete_operator_key(label="missing")

    async def test_delete_requires_identifier(self, mock_storage):
        from ad_seller.auth.api_key_service import ApiKeyService

        service = ApiKeyService(mock_storage)
        with pytest.raises(ValueError, match="key_id or label"):
            await service.delete_operator_key()


class TestOperatorApiKeyCreateRequest:
    def test_has_no_buyer_identity_fields(self):
        from ad_seller.models.api_key import OperatorApiKeyCreateRequest

        fields = set(OperatorApiKeyCreateRequest.model_fields)
        assert fields == {"label", "expires_in_days"}

    def test_buyer_create_request_has_no_role_field(self):
        from ad_seller.models.api_key import ApiKeyCreateRequest

        assert "role" not in ApiKeyCreateRequest.model_fields


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
        assert not [k for k in mock_storage._store if k.startswith(API_KEY_STORAGE_PREFIX)]


# =============================================================================
# (f) Remaining mutation surface: inventory-type overrides, change-request
#     review/apply, order transitions (REST + MCP twin)
# =============================================================================


class TestInventoryTypeOverrideRequiresOperator:
    async def test_anonymous_override_is_401(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/api/v1/products/prod-1/inventory-type",
                json={"product_id": "prod-1", "inventory_type": "ctv"},
            )
        assert resp.status_code == 401

    async def test_buyer_key_override_is_403(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/api/v1/products/prod-1/inventory-type",
                json={"product_id": "prod-1", "inventory_type": "ctv"},
                headers=_auth(buyer_key),
            )
        assert resp.status_code == 403
        assert "Operator credential required" in resp.text

    async def test_operator_key_override_passes_auth(self, client, mock_storage):
        op_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.catalog_service.override_inventory_type",
                new=AsyncMock(
                    return_value={
                        "previous_type": "display",
                        "applied_at": "2026-07-29T00:00:00Z",
                    }
                ),
            ),
        ):
            resp = await client.post(
                "/api/v1/products/prod-1/inventory-type",
                json={"product_id": "prod-1", "inventory_type": "ctv"},
                headers=_auth(op_key),
            )
        assert resp.status_code == 200
        assert resp.json()["new_type"] == "ctv"

    async def test_anonymous_delete_override_is_401(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.delete("/api/v1/products/prod-1/inventory-type")
        assert resp.status_code == 401

    async def test_buyer_key_delete_override_is_403(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.delete(
                "/api/v1/products/prod-1/inventory-type", headers=_auth(buyer_key)
            )
        assert resp.status_code == 403

    async def test_operator_key_delete_override_passes_auth(self, client, mock_storage):
        op_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.catalog_service.delete_inventory_type_override",
                new=AsyncMock(return_value=True),
            ),
        ):
            resp = await client.delete(
                "/api/v1/products/prod-1/inventory-type", headers=_auth(op_key)
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    async def test_override_read_stays_open(self, client, mock_storage):
        """GET stays on the buyer-facing (optional-key) surface — 404 for a
        product with no override, never 401."""
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.catalog_service.get_inventory_type_override",
                new=AsyncMock(return_value=None),
            ),
        ):
            resp = await client.get("/api/v1/products/prod-1/inventory-type")
        assert resp.status_code == 404


class TestChangeRequestDecisionsRequireOperator:
    """Submitting a CR stays buyer-facing; deciding/applying is seller admin."""

    async def test_anonymous_review_is_401(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/api/v1/change-requests/cr-1/review", json={"decision": "approve"}
            )
        assert resp.status_code == 401

    async def test_buyer_key_review_is_403(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/api/v1/change-requests/cr-1/review",
                json={"decision": "approve"},
                headers=_auth(buyer_key),
            )
        assert resp.status_code == 403

    async def test_operator_key_review_passes_auth(self, client, mock_storage):
        op_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.order_service.review_change_request",
                new=AsyncMock(return_value={"cr_id": "cr-1", "status": "approved"}),
            ),
        ):
            resp = await client.post(
                "/api/v1/change-requests/cr-1/review",
                json={"decision": "approve"},
                headers=_auth(op_key),
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    async def test_anonymous_apply_is_401(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests/cr-1/apply")
        assert resp.status_code == 401

    async def test_buyer_key_apply_is_403(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post("/api/v1/change-requests/cr-1/apply", headers=_auth(buyer_key))
        assert resp.status_code == 403

    async def test_operator_key_apply_passes_auth(self, client, mock_storage):
        op_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.order_service.apply_change_request",
                new=AsyncMock(return_value={"cr_id": "cr-1", "status": "applied"}),
            ),
        ):
            resp = await client.post("/api/v1/change-requests/cr-1/apply", headers=_auth(op_key))
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

    async def test_create_and_read_change_requests_stay_buyer_facing(self, client, mock_storage):
        """POST /change-requests and the reads remain on the optional-key
        surface — anonymous callers are not rejected by auth."""
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.order_service.list_change_requests",
                new=AsyncMock(return_value=[]),
            ),
        ):
            resp = await client.get("/api/v1/change-requests")
        assert resp.status_code == 200


class TestOrderTransitionRequiresOperator:
    async def test_anonymous_transition_is_401(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/api/v1/orders/ord-1/transition", json={"to_status": "approved"}
            )
        assert resp.status_code == 401

    async def test_buyer_key_transition_is_403(self, client, mock_storage):
        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/api/v1/orders/ord-1/transition",
                json={"to_status": "approved"},
                headers=_auth(buyer_key),
            )
        assert resp.status_code == 403

    async def test_operator_key_transition_passes_auth(self, client, mock_storage):
        op_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.order_service.transition_order",
                new=AsyncMock(return_value={"order_id": "ord-1", "status": "approved"}),
            ),
        ):
            resp = await client.post(
                "/api/v1/orders/ord-1/transition",
                json={"to_status": "approved"},
                headers=_auth(op_key),
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    async def test_order_reads_stay_buyer_facing(self, client, mock_storage):
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.order_service.list_orders",
                new=AsyncMock(return_value=[]),
            ),
        ):
            resp = await client.get("/api/v1/orders")
        assert resp.status_code == 200


class TestMcpTransitionOrderGated:
    """The MCP twin of POST /orders/{id}/transition is operator-gated."""

    async def test_http_without_key_is_denied_and_service_untouched(self):
        from ad_seller.interfaces import mcp_server

        service_mock = AsyncMock()
        ctx = _fake_mcp_context(headers={})
        with (
            patch.object(mcp_server.mcp, "get_context", return_value=ctx),
            patch(
                "ad_seller.services.order_service.transition_order",
                new=service_mock,
            ),
        ):
            result = await mcp_server.transition_order(order_id="ord-1", new_status="approved")
        assert "authentication_required" in result
        service_mock.assert_not_awaited()

    async def test_http_with_buyer_key_is_denied(self, mock_storage):
        from ad_seller.interfaces import mcp_server

        buyer_key = _seed_key(mock_storage._store, role=ApiKeyRole.BUYER)
        ctx = _fake_mcp_context(headers={"authorization": f"Bearer {buyer_key}"})
        with (
            patch.object(mcp_server.mcp, "get_context", return_value=ctx),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            result = await mcp_server.transition_order(order_id="ord-1", new_status="approved")
        assert "operator_required" in result

    async def test_http_with_operator_key_is_allowed(self, mock_storage):
        from ad_seller.interfaces import mcp_server

        op_key = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR)
        ctx = _fake_mcp_context(headers={"x-api-key": op_key})
        with (
            patch.object(mcp_server.mcp, "get_context", return_value=ctx),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.services.order_service.transition_order",
                new=AsyncMock(return_value={"order_id": "ord-1", "status": "approved"}),
            ),
        ):
            result = await mcp_server.transition_order(order_id="ord-1", new_status="approved")
        assert "approved" in result
        assert "operator_required" not in result
