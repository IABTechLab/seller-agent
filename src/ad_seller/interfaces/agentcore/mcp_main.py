"""AgentCore MCP runtime entrypoint for the IAB AAMP Seller Agent.

AgentCore in MCP protocol mode expects an MCP server at 0.0.0.0:8000/mcp
using Streamable HTTP transport. This entrypoint runs the seller's FastMCP
server via ``mcp.run(transport="streamable-http")`` which handles the
``/mcp`` route with proper trailing-slash support.

The FastAPI REST API is started in a background thread on port 8001 so
that MCP tools which call back to REST endpoints via httpx can resolve
to localhost.

Deploy with::

    agentcore configure -p MCP -e src/ad_seller/interfaces/agentcore/mcp_main.py ...
    agentcore deploy

Local testing::

    python src/ad_seller/interfaces/agentcore/mcp_main.py
    # MCP endpoint: http://localhost:8000/mcp  (Streamable HTTP)
    # REST API:     http://localhost:8001/health, /api/v1/...
"""

import asyncio
import logging
import os
import sys
import threading

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

logger = logging.getLogger(__name__)

_INTERNAL_REST_PORT = int(os.environ.get("INTERNAL_API_PORT", "8001"))


def _start_fastapi_background():
    """Start FastAPI REST API on an internal port in a background thread.

    Required because some MCP tools (transition_order, create_deal_from_template,
    etc.) call back to the REST API via httpx to localhost.
    """
    import uvicorn

    from ad_seller.interfaces.api.main import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=_INTERNAL_REST_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=_run, daemon=True, name="fastapi-rest-bg")
    thread.start()
    logger.info("FastAPI REST background server starting on port %d", _INTERNAL_REST_PORT)


def main():
    """Start the MCP server on port 8000 with Streamable HTTP transport.

    Uses ``mcp.run(transport="streamable-http")`` which is the pattern
    from the AgentCore MCP docs. This handles ``POST /mcp`` and ``POST /mcp/``
    correctly.

    FastAPI REST API runs in a background thread on port 8001 for MCP tool
    callbacks via httpx.
    """
    # Point MCP tools that call REST API to the internal port
    os.environ.setdefault("SELLER_AGENT_URL", f"http://localhost:{_INTERNAL_REST_PORT}")

    # Start FastAPI REST in background for MCP tool callbacks
    _start_fastapi_background()

    # Import and run the MCP server — this blocks on port 8000
    from mcp.server.transport_security import TransportSecuritySettings

    from ad_seller.interfaces.mcp_server import mcp as mcp_server

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
