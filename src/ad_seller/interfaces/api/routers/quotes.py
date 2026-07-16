# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""IAB Deals API v1.0 — quote endpoints.

EP-12.2: the wire edge speaks the shared ``iab-agentic-primitives``
contract. The request body is the shared
:class:`iab_agentic_primitives.protocol.QuoteRequest` (which carries
``media_type``/``linear_tv``/``audience_plan``/``agent_url`` — fields the
seller's old inline model silently dropped) and the response is the shared
:class:`~iab_agentic_primitives.protocol.QuoteResponse`. The internal
``quote_service`` is untouched; :mod:`..contract_mappers` translates at the
boundary. Unsupported media (e.g. ``linear_tv``) is rejected structurally
(FD-6) rather than mispriced.
"""

from fastapi import APIRouter, Depends, HTTPException
from iab_agentic_primitives.protocol import QuoteRequest, QuoteResponse

from ....services import quote_service
from .. import contract_mappers as cm
from .. import deps

router = APIRouter()


@router.post("/api/v1/quotes", tags=["Quotes"])
async def create_quote(
    request: QuoteRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> QuoteResponse:
    """Request a non-binding price quote from the seller.

    The seller evaluates the request against existing pricing rules and
    returns a quote with pricing, terms, and availability. Quotes are
    ephemeral with a 24-hour TTL — no Deal ID is created.
    """
    # FD-6: reject unsupported media structurally instead of silently
    # dropping/mispricing (the old inline model dropped media_type entirely).
    if request.media_type not in cm.SUPPORTED_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=cm.unsupported_capability_detail(
                [{"capability": request.media_type.value, "path": "media_type"}],
                message=f"Seller does not support media_type '{request.media_type.value}'.",
            ),
        )

    # Read product from cached static catalog rather than running
    # ProductSetupFlow per request (hangs in OpenDirect MCP
    # session.initialize() — see ar-uwad / catalog_service).
    catalog = deps.get_product_catalog()

    # Resolve buyer identity — API key takes priority over body. EP-5.2:
    # the claimed tier is verified against the agent registry (agent_url)
    # and capped at the verified ceiling; unverifiable claims floor.
    buyer_ident = request.buyer_identity
    context = await deps._verified_buyer_context(
        endpoint="POST /api/v1/quotes",
        buyer_tier=(
            "advertiser"
            if (buyer_ident and buyer_ident.advertiser_id)
            else "agency"
            if (buyer_ident and buyer_ident.agency_id)
            else "seat"
            if (buyer_ident and buyer_ident.seat_id)
            else "public"
        ),
        agency_id=buyer_ident.agency_id if buyer_ident else None,
        advertiser_id=buyer_ident.advertiser_id if buyer_ident else None,
        seat_id=buyer_ident.seat_id if buyer_ident else None,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
    )

    internal_request = cm.quote_request_to_internal(request)
    result = await quote_service.create_quote(internal_request, context, catalog)
    return cm.internal_quote_to_response(result, media_type=request.media_type)


@router.get("/api/v1/quotes/{quote_id}", tags=["Quotes"])
async def get_quote(quote_id: str) -> QuoteResponse:
    """Retrieve a previously issued quote.

    Returns 410 Gone if the quote has expired.
    """
    result = await quote_service.get_quote(quote_id)
    return cm.internal_quote_to_response(result)
