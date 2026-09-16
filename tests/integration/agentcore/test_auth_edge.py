# Donated to IAB Tech Lab

"""Task 7.6 — live CUSTOM_JWT edge-rejection on the deployed seller MCP runtime.

Complements the positive-path suites (``test_mcp_runtime.py``): here we assert
the CUSTOM_JWT authorizer REJECTS unauthenticated calls at the edge, before any
tool runs:

- absent ``Authorization`` header → rejected (401/403),
- a syntactically-valid-but-bogus bearer token → rejected (401/403),
- a real minted ``client_credentials`` token → accepted (control; skipped if the
  auth stack / secret is unavailable).

The negative cases need no valid token (that is the point), so they POST a
minimal MCP ``initialize`` to the runtime's ``/invocations`` URL and observe the
HTTP status directly. Resolves the ARN from ``SELLER_MCP_RUNTIME_ARN`` or the
deploy yaml; skips cleanly pre-deploy.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ad_seller.registry.runtime_registration import agentcore_invocations_url  # noqa: E402
from tests.integration.agentcore.test_mcp_runtime import _resolve_runtime_arn  # noqa: E402
from tests.integration.agentcore.test_runtime import _mint_bearer_token  # noqa: E402

pytestmark = pytest.mark.asyncio

_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": "edge-1",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "edge-test", "version": "0.0.0"},
    },
}


@pytest.fixture(scope="module")
def mcp_url() -> str:
    arn = _resolve_runtime_arn("aamp_seller_mcp", "SELLER_MCP_RUNTIME_ARN")
    if not arn:
        pytest.skip("No MCP runtime ARN — set SELLER_MCP_RUNTIME_ARN or deploy the mcp runtime")
    return agentcore_invocations_url(arn)


async def _status_for_headers(mcp_url: str, headers: dict) -> int:
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    base.update(headers)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(mcp_url, headers=base, content=json.dumps(_INIT_BODY))
    return resp.status_code


async def test_absent_token_rejected_at_edge(mcp_url):
    """No Authorization header → the CUSTOM_JWT edge rejects (401/403)."""
    status = await _status_for_headers(mcp_url, {})
    assert status in (401, 403), f"expected edge rejection without a token, got HTTP {status}"


async def test_bogus_token_rejected_at_edge(mcp_url):
    """A bogus bearer token → rejected (401/403), never reaches a tool."""
    status = await _status_for_headers(mcp_url, {"Authorization": "Bearer not-a-real-jwt"})
    assert status in (401, 403), f"expected edge rejection for a bogus token, got HTTP {status}"


async def test_valid_token_accepted(mcp_url):
    """Control: a real minted client_credentials token is accepted (not 401/403)."""
    token: Optional[str] = _mint_bearer_token(os.environ.get("AWS_REGION", "us-west-2"))
    if not token:
        pytest.skip("No bearer token — auth stack absent or secret unreadable")
    status = await _status_for_headers(mcp_url, {"Authorization": f"Bearer {token}"})
    assert status not in (401, 403), f"valid token was rejected at the edge (HTTP {status})"
