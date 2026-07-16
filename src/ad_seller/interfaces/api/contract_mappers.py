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
    Deal,
    DealStatus,
    DealType,
    DeliveryType,
    MediaType,
    Money,
    NegotiationAction,
    NegotiationRound,
    NegotiationStatus,
    OpenRTBParams,
    PricingModel,
    PricingType,
    Product,
    ProductRef,
    Quote,
    QuoteAvailability,
    QuotePricing,
    QuoteStatus,
    QuoteTerms,
)
from iab_agentic_primitives.protocol import (
    DealBookingRequest,
    DealBookingResponse,
    NegotiationRoundResponse,
    ProductListResponse,
    QuoteRequest,
    QuoteResponse,
)
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


# ---------------------------------------------------------------------------
# Deal booking surface
# ---------------------------------------------------------------------------


def deal_booking_request_to_internal(req: DealBookingRequest) -> Any:
    """Map the shared :class:`DealBookingRequest` to the seller's internal model.

    ``idempotency_key`` and ``consent_context`` are handled at the boundary
    (router) and by the shared model; the internal
    :class:`DealBookingRequestModel` keeps only the fields
    ``deal_service.book_deal`` reads.
    """
    from .schemas import DealBookingRequestModel, QuoteBuyerIdentityModel

    buyer_identity = None
    if req.buyer_identity is not None:
        buyer_identity = QuoteBuyerIdentityModel(
            seat_id=req.buyer_identity.seat_id,
            agency_id=req.buyer_identity.agency_id,
            advertiser_id=req.buyer_identity.advertiser_id,
            dsp_platform=req.buyer_identity.dsp_platform,
        )

    return DealBookingRequestModel(
        quote_id=req.quote_id,
        buyer_identity=buyer_identity,
        notes=req.notes,
        audience_plan=req.audience_plan,
    )


def _openrtb_to_shared(params: dict[str, Any] | None) -> Optional[OpenRTBParams]:
    if not params:
        return None
    return OpenRTBParams(
        id=params.get("id", ""),
        bidfloor=float_to_money(
            params.get("bidfloor"), params.get("bidfloorcur", "USD")
        )
        or Money(amount_micros=0, currency=params.get("bidfloorcur", "USD")),
        at=params.get("at", 3),
        wseat=params.get("wseat", []) or [],
        wadomain=params.get("wadomain", []) or [],
    )


def internal_deal_to_shared_deal(data: dict[str, Any]) -> Deal:
    """Build the shared :class:`Deal` primitive from the internal deal dict."""
    product = data.get("product", {})
    return Deal(
        deal_id=data["deal_id"],
        deal_type=DealType(data["deal_type"]),
        status=DealStatus(data.get("status", "proposed")),
        quote_id=data.get("quote_id"),
        product=ProductRef(
            product_id=product.get("product_id", ""),
            name=product.get("name", ""),
            inventory_type=product.get("inventory_type"),
        ),
        pricing=_quote_pricing_to_shared(data.get("pricing", {})),
        terms=_quote_terms_to_shared(data.get("terms", {})),
        buyer_tier=AccessTier(data.get("buyer_tier", "public")),
        seller_id=data.get("seller_id"),
        expires_at=_parse_dt(data.get("expires_at")),
        activation_instructions=data.get("activation_instructions", {}) or {},
        openrtb_params=_openrtb_to_shared(data.get("openrtb_params")),
        created_at=_parse_dt(data.get("created_at")) or datetime.now(),
    )


def internal_deal_to_response(data: dict[str, Any]) -> DealBookingResponse:
    """Wrap the internal deal dict in the shared :class:`DealBookingResponse`.

    The seller's audience-plan snapshot / match-summary (proposal §5.7) ride
    on the envelope alongside the Deal primitive.
    """
    return DealBookingResponse(
        deal=internal_deal_to_shared_deal(data),
        audience_plan_snapshot=data.get("audience_plan_snapshot"),
        audience_match_summary=data.get("audience_match_summary"),
    )


# ---------------------------------------------------------------------------
# Negotiation surface (the seller side of the 422 fix, FD-5)
# ---------------------------------------------------------------------------


def negotiation_round_to_response(data: dict[str, Any]) -> NegotiationRoundResponse:
    """Map the internal counter-offer result dict to the shared response.

    The internal ``negotiation_service.counter_proposal`` returns float
    prices; the shared :class:`NegotiationRound` prices in :class:`Money`.
    """
    rounds_remaining = data.get("rounds_remaining")
    if rounds_remaining is not None:
        rounds_remaining = max(int(rounds_remaining), 0)
    return NegotiationRoundResponse(
        negotiation_id=data["negotiation_id"],
        status=NegotiationStatus(data.get("status", "active")),
        round=NegotiationRound(
            round_number=data.get("round_number", 1),
            buyer_price=float_to_money(data.get("buyer_price", 0.0))
            or Money(amount_micros=0),
            seller_price=float_to_money(data.get("seller_price", 0.0))
            or Money(amount_micros=0),
            action=NegotiationAction(data.get("action", "counter")),
            concession_pct=data.get("concession_pct", 0.0),
            cumulative_concession_pct=data.get("cumulative_concession_pct", 0.0),
            rationale=data.get("rationale", ""),
        ),
        rounds_remaining=rounds_remaining,
    )


def terminal_round_response(
    status_data: dict[str, Any],
    action: NegotiationAction,
    buyer_price: Optional[float] = None,
) -> NegotiationRoundResponse:
    """Build a terminal (accept/reject) response from negotiation history.

    Used for the buyer's ``accept``/``reject`` moves, which do not run the
    seller's price-evaluation engine (that stays untouched). Prices are
    taken from the last recorded round; ``accept`` may echo a ``buyer_price``.
    """
    rounds = status_data.get("rounds") or []
    last = rounds[-1] if rounds else {}
    buyer_p = buyer_price if buyer_price is not None else last.get("buyer_price", 0.0)
    seller_p = last.get("seller_price", buyer_p or 0.0)
    status = (
        NegotiationStatus.ACCEPTED
        if action is NegotiationAction.ACCEPT
        else NegotiationStatus.REJECTED
    )
    return NegotiationRoundResponse(
        negotiation_id=status_data["negotiation_id"],
        status=status,
        round=NegotiationRound(
            round_number=last.get("round_number", 1),
            buyer_price=float_to_money(buyer_p) or Money(amount_micros=0),
            seller_price=float_to_money(seller_p) or Money(amount_micros=0),
            action=action,
            rationale=last.get("rationale", ""),
        ),
        rounds_remaining=0,
    )


# ---------------------------------------------------------------------------
# Catalog surface
# ---------------------------------------------------------------------------

#: Registry-issued id of this seller org. The internal ProductDefinition
#: does not carry it (seller-local), so it is supplied at the boundary.
SELLER_ORGANIZATION_ID = "seller-premium-pub-001"

#: Internal long-form DealType values -> canonical short wire values.
_DEAL_TYPE_WIRE = {
    "programmaticguaranteed": "PG",
    "preferreddeal": "PD",
    "privateauction": "PA",
}


def _deal_type_wire_value(deal_type: Any) -> str:
    raw = getattr(deal_type, "value", deal_type)
    return _DEAL_TYPE_WIRE.get(str(raw), str(raw))


def internal_product_to_shared(product: Any) -> Product:
    """Map the seller's internal ``ProductDefinition`` to the shared Product.

    Seller-local fields with no home on the shared schema (inventory_type,
    floor_cpm, minimum_impressions, the deal-type list, audience capability
    ids) ride in the reserved ``ext`` slot so nothing is silently dropped.

    ``inventory_type`` additionally populates the shared ``ad_formats``
    field (e.g. "display" -> ["display"], "video" -> ["video"]) so buyers
    filtering the catalog by adFormat can match products. Serving
    ``ad_formats: []`` with the taxonomy hidden in ``ext.inventory_type``
    made every adFormat-filtered client-side search return zero products.
    ``ext.inventory_type`` is kept as-is for existing consumers.
    """
    currency = getattr(product, "currency", "USD") or "USD"

    inventory_type = getattr(product, "inventory_type", None)
    ad_formats = [inventory_type] if inventory_type else []

    pricing_models = getattr(product, "supported_pricing_models", None) or []
    pricing_model = PricingModel.CPM
    if pricing_models:
        pricing_model = PricingModel(getattr(pricing_models[0], "value", pricing_models[0]))

    pricing_type = PricingType.FIXED
    internal_pt = getattr(product, "pricing_type", None)
    if internal_pt is not None:
        pricing_type = PricingType(getattr(internal_pt, "value", internal_pt))

    ext = {
        "inventory_type": inventory_type,
        "floor_cpm": getattr(product, "floor_cpm", None),
        "minimum_impressions": getattr(product, "minimum_impressions", None),
        "deal_types": [
            _deal_type_wire_value(dt)
            for dt in (getattr(product, "supported_deal_types", None) or [])
        ],
        "audience_capabilities": getattr(product, "audience_capabilities", None) or [],
    }

    return Product(
        product_id=product.product_id,
        seller_organization_id=SELLER_ORGANIZATION_ID,
        name=product.name,
        description=getattr(product, "description", None),
        base_price=float_to_money(getattr(product, "base_cpm", None), currency),
        pricing_type=pricing_type,
        pricing_model=pricing_model,
        delivery_type=DeliveryType.GUARANTEED,
        ad_formats=ad_formats,
        audience_targeting=getattr(product, "audience_targeting", None),
        ad_product_targeting=getattr(product, "ad_product_targeting", None),
        content_targeting=getattr(product, "content_targeting", None),
        available_impressions=getattr(product, "maximum_impressions", None),
        ext=ext,
    )


def products_to_list_response(
    products: list[Any],
    limit: int = 50,
    offset: int = 0,
) -> ProductListResponse:
    """Build the shared :class:`ProductListResponse` from internal products.

    Pagination is applied over the full internal catalog; ``total_count`` is
    the unpaginated size (shared catalog contract).
    """
    total = len(products)
    page = products[offset : offset + limit]
    return ProductListResponse(
        products=[internal_product_to_shared(p) for p in page],
        total_count=total,
        limit=limit,
        offset=offset,
    )
