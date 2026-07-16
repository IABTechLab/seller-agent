# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Shared FastAPI dependencies and request-context helpers.

Extracted from ``interfaces/api/main.py`` (EP-3.1). ``main`` re-exports
``_get_optional_api_key_record`` so existing test dependency-overrides
keep working — it is the SAME function object either way.
"""

import logging
from typing import Any, Optional

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


async def _get_optional_api_key_record(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
):
    """FastAPI dependency: validate API key from headers if present.

    Returns None for anonymous requests (no key in headers).
    Raises HTTPException(401) for invalid, revoked, or expired keys.
    Accepts ``Authorization: Bearer <key>`` or ``X-Api-Key: <key>``.

    The ``Header(...)`` defaults mirror ``ad_seller.auth.dependencies.
    get_api_key_record`` — without them FastAPI binds these parameters
    as *query* parameters, real credential headers never reach the
    validator, and every buyer silently falls through to anonymous
    PUBLIC-tier access (while invalid/revoked keys are never rejected).
    """
    from ...auth.dependencies import get_api_key_record

    return await get_api_key_record(authorization, x_api_key)


def _build_buyer_context(
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    seat_id: Optional[str] = None,
    api_key_record: Optional[Any] = None,
    agent_url: Optional[str] = None,
    max_access_tier: Optional[Any] = None,
):
    """Build a BuyerContext, preferring API key identity over body params.

    If an api_key_record is present, the key's identity is used and the
    buyer is marked as authenticated. Otherwise, falls back to body/query
    params (backward compatible with pre-auth behavior).

    The max_access_tier (from agent registry) is merged in when provided.
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

    # Fallback: body params (existing behavior, backward compatible)
    tier_map = {
        "public": AccessTier.PUBLIC,
        "seat": AccessTier.SEAT,
        "agency": AccessTier.AGENCY,
        "advertiser": AccessTier.ADVERTISER,
    }
    access_tier = tier_map.get(buyer_tier.lower(), AccessTier.PUBLIC)
    identity = BuyerIdentity(
        seat_id=seat_id,
        agency_id=agency_id,
        advertiser_id=advertiser_id,
    )
    return BuyerContext(
        identity=identity,
        is_authenticated=access_tier != AccessTier.PUBLIC,
        agent_url=agent_url,
        max_access_tier=max_access_tier,
    )


async def _get_registry_service():
    """Create an AgentRegistryService with storage + registry client(s).

    Registry client selection is config-driven (EP-5.1): the real IAB
    agent registry (AampApiRegistryClient) when AAMP_REGISTRY_URL is set,
    the legacy stub clients otherwise.
    """
    from ...clients.agent_registry_client import build_registry_clients
    from ...registry import AgentRegistryService
    from ...storage.factory import get_storage

    storage = await get_storage()
    settings = _get_api_settings()
    return AgentRegistryService(
        storage, registry_clients=build_registry_clients(settings)
    )


def _get_api_settings():
    """Get settings for API use."""
    from ...config import get_settings

    return get_settings()


async def _resolve_and_enforce_agent(
    agent_url: Optional[str],
) -> tuple[Optional[Any], Optional[Any]]:
    """Resolve agent and enforce blocked status.

    Returns (RegisteredAgent, AccessTier). Raises HTTPException 403
    if the agent is blocked — zero data leakage.
    """
    if not agent_url:
        return None, None

    service = await _get_registry_service()
    agent, tier = await service.resolve_agent_access(agent_url)

    if agent and agent.is_blocked:
        raise HTTPException(
            status_code=403,
            detail="Agent is blocked. Contact the seller operator for access.",
        )

    return agent, tier


async def _verified_buyer_context(
    *,
    endpoint: str,
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    seat_id: Optional[str] = None,
    api_key_record: Optional[Any] = None,
    agent_url: Optional[str] = None,
):
    """Build a BuyerContext with the trust-tier ceiling VERIFIED (EP-5.2).

    Used by every price-moving path (quotes, pricing, proposals, negotiation
    counters/messages, template booking). The buyer's CLAIMED tier — whether
    self-asserted through body identity fields like ``advertiser_id`` or
    derived from a seller-issued API key — is capped at a verified ceiling,
    fail-closed:

    - ``agent_url`` present: the agent is verified against the registry
      (through AgentRegistryService / the EP-5.1 clients). The registry
      ceiling caps the claimed tier; unknown/unverifiable agents get the
      PUBLIC floor; blocked agents are rejected with 403 before any pricing
      data leaks. Each verification outcome is persisted as the shared
      contract library's ``VerifiedTrust`` primitive (TrustVerificationStore,
      EP-0.2 durable-fallback semantics).
    - API key, no ``agent_url``: the key's identity is the EP-4.5 verified
      principal (seller-issued credential) — trusted as claimed.
    - Neither: the claim is unverifiable — the effective tier is floored to
      PUBLIC. Self-asserted identity can never raise the tier above what
      the seller can verify.

    Args:
        endpoint: Audit label for the calling path (e.g. "POST /api/v1/quotes").
    """
    from ...models.buyer_identity import AccessTier
    from ...storage.factory import get_storage
    from ...storage.trust_verifications import TrustVerificationStore

    ceiling: Optional[Any] = None

    if agent_url:
        service = await _get_registry_service()
        agent, tier, verdict = await service.verify_buyer_trust(agent_url)

        # What the buyer claims, before any ceiling is applied.
        claimed_context = _build_buyer_context(
            buyer_tier=buyer_tier,
            agency_id=agency_id,
            advertiser_id=advertiser_id,
            seat_id=seat_id,
            api_key_record=api_key_record,
            agent_url=agent_url,
        )
        # Persist the auditable verification outcome (fail-closed store).
        store = TrustVerificationStore(await get_storage())
        await store.record_verification(
            verdict,
            agent_url=agent_url,
            claimed_tier=claimed_context.effective_tier.value,
            effective_ceiling=tier.value if tier is not None else None,
            endpoint=endpoint,
        )

        if agent is not None and agent.is_blocked:
            raise HTTPException(
                status_code=403,
                detail="Agent is blocked. Contact the seller operator for access.",
            )
        # Registry-verified ceiling (PUBLIC floor for unknown agents).
        ceiling = tier if tier is not None else AccessTier.PUBLIC
    elif api_key_record is None:
        # No verifiable agent identity and no seller-issued credential:
        # fail closed — self-asserted claims get the floor tier.
        ceiling = AccessTier.PUBLIC

    return _build_buyer_context(
        buyer_tier=buyer_tier,
        agency_id=agency_id,
        advertiser_id=advertiser_id,
        seat_id=seat_id,
        api_key_record=api_key_record,
        agent_url=agent_url,
        max_access_tier=ceiling,
    )


def get_product_catalog() -> dict[str, Any]:
    """Return the cached static product catalog.

    Resolves through ``interfaces.api.main._get_static_product_catalog``
    AT CALL TIME so existing tests that patch that attribute (and reset
    ``main._STATIC_PRODUCT_CATALOG``) keep governing every endpoint.
    The lazy import avoids the main ↔ routers circular import.
    """
    from . import main

    return main._get_static_product_catalog()


async def _get_media_kit_service():
    """Create a MediaKitService with storage and pricing engine."""
    from ...engines.media_kit_service import MediaKitService
    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...models.pricing_tiers import TieredPricingConfig
    from ...storage.factory import get_storage

    storage = await get_storage()
    config = TieredPricingConfig(seller_organization_id="default")
    pricing = PricingRulesEngine(config)
    return MediaKitService(storage, pricing)
