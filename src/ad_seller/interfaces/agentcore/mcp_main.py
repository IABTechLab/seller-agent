"""AgentCore MCP runtime entrypoint for the IAB AAMP Seller Agent.

AgentCore in MCP protocol mode expects an MCP server at 0.0.0.0:8000/mcp
using Streamable HTTP transport. This entrypoint runs the seller's FastMCP
server via ``mcp.run(transport="streamable-http")`` which handles the
``/mcp`` route with proper trailing-slash support.

EP-3.2: the MCP tools are now thin adapters that call the seller service
layer (``ad_seller.services``) directly in-process. They no longer reach the
REST API over an httpx loopback, so the old background FastAPI REST sidecar —
which existed solely so loopback tools could resolve to localhost — has been
removed. A single process listens on port 8000 (MCP) and nothing else.

Deploy with::

    agentcore configure -p MCP -e src/ad_seller/interfaces/agentcore/mcp_main.py ...
    agentcore deploy

Local testing::

    python src/ad_seller/interfaces/agentcore/mcp_main.py
    # MCP endpoint: http://localhost:8000/mcp  (Streamable HTTP)
"""

import logging
import os
import sys

# Add the src directory to Python path so ad_seller is importable.
# We're at src/ad_seller/interfaces/agentcore/mcp_main.py — three levels up to src/
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)

# Environment defaults for AgentCore / workshop demo mode
os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-with-bedrock")
os.environ.setdefault("STORAGE_TYPE", "sqlite")
os.environ.setdefault("AD_SERVER_TYPE", "csv")
os.environ.setdefault("CSV_DATA_DIR", "./data/csv/samples/aws_workshop")

# Durable Bedrock auth: mint a fresh bearer token from the execution role at
# startup when none was supplied (see ad_seller.llm.bedrock_token).
from ad_seller.llm.bedrock_token import ensure_bedrock_token  # noqa: E402

ensure_bedrock_token()

logger = logging.getLogger(__name__)

# Price-moving MCP tools whose self-declared buyer tier must be verified against
# the registry before the tool runs. Keyed by tool name; the wrapper reads the
# tier/agent_url from that tool's arguments. Non-listed tools (public discovery,
# health) pass through unverified.
_VERIFIED_TOOLS = {"get_pricing", "request_quote"}


def _install_verification_wrapper(mcp_server) -> None:
    """Wrap FastMCP's ``call_tool`` to verify buyer trust for price-moving tools.

    In-boundary enforcement: for a tool in ``_VERIFIED_TOOLS`` that carries a
    non-public ``buyer_tier``, run the shared verification core
    (``interfaces/agentcore/verification.verify_buyer_context``) — cap the tier
    to the registry ceiling, floor unknown agents to PUBLIC, and REJECT a blocked
    agent before the tool executes. The verified tier is threaded back into the
    tool arguments (``buyer_tier``/``_verified_max_tier``) so the tool prices at
    the capped tier. Fail-closed: on any verification error we floor to public
    rather than let an unverified claim through.
    """
    from ad_seller.interfaces.agentcore.verification import (
        BlockedAgentError,
        verify_buyer_context,
    )
    from ad_seller.models.buyer_identity import AccessTier

    # Wrap the TOOL MANAGER's call_tool, not FastMCP.call_tool: FastMCP captures
    # its own bound `call_tool` into the low-level server at construction (so
    # reassigning it post-init is ignored), but it delegates to
    # `self._tool_manager.call_tool(...)` looked up FRESH per request — so
    # wrapping the manager method reliably intercepts every tools/call.
    tool_manager = mcp_server._tool_manager
    _orig_call_tool = tool_manager.call_tool

    async def _guarded_call_tool(name, arguments, context=None, convert_result=False):
        args = dict(arguments or {})
        tier = args.get("buyer_tier", "public")
        if name in _VERIFIED_TOOLS and tier and tier != "public":
            try:
                ctx = await verify_buyer_context(
                    endpoint=f"mcp:{name}",
                    buyer_tier=tier,
                    agent_url=args.get("agent_url") or None,
                )
                # Cap the claimed tier to the verified effective tier so the
                # tool cannot price above the registry ceiling.
                args["buyer_tier"] = ctx.effective_tier.value if ctx else "public"
            except BlockedAgentError:
                raise ValueError("Agent is blocked. Contact the seller operator for access.")
            except Exception as exc:  # noqa: BLE001 — fail closed to public
                logger.warning("MCP verification failed (%s) — flooring to public.", exc)
                args["buyer_tier"] = AccessTier.PUBLIC.value
        return await _orig_call_tool(name, args, context=context, convert_result=convert_result)

    tool_manager.call_tool = _guarded_call_tool


def main():
    """Start the MCP server on port 8000 with Streamable HTTP transport.

    Uses ``mcp.run(transport="streamable-http")`` which is the pattern
    from the AgentCore MCP docs. This handles ``POST /mcp`` and ``POST /mcp/``
    correctly.

    No background REST server is started: MCP tools call the service layer
    directly in-process (EP-3.2), so there is nothing to loop back to.
    """
    # Import and run the MCP server — this blocks on port 8000
    from mcp.server.transport_security import TransportSecuritySettings

    from ad_seller.interfaces.mcp_server import mcp as mcp_server

    # Req 8 — in-boundary MCP verification (belt-and-suspenders).
    # The seller MCP runtime enforces a CUSTOM_JWT authorizer at the edge (WHO
    # may call), but request-time REGISTRY verification (tier ceiling / block)
    # for price-moving tools lives in the tool bodies in `mcp_server.py` — shared
    # external surface owned by the core maintainers. Rather than edit that
    # surface here, we wrap FastMCP's single `call_tool` dispatch method (the
    # funnel every tools/call passes through in mcp==1.28.x) from THIS in-boundary
    # launcher, so verification holds even if the optional in-tool edit
    # (`mcp_server.get_pricing`, a separate cherry-pickable commit) is absent.
    # If that commit IS present, the in-tool check is a redundant, idempotent
    # second layer — never a conflict.
    _install_verification_wrapper(mcp_server)

    # Ensure stateless_http is set for AgentCore compatibility
    mcp_server.settings.stateless_http = True
    mcp_server.settings.host = "0.0.0.0"
    mcp_server.settings.port = 8000
    # AgentCore sends POST /mcp/ (with trailing slash)
    mcp_server.settings.streamable_http_path = "/mcp/"

    # Disable DNS rebinding protection for AgentCore deployment.
    # The FastMCP constructor auto-enables it when host="127.0.0.1" (the default),
    # but AgentCore's sidecar proxy forwards requests with its own Host header
    # (e.g. cell01.us-west-2.prod.arp.kepler-analytics.aws.dev) which doesn't
    # match the default allowed_hosts list, causing HTTP 421 Misdirected Request.
    # Since AgentCore handles network security at the infrastructure level,
    # DNS rebinding protection is not needed here.
    mcp_server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )

    mcp_server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
