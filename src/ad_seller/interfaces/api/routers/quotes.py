# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""IAB Deals API v1.0 — quote endpoints."""

from fastapi import APIRouter, Depends

from ....services import quote_service
from .. import deps
from ..schemas import QuoteRequestModel

router = APIRouter()


@router.post("/api/v1/quotes", tags=["Quotes"])
async def create_quote(
    request: QuoteRequestModel,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Request a non-binding price quote from the seller.

    The seller evaluates the request against existing pricing rules and
    returns a quote with pricing, terms, and availability. Quotes are
    ephemeral with a 24-hour TTL — no Deal ID is created.
    """
    # Read product from cached static catalog rather than running
    # ProductSetupFlow per request (hangs in OpenDirect MCP
    # session.initialize() — see ar-uwad / catalog_service).
    catalog = deps.get_product_catalog()

    # Resolve buyer identity — API key takes priority over body
    buyer_ident = request.buyer_identity
    context = deps._build_buyer_context(
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
    )

    return await quote_service.create_quote(request, context, catalog)


@router.get("/api/v1/quotes/{quote_id}", tags=["Quotes"])
async def get_quote(quote_id: str):
    """Retrieve a previously issued quote.

    Returns 410 Gone if the quote has expired.
    """
    return await quote_service.get_quote(quote_id)
