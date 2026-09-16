# Donated to IAB Tech Lab

"""Register the seller's AgentCore runtimes with the AAMP registry (Req 6.1/6.2).

For the AWS Bedrock AgentCore deployment path, each seller runtime (mcp/a2a)
is a CROSS-ORG endpoint a buyer discovers via the AAMP registry rather than a
hardcoded ARN. This module builds one registry card per runtime and publishes
it via the registry client's ``register_self``:

- ``type='remote'`` + ``endpoint_url`` = the runtime's HTTPS ``InvokeAgentRuntime``
  URL (verified shape:
  ``https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<ENCODED_ARN>/invocations?qualifier=DEFAULT``).
- ``protocol_type`` = ``mcp`` | ``a2a``.
- ``auth_required=True`` and an ``authentication`` block advertising the shared
  ``token_endpoint`` + ``scope`` so the buyer's OAuthTokenProvider knows where to
  mint a client_credentials JWT. (The buyer holds its OWN client_id/secret; the
  registry never carries a secret.)

Also prints (Req 6.5) each runtime URL, the shared token endpoint, and an
example ``client_credentials`` curl, so an operator can wire a buyer by hand.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def agentcore_invocations_url(runtime_arn: str, *, qualifier: str = "DEFAULT") -> str:
    """HTTPS MCP/A2A invocation URL for an AgentCore runtime ARN (verified shape)."""
    parts = runtime_arn.split(":")
    region = parts[3] if len(parts) > 4 else ""
    encoded = runtime_arn.replace(":", "%3A").replace("/", "%2F")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com"
        f"/runtimes/{encoded}/invocations?qualifier={qualifier}"
    )


def build_runtime_card(
    *,
    agent_name: str,
    primary_domain: str,
    runtime_arn: str,
    protocol_type: str,
    token_endpoint: str,
    scope: str,
    description: str = "",
) -> dict:
    """Build one AAMP registry card for a seller runtime (auth-advertising)."""
    return {
        "agent_name": agent_name,
        "primary_domain": primary_domain,
        "type": "remote",
        "endpoint_url": agentcore_invocations_url(runtime_arn),
        "protocol_type": protocol_type,
        "auth_required": True,
        # Extra (forward-compat) block the buyer's transport selector reads to
        # learn WHERE to authenticate. The buyer supplies its own client creds.
        "authentication": {
            "type": "oauth2",
            "grant_type": "client_credentials",
            "token_endpoint": token_endpoint,
            "scope": scope,
        },
        "description": description or f"AAMP seller {protocol_type} runtime",
    }


async def register_runtimes(
    registry_client,
    *,
    primary_domain: str,
    token_endpoint: str,
    scope: str,
    runtimes: dict[str, dict[str, str]],
) -> list[dict]:
    """Register each runtime card via ``registry_client.register_self``.

    Args:
        registry_client: an ``AampApiRegistryClient`` (has ``register_self``).
        primary_domain: the seller's company domain (must match the JWT).
        token_endpoint / scope: the shared Cognito (or BYO-IdP) token endpoint + scope.
        runtimes: ``{agent_name: {"arn": ..., "protocol_type": "mcp"|"a2a"}}``.

    Returns the stored records (skips any that failed; never raises).
    """
    stored: list[dict] = []
    for agent_name, meta in runtimes.items():
        card = build_runtime_card(
            agent_name=agent_name,
            primary_domain=primary_domain,
            runtime_arn=meta["arn"],
            protocol_type=meta.get("protocol_type", "mcp"),
            token_endpoint=token_endpoint,
            scope=scope,
        )
        result = await registry_client.register_self(card)
        if result is not None:
            stored.append(result)
    return stored


def print_connection_info(
    *,
    token_endpoint: str,
    scope: str,
    runtimes: dict[str, dict[str, str]],
) -> str:
    """Return a human-readable block (Req 6.5): runtime URLs + token endpoint + curl.

    The client_id/secret are placeholders — the buyer supplies its OWN; we never
    print a real secret.
    """
    lines = [
        "=== AAMP seller runtimes — cross-org connection info ===",
        f"Shared token endpoint : {token_endpoint}",
        f"Invoke scope          : {scope}",
        "",
        "Runtime endpoints (HTTPS InvokeAgentRuntime, Bearer JWT):",
    ]
    for agent_name, meta in runtimes.items():
        url = agentcore_invocations_url(meta["arn"])
        lines.append(f"  [{meta.get('protocol_type', 'mcp')}] {agent_name}: {url}")
    lines += [
        "",
        "Mint a client_credentials token (buyer supplies its OWN client id/secret):",
        "  curl -s -X POST \\",
        f"    '{token_endpoint}' \\",
        "    -H 'Content-Type: application/x-www-form-urlencoded' \\",
        "    -u '<BUYER_CLIENT_ID>:<BUYER_CLIENT_SECRET>' \\",
        f"    -d 'grant_type=client_credentials&scope={scope}'",
        "",
        "Then invoke a runtime with:  Authorization: Bearer <access_token>",
    ]
    return "\n".join(lines)


def _runtimes_from_env() -> dict[str, dict[str, str]]:
    """Build the runtimes map from SELLER_*_RUNTIME_ARN env vars (mcp/a2a/http)."""
    import os

    mapping = {
        "mcp": os.environ.get("SELLER_MCP_RUNTIME_ARN", ""),
        "a2a": os.environ.get("SELLER_A2A_RUNTIME_ARN", ""),
        "http": os.environ.get("SELLER_HTTP_RUNTIME_ARN", ""),
    }
    runtimes: dict[str, dict[str, str]] = {}
    for proto, arn in mapping.items():
        if not arn:
            continue
        # Derive the agent_name from the ARN tail (…/runtime/<name>-<id>).
        tail = arn.rsplit("/", 1)[-1]
        agent_name = tail.rsplit("-", 1)[0] if "-" in tail else tail
        runtimes[agent_name] = {"arn": arn, "protocol_type": proto}
    return runtimes


def main() -> int:
    """CLI: register seller runtimes + print connection info.

    Reads: AAMP_REGISTRY_URL/AAMP_REGISTRY_AUTH_TOKEN (registry client),
    SELLER_PRIMARY_DOMAIN, SELLER_TOKEN_ENDPOINT, SELLER_INVOKE_SCOPE, and the
    SELLER_{MCP,A2A,HTTP}_RUNTIME_ARN vars. Print-only when no registry URL.
    """
    import asyncio
    import os

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    token_endpoint = os.environ.get("SELLER_TOKEN_ENDPOINT", "")
    scope = os.environ.get("SELLER_INVOKE_SCOPE", "seller-agent/invoke")
    runtimes = _runtimes_from_env()
    if not runtimes:
        print("No SELLER_{MCP,A2A,HTTP}_RUNTIME_ARN set — nothing to register.")
        return 0

    # Always print the connection info (Req 6.5).
    print(print_connection_info(token_endpoint=token_endpoint, scope=scope, runtimes=runtimes))

    if not os.environ.get("AAMP_REGISTRY_URL"):
        print("\nAAMP_REGISTRY_URL unset — printed connection info only (no registration).")
        return 0

    from ad_seller.clients.agent_registry_client import AampApiRegistryClient

    primary_domain = os.environ.get("SELLER_PRIMARY_DOMAIN", "")
    if not primary_domain:
        print("SELLER_PRIMARY_DOMAIN unset — required to register; printed info only.")
        return 0

    client = AampApiRegistryClient()
    stored = asyncio.run(
        register_runtimes(
            client,
            primary_domain=primary_domain,
            token_endpoint=token_endpoint,
            scope=scope,
            runtimes=runtimes,
        )
    )
    print(f"\nRegistered {len(stored)} runtime(s) to the AAMP registry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
