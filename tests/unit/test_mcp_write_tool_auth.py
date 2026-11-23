"""MCP twins of the credential-gated REST write routes (upstream issue #86).

Over HTTP (``/mcp``), the deal-admin tools need an operator key, the same
as their REST routes, and the buyer write tools need a valid API key
unless ``ALLOW_ANONYMOUS_BUYER_WRITES`` is set. stdio has no HTTP request
and stays trusted.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_seller.config import get_settings
from ad_seller.interfaces import mcp_server
from ad_seller.models.api_key import (
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    ApiKeyRole,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import BuyerIdentity

_DEALS = "ad_seller.services.deal_service"

# tool name -> (kwargs, service attribute the tool calls)
OPERATOR_TOOLS = {
    "bulk_deal_operations": ({"operations": "[]"}, f"{_DEALS}.bulk_deal_operations"),
    "create_curated_deal": ({"curator_id": "cur-1"}, f"{_DEALS}.create_curated_deal"),
    "migrate_deal": ({"old_deal_id": "DEAL-1"}, f"{_DEALS}.migrate_deal"),
    "deprecate_deal": ({"deal_id": "DEAL-1", "reason": "r"}, f"{_DEALS}.deprecate_deal"),
    "troubleshoot_deal": (
        {"deal_id": "DEAL-1", "ssp_name": "ssp"},
        f"{_DEALS}.troubleshoot_deal_via_ssp",
    ),
}
BUYER_TOOLS = {
    "request_quote": ({"product_id": "prod-1"}, "ad_seller.services.quote_service.create_quote"),
    "create_order": ({"deal_id": "DEAL-1"}, "ad_seller.services.order_service.create_order"),
}
ALL_TOOLS = {**OPERATOR_TOOLS, **BUYER_TOOLS}


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage._store = store
    return storage


def _seed_key(store, role=ApiKeyRole.BUYER, revoked=False):
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id=f"key-{role.value}",
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12],
        identity=BuyerIdentity(agency_id="agy-1"),
        role=role,
        revoked=revoked,
    )
    store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    return raw_key


def _http_context(headers):
    ctx = MagicMock()
    ctx.request_context.request = MagicMock(headers=headers)
    return ctx


async def _invoke(name, mock_storage, context=None, stdio=False):
    """Call an MCP tool with its service mocked; return (result, service mock)."""
    kwargs, target = ALL_TOOLS[name]
    service = AsyncMock(return_value=[] if name == "bulk_deal_operations" else {"ok": True})
    get_context = (
        patch.object(mcp_server.mcp, "get_context", side_effect=LookupError)
        if stdio
        else patch.object(mcp_server.mcp, "get_context", return_value=context)
    )
    with (
        get_context,
        patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        patch.object(mcp_server, "_static_catalog", AsyncMock(return_value={})),
        patch(target, service),
    ):
        result = await getattr(mcp_server, name)(**kwargs)
    return result, service


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
async def test_anonymous_http_call_is_denied(name, mock_storage):
    result, service = await _invoke(name, mock_storage, _http_context({}))
    assert "authentication_required" in result
    service.assert_not_awaited()


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
async def test_invalid_or_revoked_key_is_denied(name, mock_storage):
    revoked = _seed_key(mock_storage._store, revoked=True)
    for headers in ({"x-api-key": "ask_live_unknown"}, {"x-api-key": revoked}):
        result, service = await _invoke(name, mock_storage, _http_context(headers))
        assert "invalid_credential" in result
        service.assert_not_awaited()


@pytest.mark.parametrize("name", sorted(OPERATOR_TOOLS))
async def test_buyer_key_is_denied_on_operator_tools(name, mock_storage):
    buyer = _seed_key(mock_storage._store, ApiKeyRole.BUYER)
    ctx = _http_context({"authorization": f"Bearer {buyer}"})
    result, service = await _invoke(name, mock_storage, ctx)
    assert "operator_required" in result
    service.assert_not_awaited()


@pytest.mark.parametrize("name", sorted(OPERATOR_TOOLS))
async def test_operator_key_runs_operator_tools(name, mock_storage):
    operator = _seed_key(mock_storage._store, ApiKeyRole.OPERATOR)
    result, service = await _invoke(name, mock_storage, _http_context({"x-api-key": operator}))
    service.assert_awaited_once()
    assert "error" not in result


@pytest.mark.parametrize("role", [ApiKeyRole.BUYER, ApiKeyRole.OPERATOR])
@pytest.mark.parametrize("name", sorted(BUYER_TOOLS))
async def test_any_valid_key_runs_buyer_tools(name, role, mock_storage):
    key = _seed_key(mock_storage._store, role)
    result, service = await _invoke(
        name, mock_storage, _http_context({"authorization": f"Bearer {key}"})
    )
    service.assert_awaited_once()
    assert "error" not in result


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
async def test_stdio_is_trusted(name, mock_storage):
    result, service = await _invoke(name, mock_storage, stdio=True)
    service.assert_awaited_once()
    assert "error" not in result


@pytest.mark.parametrize("name", sorted(BUYER_TOOLS))
async def test_flag_allows_anonymous_buyer_tools(name, mock_storage, anonymous_buyer_writes):
    result, service = await _invoke(name, mock_storage, _http_context({}))
    service.assert_awaited_once()
    assert "error" not in result


@pytest.mark.parametrize("name", sorted(OPERATOR_TOOLS))
async def test_flag_does_not_open_operator_tools(name, mock_storage, anonymous_buyer_writes):
    assert get_settings().allow_anonymous_buyer_writes
    result, service = await _invoke(name, mock_storage, _http_context({}))
    assert "authentication_required" in result
    service.assert_not_awaited()


async def test_buyer_tools_keep_the_public_pricing_context(mock_storage):
    key = _seed_key(mock_storage._store, ApiKeyRole.BUYER)
    _, service = await _invoke("request_quote", mock_storage, _http_context({"x-api-key": key}))
    context = service.await_args.args[1]
    assert context.is_authenticated is False
    assert context.get_pricing_key() == "public"
