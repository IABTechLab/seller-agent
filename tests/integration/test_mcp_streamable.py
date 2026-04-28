"""MCP Streamable HTTP Smoke Tests — /mcp endpoint.

Tests the seller agent's primary MCP transport (Streamable HTTP at /mcp)
against a live running server. Separate from test_mcp_integration.py which
uses mocked backends.

Usage:
    # Start the seller server first:
    #   uvicorn ad_seller.interfaces.api.main:app --port 8000
    #
    # Then run:
    #   pytest tests/integration/test_mcp_streamable.py -v

Requires a running seller server on port 8000 (or set SELLER_MCP_HTTP_URL).

Note: no @pytest.mark.asyncio decorators needed — pyproject.toml sets
asyncio_mode = "auto" which handles all async test functions automatically.
Adding the decorator alongside AUTO mode causes double collection.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

import pytest

# ---------------------------------------------------------------------------
# Optional MCP SDK imports
# ---------------------------------------------------------------------------
try:
    from mcp.client.streamable_http import streamable_http_client
    from mcp import ClientSession
    MCP_HTTP_AVAILABLE = True
except ImportError:
    try:
        from mcp.client.streamable_http import streamablehttp_client as streamable_http_client  # type: ignore[no-redef]
        from mcp import ClientSession
        MCP_HTTP_AVAILABLE = True
    except ImportError:
        MCP_HTTP_AVAILABLE = False

MCP_HTTP_URL = os.environ.get("SELLER_MCP_HTTP_URL", "http://127.0.0.1:3000/mcp")
TOOL_TIMEOUT = float(os.environ.get("MCP_TOOL_TIMEOUT", "15"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MCP_HTTP_AVAILABLE, reason="mcp streamable_http client not available"),
]


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _mcp_session():
    """Open a fresh Streamable HTTP MCP session for one test."""
    try:
        async with streamable_http_client(MCP_HTTP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    except Exception as exc:
        pytest.skip(f"Seller /mcp not reachable at {MCP_HTTP_URL}: {exc}")


async def _call(session: "ClientSession", name: str, args: dict | None = None):
    """Call an MCP tool and return (is_error, data)."""
    try:
        result = await asyncio.wait_for(
            session.call_tool(name, arguments=args or {}),
            timeout=TOOL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        pytest.fail(f"Tool '{name}' timed out after {TOOL_TIMEOUT}s on /mcp")

    content = result.content
    if not content or not hasattr(content[0], "text"):
        return False, {}
    text = content[0].text
    if text.startswith("Error executing tool"):
        return True, {"raw_error": text}
    try:
        return False, json.loads(text)
    except json.JSONDecodeError:
        return False, {"raw_text": text}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

async def test_streamable_http_connection():
    """/mcp must accept a session and initialize successfully."""
    async with _mcp_session() as session:
        assert session is not None


async def test_streamable_http_tool_list():
    """/mcp must advertise all foundation tools."""
    async with _mcp_session() as session:
        result = await asyncio.wait_for(session.list_tools(), timeout=TOOL_TIMEOUT)
        tool_names = {t.name for t in result.tools}
        for required in ("health_check", "get_setup_status", "get_config"):
            assert required in tool_names, (
                f"Required tool '{required}' missing — got: {sorted(tool_names)}"
            )


# ---------------------------------------------------------------------------
# Foundation tools
# ---------------------------------------------------------------------------

async def test_health_check():
    async with _mcp_session() as session:
        err, data = await _call(session, "health_check")
    assert not err, f"health_check error: {data}"
    assert data.get("status") in ("healthy", "degraded")
    assert "checks" in data


async def test_get_setup_status():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_setup_status")
    assert not err, f"get_setup_status error: {data}"
    assert "setup_complete" in data
    assert "publisher_identity" in data
    assert "ad_server" in data


async def test_get_config():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_config")
    assert not err, f"get_config error: {data}"
    assert "publisher" in data
    assert "pricing" in data
    assert "anthropic" not in str(data).lower(), "API key must not be exposed"


# ---------------------------------------------------------------------------
# Inventory & Products
# ---------------------------------------------------------------------------

async def test_list_products():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_products")
    assert not err, f"list_products error: {data}"
    assert "products" in data
    assert isinstance(data["products"], list)


async def test_list_packages():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_packages")
    assert not err, f"list_packages error: {data}"
    assert "packages" in data
    assert isinstance(data["packages"], list)


async def test_get_rate_card():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_rate_card")
    assert not err, f"get_rate_card error: {data}"
    assert "entries" in data
    assert isinstance(data["entries"], list)


async def test_get_sync_status():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_sync_status")
    assert not err, f"get_sync_status error: {data}"


# ---------------------------------------------------------------------------
# Orders & Approvals
# ---------------------------------------------------------------------------

async def test_list_orders():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_orders")
    assert not err, f"list_orders error: {data}"


async def test_list_pending_approvals():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_pending_approvals")
    assert not err, f"list_pending_approvals error: {data}"


async def test_get_inbound_queue():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_inbound_queue")
    assert not err, f"get_inbound_queue error: {data}"
    assert "items" in data
    assert "count" in data


# ---------------------------------------------------------------------------
# Buyer agents & SSPs
# ---------------------------------------------------------------------------

async def test_list_buyer_agents():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_buyer_agents")
    assert not err, f"list_buyer_agents error: {data}"


async def test_list_ssps():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_ssps")
    assert not err, f"list_ssps error: {data}"
    assert "connectors" in data


async def test_list_agents():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_agents")
    assert not err, f"list_agents error: {data}"
    assert "hierarchy" in data


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

async def test_api_key_lifecycle():
    """Full create → list → revoke lifecycle over /mcp."""
    async with _mcp_session() as session:
        err, created = await _call(session, "create_api_key", {
            "name": "smoke-test-key",
            "label": "mcp-streamable-smoke",
        })
        assert not err, f"create_api_key failed: {created}"
        key_id = created.get("key_id")
        assert key_id, "Response must include key_id"

        err, listed = await _call(session, "list_api_keys")
        assert not err
        assert any(k.get("key_id") == key_id for k in listed.get("keys", []))

        err, revoked = await _call(session, "revoke_api_key", {"key_id": key_id})
        assert not err, f"revoke_api_key failed: {revoked}"
