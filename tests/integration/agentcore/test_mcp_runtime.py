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
    """Resolve a deployed runtime ARN from an env override or the deploy yaml."""
    arn = os.environ.get(env_var, "")
    if arn:
        return arn
    yaml_path = Path(__file__).resolve().parents[3] / ".bedrock_agentcore.yaml"
    if not yaml_path.exists():
        return ""
    import yaml

    with open(yaml_path) as f:
        cfg = yaml.safe_load(f) or {}
    agent = cfg.get("agents", {}).get(agent_name, {})
    return agent.get("bedrock_agentcore", {}).get("agent_arn", "") or ""


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
