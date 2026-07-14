# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quote lifecycle service (IAB Deals API v1.0).

Extracted from ``interfaces/api/main.py`` (EP-3.1): quote creation,
lazy expiry, and status handling. Behavior-preserving — error semantics
are expressed as ``HTTPException`` exactly as the endpoints raised them.

Imports of storage/flows/engines stay function-level so existing test
patch points (e.g. ``ad_seller.storage.factory.get_storage``) keep
working, mirroring the original endpoint bodies.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Deal type wire values accepted by the Deals API.
DEAL_TYPE_MAP_KEYS = ("PG", "PD", "PA")


def _deal_type_map():
    from ..models.core import DealType

    return {
        "PG": DealType.PROGRAMMATIC_GUARANTEED,
        "PD": DealType.PREFERRED_DEAL,
        "PA": DealType.PRIVATE_AUCTION,
    }


def get_pricing(
    product_id: str,
    product: Any,
    buyer_context: Any,
    volume: int = 0,
) -> dict[str, Any]:
    """Calculate tier/volume-adjusted pricing for a product."""
    from ..engines.pricing_rules_engine import PricingRulesEngine
    from ..models.pricing_tiers import TieredPricingConfig

    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    decision = engine.calculate_price(
        product_id=product_id,
        base_price=product.base_cpm,
        buyer_context=buyer_context,
        volume=volume,
    )

    return {
        "product_id": product_id,
        "base_price": decision.base_price,
        "final_price": decision.final_price,
        "currency": decision.currency,
        "tier_discount": decision.tier_discount,
        "volume_discount": decision.volume_discount,
        "rationale": decision.rationale,
    }


async def create_quote(
    request: Any,
    buyer_context: Any,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Create a non-binding price quote with a 24-hour TTL.

    Args:
        request: ``QuoteRequestModel`` payload.
        buyer_context: resolved ``BuyerContext`` (API key beats body params).
        catalog: static product catalog dict (``{"products": {...}, ...}``).
    """
    from ..engines.pricing_rules_engine import PricingRulesEngine
    from ..models.pricing_tiers import TieredPricingConfig
    from ..models.quotes import (
        QuoteAvailability,
        QuotePricing,
        QuoteProductInfo,
        QuoteResponse,
        QuoteStatus,
        QuoteTerms,
    )
    from ..storage.factory import get_storage

    deal_type_map = _deal_type_map()
    deal_type_str = request.deal_type.upper()
    if deal_type_str not in deal_type_map:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_deal_type",
                "message": f"Deal type must be one of: PG, PD, PA. Got: {request.deal_type}",
            },
        )

    # PG deals require impressions
    if deal_type_str == "PG" and not request.impressions:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "pg_requires_impressions",
                "message": "Programmatic Guaranteed deals require an impressions count.",
            },
        )

    product = catalog["products"].get(request.product_id)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "product_not_found",
                "message": f"Product '{request.product_id}' not found in catalog.",
            },
        )

    # Validate minimum impressions
    if request.impressions and request.impressions < product.minimum_impressions:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "below_minimum_impressions",
                "message": f"Minimum impressions for this product: {product.minimum_impressions}.",
            },
        )

    # Calculate price via PricingRulesEngine
    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    deal_type_enum = deal_type_map[deal_type_str]
    decision = engine.calculate_price(
        product_id=request.product_id,
        base_price=product.base_cpm,
        buyer_context=buyer_context,
        deal_type=deal_type_enum,
        volume=request.impressions or 0,
        inventory_type=product.inventory_type,
    )

    # Evaluate target_cpm if provided
    final_cpm = decision.final_price
    if request.target_cpm is not None:
        acceptable, _ = engine.is_price_acceptable(
            offered_price=request.target_cpm,
            product_floor=product.floor_cpm,
            buyer_context=buyer_context,
        )
        if acceptable:
            final_cpm = request.target_cpm

    # Build timestamps
    now = datetime.utcnow()
    expires_at = now + timedelta(hours=24)

    # Default flight dates
    flight_start = request.flight_start or now.strftime("%Y-%m-%d")
    flight_end = request.flight_end or (now + timedelta(days=30)).strftime("%Y-%m-%d")

    # Generate quote
    quote_id = f"qt-{uuid.uuid4().hex[:12]}"
    is_guaranteed = deal_type_str == "PG"

    quote = QuoteResponse(
        quote_id=quote_id,
        status=QuoteStatus.AVAILABLE,
        product=QuoteProductInfo(
            product_id=product.product_id,
            name=product.name,
            inventory_type=product.inventory_type,
        ),
        pricing=QuotePricing(
            base_cpm=decision.base_price,
            tier_discount_pct=round(decision.tier_discount * 100, 1),
            volume_discount_pct=round(decision.volume_discount * 100, 1),
            final_cpm=final_cpm,
            currency=decision.currency,
            pricing_model=decision.pricing_model.value,
            rationale=decision.rationale,
        ),
        terms=QuoteTerms(
            impressions=request.impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            guaranteed=is_guaranteed,
        ),
        availability=QuoteAvailability(),
        deal_type=deal_type_str,
        buyer_tier=buyer_context.effective_tier.value,
        expires_at=expires_at.isoformat() + "Z",
        created_at=now.isoformat() + "Z",
    )

    # Persist with 24-hour TTL
    storage = await get_storage()
    await storage.set_quote(quote_id, quote.model_dump(mode="json"), ttl=86400)

    # Record in quote history for pricing verification (Layer 4)
    from ..storage.quote_history import QuoteHistoryStore

    quote_history = QuoteHistoryStore(storage)
    await quote_history.record_quote(
        quote_id=quote_id,
        buyer_id=buyer_context.get_pricing_key(),
        product_id=request.product_id,
        quoted_cpm=final_cpm,
        expires_at=expires_at,
    )

    return quote.model_dump(mode="json")


async def get_quote(quote_id: str) -> dict[str, Any]:
    """Retrieve a previously issued quote. Raises 410 Gone if expired."""
    from ..models.quotes import QuoteStatus
    from ..storage.factory import get_storage

    storage = await get_storage()
    quote = await storage.get_quote(quote_id)

    if not quote:
        raise HTTPException(
            status_code=404,
            detail={"error": "quote_not_found", "message": f"Quote '{quote_id}' not found."},
        )

    # Lazy expiry check
    if quote.get("expires_at"):
        expires = datetime.fromisoformat(quote["expires_at"].rstrip("Z"))
        if datetime.utcnow() > expires:
            quote["status"] = QuoteStatus.EXPIRED.value
            await storage.set_quote(quote_id, quote, ttl=3600)  # Keep expired record briefly
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "quote_expired",
                    "message": "Quote has expired. Request a new quote.",
                },
            )

    return quote
