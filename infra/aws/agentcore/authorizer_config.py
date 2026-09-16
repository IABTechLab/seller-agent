# Donated to IAB Tech Lab

"""Authorizer-config builder for the per-runtime CUSTOM_JWT inbound authorizer.

Produces the `authorizerConfiguration.customJWTAuthorizer` shape that
`agentcore configure` / CreateAgentRuntime / UpdateAgentRuntime expects, from
either the seller-owned Cognito auth stack outputs OR a BYO-IdP discovery URL.

Validated (enterprise-auth-gateway task 0.3, AWS inbound-jwt-authorizer doc):
Cognito `client_credentials` (M2M) tokens carry `client_id` + `scope` but
typically NO `aud`, so we validate on **allowedClients + allowedScopes**, NOT
allowedAudience. At least one of the three is required; when several are set all
are verified.

Usable two ways:
  * imported: ``build_authorizer_config(discovery_url, clients, scopes)`` (tested)
  * CLI: ``python authorizer_config.py --discovery-url ... --allowed-clients a,b
    --allowed-scopes s`` prints the JSON on stdout for deploy.sh to consume.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable


def _split_csv(value: str | Iterable[str] | None) -> list[str]:
    """Normalize a CSV string (or iterable) into a clean list, dropping blanks."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return [p.strip() for p in parts if p and p.strip()]


def build_authorizer_config(
    discovery_url: str,
    allowed_clients: str | Iterable[str] | None = None,
    allowed_scopes: str | Iterable[str] | None = None,
) -> dict:
    """Build the customJWTAuthorizer config for one runtime.

    Args:
        discovery_url: OIDC discovery URL (Cognito pool OR BYO-IdP).
        allowed_clients: client_id(s) — CSV string or iterable. Validated
            against the token's ``client_id`` claim.
        allowed_scopes: scope(s) — CSV string or iterable. Validated against
            the token's ``scope`` claim.

    Returns:
        ``{"customJWTAuthorizer": {"discoveryUrl": ..., "allowedClients": [...],
        "allowedScopes": [...]}}`` — allowedClients/allowedScopes are omitted
        when empty, but at least one MUST be present (else ValueError).

    Raises:
        ValueError: if discovery_url is empty, or neither clients nor scopes
            are supplied (the authorizer requires at least one matcher).
    """
    if not discovery_url or not discovery_url.strip():
        raise ValueError("discovery_url is required for the CUSTOM_JWT authorizer")

    clients = _split_csv(allowed_clients)
    scopes = _split_csv(allowed_scopes)

    if not clients and not scopes:
        raise ValueError(
            "at least one of allowed_clients / allowed_scopes is required "
            "(client_credentials tokens carry no aud, so allowedAudience is unused)"
        )

    authorizer: dict = {"discoveryUrl": discovery_url.strip()}
    if clients:
        authorizer["allowedClients"] = clients
    if scopes:
        authorizer["allowedScopes"] = scopes

    # NOTE: allowedAudience is deliberately NOT set — see module docstring.
    return {"customJWTAuthorizer": authorizer}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit the customJWTAuthorizer JSON for a runtime authorizer."
    )
    parser.add_argument("--discovery-url", required=True)
    parser.add_argument("--allowed-clients", default="", help="CSV of client_ids")
    parser.add_argument("--allowed-scopes", default="", help="CSV of scopes")
    args = parser.parse_args(argv)

    try:
        config = build_authorizer_config(
            args.discovery_url, args.allowed_clients, args.allowed_scopes
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
