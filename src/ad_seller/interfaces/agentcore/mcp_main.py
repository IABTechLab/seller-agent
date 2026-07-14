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

logger = logging.getLogger(__name__)


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
