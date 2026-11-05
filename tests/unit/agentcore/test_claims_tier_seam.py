"""Unit tests for the claim → tier seam (spec task 5.2).

Offline: exercises the JWT-payload decode + client_id/scope → tier mapping and
its inert/fallback behavior. No AWS, no real token verification (the AgentCore
edge owns that; this seam only reads claims for tier mapping).
"""

import base64
import json

from ad_seller.interfaces.agentcore import claims


def _make_jwt(payload: dict) -> str:
    """Build a syntactically valid unsigned JWT with the given payload."""

    def _b64(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = _b64({"alg": "RS256", "typ": "JWT"})
    body = _b64(payload)
    return f"{header}.{body}.signature-not-checked"


class _Ctx:
    """Minimal RequestContext stand-in exposing request_headers."""

    def __init__(self, headers):
        self.request_headers = headers


# ---------------------------------------------------------------------------
# _parse_map
# ---------------------------------------------------------------------------
def test_parse_map_valid_pairs():
    m = claims._parse_map("clientA:strategic_advertiser,clientB:preferred_agency")
    assert m == {
        "clientA": "strategic_advertiser",
        "clientB": "preferred_agency",
    }


def test_parse_map_drops_unknown_tier_and_junk():
    m = claims._parse_map("clientA:not_a_tier,garbage,clientB:public")
    assert m == {"clientB": "public"}


def test_parse_map_empty():
    assert claims._parse_map(None) == {}
    assert claims._parse_map("") == {}


# ---------------------------------------------------------------------------
# _decode_jwt_claims
# ---------------------------------------------------------------------------
def test_decode_jwt_claims_reads_payload():
    tok = _make_jwt({"client_id": "abc", "scope": "seller-agent/invoke"})
    out = claims._decode_jwt_claims(tok)
    assert out["client_id"] == "abc"
    assert out["scope"] == "seller-agent/invoke"


def test_decode_jwt_claims_malformed_returns_empty():
    assert claims._decode_jwt_claims("not-a-jwt") == {}
    assert claims._decode_jwt_claims("a.b") == {}
    assert claims._decode_jwt_claims("a.!!!.c") == {}


# ---------------------------------------------------------------------------
# _extract_bearer_token
# ---------------------------------------------------------------------------
def test_extract_bearer_token_case_insensitive():
    assert claims._extract_bearer_token({"Authorization": "Bearer xyz"}) == "xyz"
    assert claims._extract_bearer_token({"authorization": "bearer xyz"}) == "xyz"


def test_extract_bearer_token_absent():
    assert claims._extract_bearer_token(None) is None
    assert claims._extract_bearer_token({"X-Foo": "bar"}) is None
    assert claims._extract_bearer_token({"Authorization": "Basic xyz"}) is None


# ---------------------------------------------------------------------------
# tier_from_context — the seam
# ---------------------------------------------------------------------------
def test_seam_inert_when_no_mapping_configured(monkeypatch):
    monkeypatch.delenv(claims.CLAIM_TIER_MAP_ENV, raising=False)
    monkeypatch.delenv(claims.SCOPE_TIER_MAP_ENV, raising=False)
    tok = _make_jwt({"client_id": "abc"})
    ctx = _Ctx({"Authorization": f"Bearer {tok}"})
    assert claims.tier_from_context(ctx) is None


def test_seam_maps_client_id_to_tier(monkeypatch):
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "abc:strategic_advertiser")
    monkeypatch.delenv(claims.SCOPE_TIER_MAP_ENV, raising=False)
    tok = _make_jwt({"client_id": "abc", "scope": "seller-agent/invoke"})
    ctx = _Ctx({"Authorization": f"Bearer {tok}"})
    assert claims.tier_from_context(ctx) == "strategic_advertiser"


def test_seam_maps_scope_to_tier(monkeypatch):
    monkeypatch.delenv(claims.CLAIM_TIER_MAP_ENV, raising=False)
    monkeypatch.setenv(
        claims.SCOPE_TIER_MAP_ENV, "seller-agent/agency:preferred_agency"
    )
    tok = _make_jwt({"client_id": "abc", "scope": "openid seller-agent/agency"})
    ctx = _Ctx({"Authorization": f"Bearer {tok}"})
    assert claims.tier_from_context(ctx) == "preferred_agency"


def test_seam_highest_tier_wins(monkeypatch):
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "abc:preferred_agency")
    monkeypatch.setenv(
        claims.SCOPE_TIER_MAP_ENV, "seller-agent/adv:strategic_advertiser"
    )
    tok = _make_jwt({"client_id": "abc", "scope": "seller-agent/adv"})
    ctx = _Ctx({"Authorization": f"Bearer {tok}"})
    # client_id maps to preferred_agency (rank 2), scope to strategic (rank 3) → strategic wins
    assert claims.tier_from_context(ctx) == "strategic_advertiser"


def test_seam_returns_none_when_no_token(monkeypatch):
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "abc:strategic_advertiser")
    ctx = _Ctx({"X-Foo": "bar"})
    assert claims.tier_from_context(ctx) is None


def test_seam_returns_none_when_claim_unmapped(monkeypatch):
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "known:strategic_advertiser")
    tok = _make_jwt({"client_id": "some-other-client"})
    ctx = _Ctx({"Authorization": f"Bearer {tok}"})
    assert claims.tier_from_context(ctx) is None


def test_seam_handles_scope_as_list(monkeypatch):
    monkeypatch.setenv(
        claims.SCOPE_TIER_MAP_ENV, "seller-agent/agency:preferred_agency"
    )
    tok = _make_jwt({"scope": ["openid", "seller-agent/agency"]})
    ctx = _Ctx({"Authorization": f"Bearer {tok}"})
    assert claims.tier_from_context(ctx) == "preferred_agency"


def test_seam_reads_cid_and_scp_aliases(monkeypatch):
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "aliased:registered_buyer")
    tok = _make_jwt({"cid": "aliased"})
    ctx = _Ctx({"Authorization": f"Bearer {tok}"})
    assert claims.tier_from_context(ctx) == "registered_buyer"


def test_seam_none_context_safe(monkeypatch):
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "abc:strategic_advertiser")
    # getattr(None, "request_headers", None) → None → no token → None
    assert claims.tier_from_context(None) is None


# ---------------------------------------------------------------------------
# Req 13: forwarded tier header (the managed CUSTOM_JWT-authorizer path)
# ---------------------------------------------------------------------------
def test_resolve_scope_or_tier_forms():
    m = {"seller-agent/agency": "preferred_agency"}
    # exact SCOPE_TIER_MAP key
    assert claims._resolve_scope_or_tier("seller-agent/agency", m) == "preferred_agency"
    # canonical tier name
    assert claims._resolve_scope_or_tier("registered_buyer", {}) == "registered_buyer"
    # bare scope suffix alias
    assert claims._resolve_scope_or_tier("advertiser", {}) == "strategic_advertiser"
    # resource/scope suffix without a map
    assert claims._resolve_scope_or_tier("seller-agent/seat", {}) == "registered_buyer"
    # unknown → None
    assert claims._resolve_scope_or_tier("nonsense", {}) is None


def test_header_tier_wins_with_no_mapping(monkeypatch):
    # No env mapping at all — the forwarded header alone drives the tier.
    monkeypatch.delenv(claims.CLAIM_TIER_MAP_ENV, raising=False)
    monkeypatch.delenv(claims.SCOPE_TIER_MAP_ENV, raising=False)
    ctx = _Ctx({"X-Amzn-Bedrock-AgentCore-Runtime-Custom-Tier": "seller-agent/agency"})
    assert claims.tier_from_context(ctx) == "preferred_agency"


def test_header_tier_bare_name(monkeypatch):
    monkeypatch.delenv(claims.CLAIM_TIER_MAP_ENV, raising=False)
    monkeypatch.delenv(claims.SCOPE_TIER_MAP_ENV, raising=False)
    ctx = _Ctx({"x-amzn-bedrock-agentcore-runtime-custom-tier": "advertiser"})
    assert claims.tier_from_context(ctx) == "strategic_advertiser"


def test_header_tier_highest_of_multiple(monkeypatch):
    monkeypatch.delenv(claims.CLAIM_TIER_MAP_ENV, raising=False)
    monkeypatch.delenv(claims.SCOPE_TIER_MAP_ENV, raising=False)
    ctx = _Ctx({"X-Amzn-Bedrock-AgentCore-Runtime-Custom-Tier": "invoke agency advertiser"})
    assert claims.tier_from_context(ctx) == "strategic_advertiser"


def test_header_takes_priority_over_bearer(monkeypatch):
    # Header says agency; the bearer's client_id maps to registered_buyer.
    # The forwarded, edge-verified header must win.
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "abc:registered_buyer")
    tok = _make_jwt({"client_id": "abc"})
    ctx = _Ctx(
        {
            "Authorization": f"Bearer {tok}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Tier": "agency",
        }
    )
    assert claims.tier_from_context(ctx) == "preferred_agency"


def test_header_unresolvable_falls_through_to_bearer(monkeypatch):
    monkeypatch.setenv(claims.CLAIM_TIER_MAP_ENV, "abc:registered_buyer")
    tok = _make_jwt({"client_id": "abc"})
    ctx = _Ctx(
        {
            "Authorization": f"Bearer {tok}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Tier": "garbage-scope",
        }
    )
    # Header token doesn't resolve → fall through to the bearer client_id map.
    assert claims.tier_from_context(ctx) == "registered_buyer"


def test_header_absent_and_no_mapping_is_inert(monkeypatch):
    monkeypatch.delenv(claims.CLAIM_TIER_MAP_ENV, raising=False)
    monkeypatch.delenv(claims.SCOPE_TIER_MAP_ENV, raising=False)
    ctx = _Ctx({"baggage": "x", "workloadaccesstoken": "opaque"})
    assert claims.tier_from_context(ctx) is None
