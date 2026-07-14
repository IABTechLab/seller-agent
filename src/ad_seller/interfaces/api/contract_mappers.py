# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Anti-corruption boundary mappers: shared contract <-> seller internal.

EP-12.2: the seller now speaks the shared ``iab-agentic-primitives``
contract AT THE WIRE EDGE only. The REST request/response boundary
(``interfaces/api/routers/*``) parses and builds the shared protocol
types; everything below the router — services, engines, storage — keeps
the seller's internal models untouched. These mappers are the ONE place
the two shapes meet, so the internal models never leak onto the wire and
the shared types never leak into the service layer.

Money (flagged decision FD-11): the internal models price in ``float``
dollars; the shared contract prices in :class:`Money` (integer micros).
Conversion happens here and nowhere else.
"""

from datetime import date, datetime
from typing import Any, Optional

from iab_agentic_primitives.primitives import (
    AccessTier,
    DealType,
    MediaType,
    Money,
    PricingModel,
    PricingType,
    ProductRef,
    Quote,
    QuoteAvailability,
    QuotePricing,
    QuoteStatus,
    QuoteTerms,
)
from iab_agentic_primitives.protocol import QuoteRequest, QuoteResponse
from iab_agentic_primitives.protocol.errors import ErrorCode, ErrorDetail

# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------


def float_to_money(amount: Optional[float], currency: str = "USD") -> Optional[Money]:
    """Convert internal float dollars to shared :class:`Money` (micros)."""
    if amount is None:
        return None
    return Money(amount_micros=round(float(amount) * 1_000_000), currency=currency)


def money_to_float(amount: Optional[Money]) -> Optional[float]:
    """Convert shared :class:`Money` (micros) back to internal float dollars."""
    if amount is None:
        return None
    return amount.amount_micros / 1_000_000


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _parse_date(value: Any) -> Optional[date]:
    """Parse a ``YYYY-MM-DD`` date string."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# ---------------------------------------------------------------------------
# Structured error envelope (FD-6)
# ---------------------------------------------------------------------------


def unsupported_capability_detail(
    capabilities: list[dict[str, Any]] | list[str],
    message: str = "",
) -> dict[str, Any]:
    """Build the ``{"error": ..., "unsupported": [...]}`` inner detail.

    Returned as the ``detail`` of a FastAPI ``HTTPException`` so the wire
    shape is the shared :class:`ErrorEnvelope`
    (``{"detail": {"error": "unsupported_capability", "unsupported": [...]}}``).
    """
    detail = ErrorDetail(
        error=ErrorCode.UNSUPPORTED_CAPABILITY,
        message=message,
        unsupported=capabilities,
    )
    return detail.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Quote surface
# ---------------------------------------------------------------------------

#: Media types the seller can price. ``linear_tv`` is deliberately absent —
#: it is rejected structurally (FD-6) rather than silently mispriced.
SUPPORTED_MEDIA_TYPES: frozenset[MediaType] = frozenset(
    {MediaType.DIGITAL, MediaType.CTV}
)


def quote_request_to_internal(req: QuoteRequest) -> Any:
    """Map the shared :class:`QuoteRequest` to the seller's internal model.

    The internal :class:`QuoteRequestModel` is the shape the untouched
    ``quote_service.create_quote`` expects: string ``deal_type``, string
    dates, and float ``target_cpm``.
    """
    from .schemas import QuoteBuyerIdentityModel, QuoteRequestModel

    buyer_identity = None
    if req.buyer_identity is not None:
        buyer_identity = QuoteBuyerIdentityModel(
            seat_id=req.buyer_identity.seat_id,
            agency_id=req.buyer_identity.agency_id,
            advertiser_id=req.buyer_identity.advertiser_id,
            dsp_platform=req.buyer_identity.dsp_platform,
        )

    return QuoteRequestModel(
        product_id=req.product_id,
        deal_type=req.deal_type.value,
        impressions=req.impressions,
        flight_start=req.flight_start.isoformat() if req.flight_start else None,
        flight_end=req.flight_end.isoformat() if req.flight_end else None,
        target_cpm=money_to_float(req.target_cpm),
        buyer_identity=buyer_identity,
    )


def _quote_pricing_to_shared(pricing: dict[str, Any]) -> QuotePricing:
    currency = pricing.get("currency", "USD")
    return QuotePricing(
        pricing_type=PricingType(pricing.get("pricing_type", "fixed")),
        base_cpm=float_to_money(pricing.get("base_cpm"), currency),
        tier_discount_pct=pricing.get("tier_discount_pct", 0.0),
        volume_discount_pct=pricing.get("volume_discount_pct", 0.0),
        final_cpm=float_to_money(pricing.get("final_cpm"), currency),
        pricing_model=PricingModel(pricing.get("pricing_model", "cpm")),
        rationale=pricing.get("rationale", ""),
    )


def _quote_terms_to_shared(terms: dict[str, Any]) -> QuoteTerms:
    return QuoteTerms(
        impressions=terms.get("impressions"),
        flight_start=_parse_date(terms.get("flight_start")),
        flight_end=_parse_date(terms.get("flight_end")),
        guaranteed=terms.get("guaranteed", False),
    )


def internal_quote_to_shared_quote(
    data: dict[str, Any],
    media_type: MediaType = MediaType.DIGITAL,
) -> Quote:
    """Build the shared :class:`Quote` primitive from the internal quote dict."""
    product = data.get("product", {})
    availability = data.get("availability")
    return Quote(
        quote_id=data["quote_id"],
        status=QuoteStatus(data.get("status", "available")),
        deal_type=DealType(data["deal_type"]),
        product=ProductRef(
            product_id=product.get("product_id", ""),
            name=product.get("name", ""),
            inventory_type=product.get("inventory_type"),
        ),
        pricing=_quote_pricing_to_shared(data.get("pricing", {})),
        terms=_quote_terms_to_shared(data.get("terms", {})),
        availability=(
            QuoteAvailability(
                inventory_available=availability.get("inventory_available", True),
                estimated_fill_rate=availability.get("estimated_fill_rate"),
                competing_demand=availability.get("competing_demand"),
            )
            if isinstance(availability, dict)
            else None
        ),
        buyer_tier=AccessTier(data.get("buyer_tier", "public")),
        expires_at=_parse_dt(data.get("expires_at")),
        seller_id=data.get("seller_id"),
        created_at=_parse_dt(data.get("created_at")) or datetime.now(),
        deal_id=data.get("deal_id"),
        media_type=media_type,
    )


def internal_quote_to_response(
    data: dict[str, Any],
    media_type: MediaType = MediaType.DIGITAL,
) -> QuoteResponse:
    """Wrap the internal quote dict in the shared :class:`QuoteResponse`."""
    return QuoteResponse(quote=internal_quote_to_shared_quote(data, media_type))
