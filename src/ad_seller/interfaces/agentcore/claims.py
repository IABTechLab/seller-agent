# Donated to IAB Tech Lab
"""Claim → tier mapping seam (spec task 5.2, defense-in-depth).

The runtime's CUSTOM_JWT authorizer already validates the inbound bearer token
at the AgentCore edge (signature via the IdP discovery URL, ``allowedClients``,
``allowedScopes``) BEFORE the request ever reaches the entrypoint. By the time
this module runs, the token is known-authentic and known-authorized to invoke.

What this seam adds is a SECOND layer: instead of trusting the buyer's
self-declared ``buyer_tier`` payload field, derive the *claimed* tier from the
verified JWT's own claims — its ``client_id`` (which app client minted the
token) and OAuth ``scope`` — via an operator-configured mapping. That claimed
tier is then handed to the SAME ``verify_buyer_context`` cap → floor → block
path the rest of the AgentCore surface uses, so the registry-verified ceiling
still caps it. A buyer can no longer self-assert a higher tier than the token
its own client minted is mapped to.

Design notes:
- PRIMARY source (Req 13, managed CUSTOM_JWT-authorizer path): the runtime's
  authorizer validates + STRIPS the inbound bearer at the edge and forwards only
  allowlisted headers. deploy.sh allowlists
  ``X-Amzn-Bedrock-AgentCore-Runtime-Custom-Tier``; its value is a scope/tier the
  edge already verified, so the seam reads it directly — no JWT decode, no env
  mapping required.
- SECONDARY source (forwarding transports): when the raw ``Authorization: Bearer``
  reaches the entrypoint (a direct HTTPS/MCP client, or a BYO front door), we read
  the token from the header and decode its payload segment for ``client_id``/
  ``scope``.
- We do an UNVERIFIED decode of the raw JWT on purpose: the authorizer has already
  verified signature + client + scope at the edge, and re-verifying here would
  require re-fetching the IdP JWKS on every invoke for zero added trust. We read
  only ``client_id``/``scope`` for tier mapping — never for the authentication
  decision, which the edge owns.
- Fail-OPEN to the payload tier: if neither source yields a tier, the seam
  returns ``None`` and the caller keeps its existing payload-``buyer_tier``
  behavior unchanged. This keeps the change additive and backward-compatible.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Environment variable carrying the client_id → tier mapping, as a compact
# ``client_id:tier`` comma list, e.g.
#   CLAIM_TIER_MAP="3pkkomqgfa24n...:strategic_advertiser,7xabc...:preferred_agency"
# Tier strings match the seller's _TIER_MAP keys in http_main
# (public | registered_buyer | preferred_agency | strategic_advertiser).
CLAIM_TIER_MAP_ENV = "CLAIM_TIER_MAP"

# Optional scope → tier mapping (space-separated scopes on the token are checked
# against this; the HIGHEST matching tier wins). Same string format.
# e.g. SCOPE_TIER_MAP="seller-agent/advertiser:strategic_advertiser,seller-agent/agency:preferred_agency"
SCOPE_TIER_MAP_ENV = "SCOPE_TIER_MAP"

# Req 13: the custom header AgentCore forwards to the runtime carrying the
# Cognito-verified buyer tier. A runtime fronted by a CUSTOM_JWT authorizer
# strips the inbound Authorization bearer at the edge (verified live 2026-09-19)
# and forwards ONLY headers on its request-header allowlist. deploy.sh
# allowlists this header, so on the managed-authorizer path this is the tier
# source; the raw-JWT path below remains for forwarding transports.
#
# The header VALUE is one of:
#   - a Cognito scope string  (e.g. "seller-agent/agency")  → SCOPE_TIER_MAP
#   - a bare tier name         (e.g. "preferred_agency" or "agency")
# Either way the edge already verified the scope the buyer's client was granted,
# so a forged header cannot escalate beyond what the authorizer admitted; the
# registry ceiling still caps it downstream.
TIER_HEADER_NAME = "x-amzn-bedrock-agentcore-runtime-custom-tier"

# Short tier-scope aliases → canonical tier (so a header of "agency" or the
# scope suffix "agency" both resolve without needing SCOPE_TIER_MAP configured).
_SCOPE_SUFFIX_TIER = {
    "seat": "registered_buyer",
    "agency": "preferred_agency",
    "advertiser": "strategic_advertiser",
}

# Tier ranking so a scope/client match resolves deterministically to the highest.
_TIER_RANK = {
    "public": 0,
    "registered_buyer": 1,
    "preferred_agency": 2,
    "strategic_advertiser": 3,
}


def _parse_map(raw: Optional[str]) -> dict[str, str]:
    """Parse a ``key:tier,key2:tier2`` env string into a dict. Tolerant of junk."""
    out: dict[str, str] = {}
    if not raw:
        return out
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, _, tier = pair.partition(":")
        key = key.strip()
        tier = tier.strip().lower()
        if key and tier in _TIER_RANK:
            out[key] = tier
    return out


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Unverified decode of a JWT's payload segment.

    Safe here: the AgentCore authorizer has already verified the token at the
    edge. We only read claims for tier mapping, never for authentication.
    Returns ``{}`` on any malformed input rather than raising.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        # JWT uses URL-safe base64 without padding — restore it.
        padding = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _extract_bearer_token(request_headers: Optional[dict[str, str]]) -> Optional[str]:
    """Pull the bearer token from the (case-insensitive) Authorization header."""
    if not request_headers:
        return None
    for name, value in request_headers.items():
        if name.lower() == "authorization" and isinstance(value, str):
            prefix = "bearer "
            if value.lower().startswith(prefix):
                return value[len(prefix):].strip()
    return None


def _resolve_scope_or_tier(token: str, scope_map: dict[str, str]) -> Optional[str]:
    """Resolve a single scope/tier token string to a canonical tier, or None.

    Accepts (in priority order):
      1. an exact SCOPE_TIER_MAP key (a full scope string the operator mapped),
      2. a canonical tier name already (public/registered_buyer/…),
      3. a known scope suffix alias (seat/agency/advertiser),
      4. the suffix of a "resource/scope" string via (2)/(3).
    """
    token = token.strip()
    if not token:
        return None
    if token in scope_map:
        return scope_map[token]
    low = token.lower()
    if low in _TIER_RANK:
        return low
    if low in _SCOPE_SUFFIX_TIER:
        return _SCOPE_SUFFIX_TIER[low]
    # "seller-agent/agency" → suffix "agency"
    suffix = low.rsplit("/", 1)[-1]
    if suffix in _TIER_RANK:
        return suffix
    if suffix in _SCOPE_SUFFIX_TIER:
        return _SCOPE_SUFFIX_TIER[suffix]
    return None


def _tier_from_header(
    request_headers: Optional[dict[str, str]], scope_map: dict[str, str]
) -> Optional[str]:
    """Derive the tier from the forwarded Cognito-verified tier header (Req 13).

    The header value may carry one or more space/comma-separated scope or tier
    tokens; the HIGHEST-ranked resolved tier wins. Returns None when the header
    is absent or none of its tokens resolve.
    """
    if not request_headers:
        return None
    raw: Optional[str] = None
    for name, value in request_headers.items():
        if name.lower() == TIER_HEADER_NAME and isinstance(value, str):
            raw = value
            break
    if not raw:
        return None
    candidates: list[str] = []
    for part in raw.replace(",", " ").split():
        resolved = _resolve_scope_or_tier(part, scope_map)
        if resolved:
            candidates.append(resolved)
    if not candidates:
        return None
    best = max(candidates, key=lambda t: _TIER_RANK.get(t, 0))
    logger.info("claim→tier: derived %r from forwarded tier header %r", best, raw)
    return best


def tier_from_context(context: Any) -> Optional[str]:
    """Derive a claimed tier from the request's verified tier signal, or None.

    ``context`` is the SDK ``RequestContext`` passed to the entrypoint. Two
    sources, in priority order:

      1. The forwarded, edge-verified tier header
         (``X-Amzn-Bedrock-AgentCore-Runtime-Custom-Tier``) — the source on the
         managed CUSTOM_JWT-authorizer path, where the raw bearer is stripped
         (Req 13, live-verified 2026-09-19). deploy.sh allowlists this header.
      2. The raw ``Authorization`` bearer's ``client_id``/``scope`` claims — the
         source on a transport that FORWARDS the raw JWT (a direct HTTPS/MCP
         client, or a BYO front door). Kept for those paths.

    Returns a tier string (a key of http_main._TIER_MAP) when either source
    resolves, else ``None`` (caller falls back to the payload buyer_tier).
    """
    client_map = _parse_map(os.environ.get(CLAIM_TIER_MAP_ENV))
    scope_map = _parse_map(os.environ.get(SCOPE_TIER_MAP_ENV))

    request_headers = getattr(context, "request_headers", None)

    # Source 1: the forwarded, edge-verified tier header. This works with NO
    # env mapping configured (the header itself carries a scope/tier the edge
    # already verified), which is the common managed-authorizer case.
    header_tier = _tier_from_header(request_headers, scope_map)
    if header_tier:
        return header_tier

    # Source 2: the raw JWT (forwarding transports only). Requires a mapping.
    if not client_map and not scope_map:
        # No mapping configured and no tier header — seam inert.
        return None

    token = _extract_bearer_token(request_headers)
    if not token:
        # NOTE (verified live on AgentCore, 2026-09-19): a runtime fronted by a
        # CUSTOM_JWT authorizer does NOT forward the buyer's inbound
        # Authorization bearer to the entrypoint — the authorizer validates and
        # strips it at the edge, forwarding only an opaque WorkloadAccessToken.
        # On that path the tier arrives via the forwarded header (Source 1) which
        # is checked first; this bearer path fires only on a transport that
        # forwards the raw JWT. See spec Req 13 / task group 11.
        logger.debug("claim→tier: no tier header and no forwarded bearer — payload tier")
        return None

    claims = _decode_jwt_claims(token)
    if not claims:
        return None

    candidates: list[str] = []

    # client_id (Cognito client_credentials tokens carry client_id, not aud).
    client_id = claims.get("client_id") or claims.get("cid")
    if isinstance(client_id, str) and client_id in client_map:
        candidates.append(client_map[client_id])

    # scope: space-separated string per RFC 6749; check each against the map.
    scope_val = claims.get("scope") or claims.get("scp")
    scopes: list[str] = []
    if isinstance(scope_val, str):
        scopes = scope_val.split()
    elif isinstance(scope_val, list):
        scopes = [str(s) for s in scope_val]
    for s in scopes:
        if s in scope_map:
            candidates.append(scope_map[s])

    if not candidates:
        return None

    # Highest-ranked matching tier wins.
    best = max(candidates, key=lambda t: _TIER_RANK.get(t, 0))
    logger.info(
        "claim→tier: derived %r from JWT (client_id=%s, scopes=%s)",
        best,
        client_id,
        ",".join(scopes) if scopes else "-",
    )
    return best
