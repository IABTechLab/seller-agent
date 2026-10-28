# Donated to IAB Tech Lab

"""Live post-deploy smoke test for the seller A2A runtime.

Closes the coverage gap: nothing exercised the deployed A2A (JSON-RPC) runtime
after deploy. This posts a minimal agent-card / message request to the A2A
runtime's real ``/invocations`` endpoint with a Cognito ``client_credentials``
JWT and asserts a well-formed JSON-RPC response — i.e. the JWT-protected A2A
surface is up, authenticates the bearer token, and speaks JSON-RPC (not a
transport/protocol error).

Scope is deliberately a smoke test: there is no buyer-side A2A client yet, so
this proves the runtime is reachable + authenticated, not a full A2A exchange.
Resolves the ARN from ``SELLER_A2A_RUNTIME_ARN`` or ``.bedrock_agentcore.yaml``
(agent ``aamp_seller_a2a``); skips cleanly when absent.
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ad_seller.registry.runtime_registration import agentcore_invocations_url  # noqa: E402
from tests.integration.agentcore.test_mcp_runtime import _resolve_runtime_arn  # noqa: E402
from tests.integration.agentcore.test_runtime import _mint_bearer_token  # noqa: E402

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def a2a_url() -> str:
    arn = _resolve_runtime_arn("aamp_seller_a2a", "SELLER_A2A_RUNTIME_ARN")
    if not arn:
        pytest.skip("No A2A runtime ARN — set SELLER_A2A_RUNTIME_ARN or deploy the a2a runtime")
    return agentcore_invocations_url(arn)


@pytest.fixture(scope="module")
def token() -> Optional[str]:
    tok = _mint_bearer_token(os.environ.get("AWS_REGION", "us-west-2"))
    if not tok:
        pytest.skip("No bearer token — auth stack absent or secret unreadable")
    return tok


def _post_jsonrpc(url: str, token: str, body: dict, timeout: int = 60):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    return resp.status, raw


def test_a2a_runtime_authenticates_and_speaks_jsonrpc(a2a_url, token):
    """A2A runtime is up, accepts the JWT, and returns well-formed JSON-RPC.

    We send a minimal JSON-RPC request. The runtime may legitimately return a
    JSON-RPC *error* object for an unsupported method — that is still proof the
    surface is authenticated and speaking the protocol. What must NOT happen is
    a 401/403 (auth failure) or a non-JSON transport error.
    """
    body = {"jsonrpc": "2.0", "id": "smoke-1", "method": "agent/getAuthenticatedExtendedCard"}
    try:
        status, raw = _post_jsonrpc(a2a_url, token, body)
    except urllib.error.HTTPError as e:  # noqa: PERF203
        # 401/403 = auth broken (real failure); other codes may still carry a
        # JSON-RPC error body we can validate below.
        assert e.code not in (401, 403), f"A2A rejected the JWT (HTTP {e.code})"
        raw = e.read().decode()
        status = e.code

    assert raw.strip(), f"empty A2A response (HTTP {status})"
    parsed = json.loads(raw)  # must be valid JSON-RPC, not a transport error blob
    assert parsed.get("jsonrpc") == "2.0", f"not a JSON-RPC response: {parsed}"
    assert "result" in parsed or "error" in parsed, f"malformed JSON-RPC: {parsed}"
