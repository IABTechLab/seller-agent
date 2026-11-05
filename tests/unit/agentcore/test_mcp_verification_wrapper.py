# Donated to IAB Tech Lab

"""Tests for the in-boundary MCP call-tool verification wrapper (Req 8).

The wrapper (installed by ``mcp_main._install_verification_wrapper``) enforces
registry verification for price-moving MCP tools at the transport boundary, so
the guarantee holds even without the optional in-tool edit (commit B).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ad_seller.interfaces.agentcore import mcp_main  # noqa: E402
from ad_seller.interfaces.agentcore.verification import BlockedAgentError  # noqa: E402
from ad_seller.models.buyer_identity import AccessTier  # noqa: E402


def _fake_server():
    """A stand-in FastMCP with a _tool_manager whose call_tool records args."""
    calls = []

    async def orig_call_tool(name, arguments, context=None, convert_result=False):
        calls.append((name, dict(arguments)))
        return [{"type": "text", "text": "ok"}]

    tm = SimpleNamespace(call_tool=orig_call_tool)
    server = SimpleNamespace(_tool_manager=tm)
    return server, calls


@pytest.mark.asyncio
async def test_wrapper_caps_tier_to_ceiling():
    server, calls = _fake_server()
    ctx = SimpleNamespace(effective_tier=AccessTier.SEAT)
    with patch(
        "ad_seller.interfaces.agentcore.verification.verify_buyer_context",
        new=AsyncMock(return_value=ctx),
    ):
        mcp_main._install_verification_wrapper(server)
        await server._tool_manager.call_tool(
            "get_pricing", {"product_id": "p1", "buyer_tier": "advertiser", "agent_url": "u"}
        )
    # The tool ran with the CAPPED tier, not the claimed 'advertiser'.
    assert calls[0][1]["buyer_tier"] == "seat"


@pytest.mark.asyncio
async def test_wrapper_blocks_blocked_agent():
    server, calls = _fake_server()
    with patch(
        "ad_seller.interfaces.agentcore.verification.verify_buyer_context",
        new=AsyncMock(side_effect=BlockedAgentError("u")),
    ):
        mcp_main._install_verification_wrapper(server)
        with pytest.raises(ValueError, match="blocked"):
            await server._tool_manager.call_tool(
                "get_pricing", {"buyer_tier": "advertiser", "agent_url": "u"}
            )
    assert calls == []  # tool never ran


@pytest.mark.asyncio
async def test_wrapper_passes_public_through_unverified():
    server, calls = _fake_server()
    with patch(
        "ad_seller.interfaces.agentcore.verification.verify_buyer_context",
        new=AsyncMock(side_effect=AssertionError("should not verify public")),
    ):
        mcp_main._install_verification_wrapper(server)
        await server._tool_manager.call_tool("get_pricing", {"buyer_tier": "public"})
    assert calls[0][1]["buyer_tier"] == "public"


@pytest.mark.asyncio
async def test_wrapper_ignores_non_price_tools():
    server, calls = _fake_server()
    with patch(
        "ad_seller.interfaces.agentcore.verification.verify_buyer_context",
        new=AsyncMock(side_effect=AssertionError("should not verify list_products")),
    ):
        mcp_main._install_verification_wrapper(server)
        await server._tool_manager.call_tool("list_products", {"buyer_tier": "advertiser"})
    assert calls[0][0] == "list_products"


@pytest.mark.asyncio
async def test_wrapper_fails_closed_to_public_on_error():
    server, calls = _fake_server()
    with patch(
        "ad_seller.interfaces.agentcore.verification.verify_buyer_context",
        new=AsyncMock(side_effect=RuntimeError("registry down")),
    ):
        mcp_main._install_verification_wrapper(server)
        await server._tool_manager.call_tool(
            "get_pricing", {"buyer_tier": "advertiser", "agent_url": "u"}
        )
    assert calls[0][1]["buyer_tier"] == "public"  # floored, not the claimed tier
