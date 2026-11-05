# Donated to IAB Tech Lab

"""Live post-deploy integration tests for the seller MCP runtime.

Closes the coverage gap: the chat/crew suite (``test_runtime.py``) only exercises
the HTTP runtime. These tests drive the DEPLOYED MCP runtime over its real
Streamable-HTTP ``/invocations`` endpoint with a Cognito ``client_credentials``
JWT (``Authorization: Bearer``), the same transport the cross-org buyer uses:

1. ``tools/list`` succeeds → auth + transport + server are healthy.
2. ``get_pricing`` at the PUBLIC tier returns a real price → the in-boundary
   call-tool wrapper (``mcp_main._install_verification_wrapper``) passes public
   calls straight through.
3. ``get_pricing`` with a NON-PUBLIC tier + an UNREGISTERED ``agent_url`` comes
   back capped/floored (no advertiser-tier escalation) → the wrapper's live
   fail-closed verification engages, not just its unit tests.

Requires a deployed MCP runtime + the auth stack. Resolves the runtime ARN from
``SELLER_MCP_RUNTIME_ARN`` or ``.bedrock_agentcore.yaml`` (agent ``aamp_seller_mcp``);
skips cleanly when neither is present so the suite is safe pre-deploy.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ad_seller.registry.runtime_registration import agentcore_invocations_url  # noqa: E402

# Reuse the token minter + ARN resolution from the HTTP suite (same seams).
from tests.integration.agentcore.test_runtime import _mint_bearer_token  # noqa: E402

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


def _resolve_runtime_arn(agent_name: str, env_var: str) -> str:
    """Resolve a deployed runtime ARN, stale-proof (live ListAgentRuntimes by name).

    Delegates to the shared ``resolve_live_runtime_arn`` so a delete+recreate
    (forced by the immutable-VPC / header-allowlist constraints) can't leave this
    suite invoking a dead ARN from the yaml snapshot.
    """
    from tests.integration.agentcore.conftest import resolve_live_runtime_arn

    return resolve_live_runtime_arn(agent_name, env_var)


@pytest.fixture(scope="module")
def mcp_url() -> str:
    arn = _resolve_runtime_arn("aamp_seller_mcp", "SELLER_MCP_RUNTIME_ARN")
    if not arn:
        pytest.skip("No MCP runtime ARN — set SELLER_MCP_RUNTIME_ARN or deploy the mcp runtime")
    return agentcore_invocations_url(arn)


@pytest.fixture(scope="module")
def token() -> Optional[str]:
    region = os.environ.get("AWS_REGION", "us-west-2")
    tok = _mint_bearer_token(region)
    if not tok:
        pytest.skip("No bearer token — auth stack absent or secret unreadable")
    return tok


async def _call_tool(mcp_url: str, token: str, name: str, arguments: dict):
    """Open an authenticated Streamable-HTTP MCP session and call one tool."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if name == "__list__":
                return await session.list_tools()
            return await session.call_tool(name, arguments)


def _result_text(result) -> str:
    """Flatten a CallToolResult's content blocks to text for assertions."""
    parts = []
    for block in getattr(result, "content", []) or []:
        parts.append(getattr(block, "text", "") or "")
    return " ".join(parts)


async def test_mcp_tools_list_over_jwt(mcp_url, token):
    """Auth + transport + server healthy: tools/list returns the seller tools."""
    result = await _call_tool(mcp_url, token, "__list__", {})
    names = {t.name for t in result.tools}
    assert "get_pricing" in names, f"get_pricing not advertised; got {names}"


async def test_mcp_list_products_returns_catalog(mcp_url, token):
    """list_products returns the real inv-* catalog over JWT (seller-only proof).

    Drives the seller MCP runtime directly — no buyer, no client-side filter —
    so it isolates "does the deployed seller serve its catalog" from the buyer's
    OpenDirect discovery path. list_products takes only a limit (no query), so
    it returns the full catalog; the assertion is that real seller catalog
    product_ids (``inv-*``) come back with a positive count.
    """
    import json as _json
    import re as _re

    result = await _call_tool(mcp_url, token, "list_products", {"limit": 50})
    text = _result_text(result)
    assert not result.isError, f"list_products errored: {text}"

    # Parse the JSON body the tool returns (shared ProductListResponse:
    # {"products": [...], "total_count": N, "limit": ..., "offset": ...}).
    payload = _json.loads(text)
    assert payload.get("total_count", 0) > 0, f"empty catalog: {text[:400]}"
    inv_ids = sorted(
        p["product_id"]
        for p in payload["products"]
        if _re.match(r"inv-", str(p.get("product_id", "")))
    )
    assert inv_ids, f"no inv-* product_ids in catalog: {text[:400]}"
    # Each product must carry seller_organization_id: the MCP catalog now
    # serializes through the shared boundary mapper, so cross-org buyers can
    # validate the records against the shared WireProduct (which requires it).
    assert all(
        p.get("seller_organization_id") for p in payload["products"]
    ), f"product missing seller_organization_id: {text[:400]}"
    logger.info("seller catalog served %d inv-* products: %s", len(inv_ids), inv_ids[:5])


async def test_mcp_get_pricing_public_passes_through(mcp_url, token):
    """PUBLIC tier: wrapper passes through, tool returns a real price."""
    result = await _call_tool(
        mcp_url, token, "get_pricing", {"product_id": "inv-ctv-001", "buyer_tier": "public"}
    )
    text = _result_text(result)
    assert not result.isError, f"public get_pricing errored: {text}"
    assert text.strip(), "expected a non-empty pricing response"


async def test_mcp_get_pricing_unregistered_advertiser_is_capped(mcp_url, token):
    """NON-PUBLIC tier + unregistered agent_url: wrapper caps/floors, no error.

    A self-declared 'advertiser' from an agent the registry does not know must
    NOT be honored at advertiser tier. The wrapper verifies live and floors to
    public; the call still succeeds (capped), it is not rejected.
    """
    result = await _call_tool(
        mcp_url,
        token,
        "get_pricing",
        {
            "product_id": "inv-ctv-001",
            "buyer_tier": "advertiser",
            "agent_url": "https://unregistered.example.com/agent",
        },
    )
    text = _result_text(result)
    # Not blocked (unregistered != blocked) and not an escalation error.
    assert not result.isError, f"capped get_pricing should still succeed: {text}"
    # Fail-closed floor means it did not price as a verified advertiser.
    assert "advertiser" not in text.lower() or "public" in text.lower(), (
        f"unregistered agent appears to have been honored at advertiser tier: {text}"
    )
