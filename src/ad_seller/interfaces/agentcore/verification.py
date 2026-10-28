# Donated to IAB Tech Lab

"""Buyer-trust verification core for the AgentCore surfaces (Req 8).

Scope: this module lives INSIDE the agentcore boundary and is the ONE
implementation of the verify → cap → floor → persist → block logic reused
across the AgentCore surfaces so we do not re-code it per runtime:

- The AgentCore HTTP/A2A entrypoint (``http_main`` → a2a via ``_handle_invocation``)
  imports ``verify_buyer_context`` directly.
- The AgentCore MCP runtime enforces it at the transport boundary via the
  ``mcp_main`` call-tool wrapper (in-boundary), so verification holds even if the
  optional in-tool edit (``mcp_server.get_pricing``, a separate cherry-pickable
  commit) is not present.

BOUNDARY NOTE (bkrishnr tenet 2026-09-16): we deliberately DO NOT refactor the
existing FastAPI ``deps._verified_buyer_context`` to consume this core — that
would edit shared external surface. The FastAPI path keeps its own (equivalent)
implementation; consolidating the two is left to the core maintainers. This
module only READS the existing ``deps._get_registry_service`` factory (a
read, not an edit) so config selection + existing tests keep one seam.

The core is transport-neutral: it raises a plain :class:`BlockedAgentError`
rather than a web-framework exception, so each adapter maps it to its own error
shape. Self-asserted identity can never raise the tier above what the seller can
verify (fail-closed PUBLIC floor).
"""

from __future__ import annotations

from typing import Any, Optional


class BlockedAgentError(Exception):
    """Raised when a blocked buyer agent is rejected (adapters map to 403)."""

    def __init__(self, agent_url: str | None = None) -> None:
        super().__init__(f"Agent is blocked: {agent_url or '(unknown)'}")
        self.agent_url = agent_url


def build_buyer_context(
    *,
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    seat_id: Optional[str] = None,
    api_key_record: Optional[Any] = None,
    agent_url: Optional[str] = None,
    max_access_tier: Optional[Any] = None,
):
    """Build a BuyerContext, preferring API-key identity over body params.

    ``max_access_tier`` is the verified ceiling merged in by
    :func:`verify_buyer_context`.
    """
    from ...models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity

    if api_key_record is not None:
        return BuyerContext(
            identity=api_key_record.identity,
            is_authenticated=True,
            authentication_method="api_key",
            agent_url=agent_url,
            max_access_tier=max_access_tier,
        )

    tier_map = {
        "public": AccessTier.PUBLIC,
        "seat": AccessTier.SEAT,
        "agency": AccessTier.AGENCY,
        "advertiser": AccessTier.ADVERTISER,
    }
    access_tier = tier_map.get((buyer_tier or "public").lower(), AccessTier.PUBLIC)
    identity = BuyerIdentity(seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id)
    return BuyerContext(
        identity=identity,
        is_authenticated=access_tier != AccessTier.PUBLIC,
        agent_url=agent_url,
        max_access_tier=max_access_tier,
    )


async def verify_buyer_context(
    *,
    endpoint: str,
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    seat_id: Optional[str] = None,
    api_key_record: Optional[Any] = None,
    agent_url: Optional[str] = None,
):
    """Build a BuyerContext with the trust-tier ceiling VERIFIED, fail-closed.

    - ``agent_url`` present → verify against the registry; the ceiling caps the
      claimed tier, unknown/unverifiable agents get the PUBLIC floor, and BLOCKED
      agents raise :class:`BlockedAgentError`. Each outcome is persisted as a
      ``VerifiedTrust`` record.
    - API key, no ``agent_url`` → the key's identity is the verified principal.
    - Neither → the claim is unverifiable; the effective tier is floored to PUBLIC.

    Args:
        endpoint: Audit label for the calling surface (persisted on the
            ``VerifiedTrust`` record; e.g. "agentcore:invoke", "mcp:get_pricing").
            Non-functional — it does not affect the tier decision.
    """
    from ...models.buyer_identity import AccessTier
    from ...storage.factory import get_storage
    from ...storage.trust_verifications import TrustVerificationStore

    ceiling: Optional[Any] = None

    if agent_url:
        # READ (not edit) the existing registry-service factory so config
        # selection + every existing test's mock keep working through one seam.
        from ..api.deps import _get_registry_service

        service = await _get_registry_service()
        agent, tier, verdict = await service.verify_buyer_trust(agent_url)

        storage = await get_storage()
        claimed = build_buyer_context(
            buyer_tier=buyer_tier,
            agency_id=agency_id,
            advertiser_id=advertiser_id,
            seat_id=seat_id,
            api_key_record=api_key_record,
            agent_url=agent_url,
        )
        store = TrustVerificationStore(storage)
        await store.record_verification(
            verdict,
            agent_url=agent_url,
            claimed_tier=claimed.effective_tier.value,
            effective_ceiling=tier.value if tier is not None else None,
            endpoint=endpoint,
        )

        if agent is not None and getattr(agent, "is_blocked", False):
            raise BlockedAgentError(agent_url)

        ceiling = tier if tier is not None else AccessTier.PUBLIC
    elif api_key_record is None:
        ceiling = AccessTier.PUBLIC

    return build_buyer_context(
        buyer_tier=buyer_tier,
        agency_id=agency_id,
        advertiser_id=advertiser_id,
        seat_id=seat_id,
        api_key_record=api_key_record,
        agent_url=agent_url,
        max_access_tier=ceiling,
    )
