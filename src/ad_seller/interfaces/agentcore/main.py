"""Unified AgentCore entrypoint — routes to MCP or HTTP based on AGENTCORE_MODE env var.

This single entrypoint eliminates the need to swap Dockerfile CMD between deploys,
enabling parallel deployment of MCP and HTTP runtimes from the same container image.

Set ``AGENTCORE_MODE`` env var at deploy time:
- ``mcp``  → runs ``mcp_main.py`` (Streamable HTTP MCP server on port 8000)
- ``http`` → runs ``http_main.py`` (BedrockAgentCoreApp on port 8080)

Default is ``http`` for backward compatibility.

Usage::

    AGENTCORE_MODE=mcp  python -m src.ad_seller.interfaces.agentcore.main
    AGENTCORE_MODE=http python -m src.ad_seller.interfaces.agentcore.main
"""

import os
import sys

# Add the src directory to Python path so ad_seller is importable.
# We're at src/ad_seller/interfaces/agentcore/main.py — three levels up to src/
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)


def main():
    mode = os.environ.get("AGENTCORE_MODE", "http").strip().lower()

    if mode == "mcp":
        from ad_seller.interfaces.agentcore.mcp_main import main as mcp_main

        mcp_main()
    elif mode == "http":
        from ad_seller.interfaces.agentcore.http_main import app

        app.run()
    else:
        print(f"ERROR: Unknown AGENTCORE_MODE={mode!r}. Must be 'mcp' or 'http'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
