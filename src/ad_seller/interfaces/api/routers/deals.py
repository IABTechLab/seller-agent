# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal endpoints: legacy generation, IAB Deals API v1.0 booking, agentic
audience match, template/bulk/curated creation, DSP export/push, SSP
distribution, and migration/deprecation/lineage.

EP-8.4 route-shadowing fix: literal/static routes are registered BEFORE
their ``{param}`` sibling on the same method + prefix, because FastAPI
matches routes in registration order. In particular
``GET /api/v1/deals/export`` is now registered BEFORE
``GET /api/v1/deals/{deal_id}`` so the literal export path is reachable
(previously ``{deal_id}="export"`` shadowed it). Path strings, methods,
and handler behavior are otherwise unchanged.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from iab_agentic_primitives.protocol import DealBookingRequest, DealBookingResponse

from ....services import deal_service
from .. import contract_mappers as cm
from .. import deps
from ..schemas import (
    AgenticAudienceMatchRequest,
    BulkDealOperationResult,
    BulkDealRequest,
    BulkDealResponse,
    CuratedDealRequest,
    CuratorRegistrationRequest,
    DealDeprecationRequest,
    DealFromTemplateRequest,
    DealFromTemplateResponse,
    DealMigrationRequest,
    DealPerformanceResponse,
    DealPushRequest,
    DealRequest,
    DealResponse,
    SSPDealDistributeRequest,
)

router = APIRouter()


@router.post("/deals", response_model=DealResponse, tags=["Deals"])
async def generate_deal(request: DealRequest):
    """Generate a deal from an accepted proposal."""
    result = deal_service.generate_deal_from_proposal(request.proposal_id)

    return DealResponse(
        deal_id=result["deal_id"],
        deal_type=result["deal_type"],
        price=result["price"],
        pricing_model=result["pricing_model"],
        openrtb_params=result["openrtb_params"],
        activation_instructions=result["activation_instructions"],
    )


@router.post("/api/v1/deals", tags=["Deal Booking"])
async def book_deal(
    request: DealBookingRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> DealBookingResponse:
    """Book a deal from a previously issued quote.

    The seller validates the quote, generates a Deal ID, and returns
    confirmed terms. This is the commit point — the quote becomes bound.

    **Wire format (proposal §5.6 + §6 row 14b):** the seller accepts both
    audience-plan content types --
    ``application/vnd.ucp.embedding+json; v=1`` (legacy UCP carrier) and
    ``application/vnd.iab.agentic-audiences+json; v=1`` (new IAB Agentic
    Audiences alias). FastAPI's body parsing is content-type-permissive, so
    both names round-trip the same Pydantic model with no custom dependency
    needed; the dual acceptance is exercised by
    ``tests/unit/test_deal_booking_snapshot.py``.

    **Snapshot (proposal §5.1 Step 2 + wire-format §6.5):** when the request
    carries an ``audience_plan``, the seller persists it verbatim as
    ``audience_plan_snapshot`` against the deal record and returns the
    snapshot plus a per-role ``audience_match_summary`` so the buyer can
    verify the booking. The snapshot is authoritative for the lifetime of
    the deal -- if seller capabilities change mid-flight, the snapshot is
    honored (see ``services/fulfillment.honor_audience_plan_snapshot``).

    **Forensic logging (proposal §5.1 Step 2):** the
    ``audience_plan_id`` hash is logged at INFO via
    ``ad_seller.audience.booking``. The buyer logs the same hash on its
    side; matching entries are the cross-system anchor for dispute
    resolution.

    **Idempotency (FD-12):** the request carries a required
    ``idempotency_key``. A replay with a key already booked returns the
    same Deal without minting a second one (no duplicate side effect).
    """
    from ....storage.factory import get_storage

    internal_request = cm.deal_booking_request_to_internal(request)

    # Honor the shared idempotency key: same key -> same response, no
    # duplicate booking (FD-12). Kept at the wire edge so deal_service
    # stays untouched. Defensive against storage backends/mocks that do
    # not implement the generic get/set KV methods.
    idem_storage_key = f"idempotency:deal:{request.idempotency_key}"
    storage = await get_storage()
    try:
        prior_deal_id = await storage.get(idem_storage_key)
    except Exception:
        prior_deal_id = None
    # Only a real, previously-persisted string id counts as a prior booking;
    # this also guards against AsyncMock storages that auto-return truthy
    # sentinels for undefined KV methods.
    if isinstance(prior_deal_id, str) and prior_deal_id:
        existing = await deal_service.get_deal(prior_deal_id)
        return cm.internal_deal_to_response(existing)

    result = await deal_service.book_deal(internal_request)

    try:
        await storage.set(idem_storage_key, result["deal_id"], ttl=86400)
    except Exception:
        pass

    return cm.internal_deal_to_response(result)


@router.post("/agentic-audience/match", tags=["Audience"])
async def agentic_audience_match(request: AgenticAudienceMatchRequest):
    """Match a buyer-supplied agentic `AudienceRef` against this seller.

    Per proposal §5.7 + §6 row 11. Returns a match score and quality bucket.
    The score is mock-quality (deterministic from sha256 of `identifier`);
    the real embedding-similarity model is Epic 2 (E2-2).

    Behavior:
    - Non-agentic refs return HTTP 400.
    - Sellers with no top-level agentic capability (legacy / agentic
      decommissioned) return `agentic_supported_by_seller=False`,
      `match_quality="POOR"`, score 0.
    - Otherwise the score is deterministic per `identifier` and bucketed
      into `STRONG | MODERATE | WEAK | POOR`.
    """
    return deal_service.match_agentic_audience(request.audience_ref)


@router.get("/api/v1/deals/export", tags=["Deal Booking"])
async def export_deals(
    format: str = "generic",
    status: Optional[str] = None,
):
    """Export deals in DSP-native format for platform connectors.

    Args:
        format: Export format — generic, ttd, dv360, amazon, xandr
        status: Filter by deal status (confirmed, proposed, cancelled)

    Returns deals formatted for the target DSP's import requirements.
    Enables buyer Phase 4D platform connectors to pull deals natively.

    NOTE (EP-8.4): this literal route is registered BEFORE the
    ``/api/v1/deals/{deal_id}`` catch-all so it is not shadowed. FastAPI
    matches routes in registration order, so static/literal paths must
    precede their ``{param}`` sibling on the same method + prefix.
    """
    return await deal_service.export_deals(format=format, status=status)


@router.get("/api/v1/deals/{deal_id}", tags=["Deal Booking"])
async def get_deal_by_id(deal_id: str) -> DealBookingResponse:
    """Get the current status of a deal.

    Performs a lazy expiry check for deals in 'proposed' status. Returns
    the shared :class:`DealBookingResponse` (wraps the Deal primitive).
    """
    result = await deal_service.get_deal(deal_id)
    return cm.internal_deal_to_response(result)


@router.post(
    "/api/v1/deals/from-template",
    tags=["Deal Booking"],
    response_model=DealFromTemplateResponse,
    status_code=201,
)
async def create_deal_from_template(
    request: DealFromTemplateRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Create a deal directly from template parameters (quote + auto-book).

    Accepts structured template params instead of requiring a pre-existing
    quote. Internally runs the pricing engine, validates the buyer's max_cpm
    against the floor price, and auto-books the deal if acceptable.

    Returns 201 with the created deal on success.
    Returns 422 when max_cpm is below the seller's floor price, including
    the seller's minimum price in the response.
    Returns 401 for unauthenticated requests.
    """
    # Require authentication
    if not api_key_record:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authentication_required",
                "message": "API key required for deal creation.",
            },
        )

    # Product data from the single cached catalog source (EP-3.3)
    catalog = deps.get_product_catalog()

    # Resolve buyer context from API key + body. EP-5.2: the tier is capped
    # at the registry-verified ceiling when an agent_url is presented (the
    # API key identity is the EP-4.5 verified principal otherwise).
    buyer_ident = request.buyer_identity
    context = await deps._verified_buyer_context(
        endpoint="POST /api/v1/deals/from-template",
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

    deal_data = await deal_service.create_deal_from_template(request, context, catalog)

    return DealFromTemplateResponse(
        deal_id=deal_data["deal_id"],
        status="confirmed",
        deal_type=deal_data["deal_type"],
        product_id=deal_data["product_id"],
        actual_price_cpm=deal_data["actual_price_cpm"],
        impressions=deal_data["impressions"],
        flight_start=deal_data["flight_start"],
        flight_end=deal_data["flight_end"],
        buyer_tier=deal_data["buyer_tier"],
        activation_instructions=deal_data["activation_instructions"],
        schain=deal_data["schain"],
        created_at=deal_data["created_at"],
    )


@router.get("/api/v1/deals/{deal_id}/performance", tags=["Deal Performance"])
async def get_deal_performance(deal_id: str):
    """Return delivery stats for a deal.

    Provides performance feedback for buyer SPO (Supply Path Optimization).
    Returns placeholder/mock stats initially — real ad server integration
    comes in a future phase.
    """
    data = await deal_service.get_deal_performance(deal_id)
    return DealPerformanceResponse(**data)


@router.post("/api/v1/deals/bulk", tags=["Bulk Operations"], response_model=BulkDealResponse)
async def bulk_deal_operations(
    request: BulkDealRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Process a batch of deal operations (create/update/cancel).

    Enables the Deal Library buyer agent to efficiently manage multiple
    deals in a single request. Each operation is processed independently
    and returns per-operation success/failure.
    """
    results = await deal_service.bulk_deal_operations(request.operations)

    result_models = [BulkDealOperationResult(**r) for r in results]
    succeeded = sum(1 for r in result_models if r.success)
    return BulkDealResponse(
        total=len(request.operations),
        succeeded=succeeded,
        failed=len(request.operations) - succeeded,
        results=result_models,
    )


@router.post("/api/v1/deals/push", tags=["Deal Booking"])
async def push_deal_to_buyers(request: DealPushRequest):
    """Push a deal to one or more buyer endpoints via IAB Deals API v1.0.

    The seller sends deal terms to buyer DSPs. Each buyer receives an
    HTTP POST with the full IAB Deal object and responds with acceptance status.

    This is the standardized deal distribution path — alternative to
    SSP-mediated distribution (PubMatic, Index Exchange, etc.).
    """
    return await deal_service.push_deal_to_buyers(request)


@router.get("/api/v1/deals/{deal_id}/buyer-status", tags=["Deal Booking"])
async def get_deal_buyer_status(deal_id: str, buyer_url: str):
    """Query a buyer for their acceptance status of a deal.

    Polls the buyer's deal status endpoint to check if the deal
    has been approved, rejected, or is ready to serve.
    """
    return await deal_service.get_deal_buyer_status(deal_id, buyer_url)


@router.post("/api/v1/deals/distribute", tags=["Deal Booking"])
async def distribute_deal_via_ssp(request: SSPDealDistributeRequest):
    """Distribute a deal through configured SSP(s).

    Routes the deal to the appropriate SSP based on routing rules
    or explicit ssp_name. The SSP handles DSP-side distribution.

    Supports multiple SSPs: PubMatic (MCP), Index Exchange (REST),
    Magnite (REST), or any configured SSP connector.
    """
    return await deal_service.distribute_deal_via_ssp(request)


@router.get("/api/v1/deals/{deal_id}/ssp-troubleshoot", tags=["Deal Booking"])
async def troubleshoot_deal_via_ssp(deal_id: str, ssp_name: str):
    """Troubleshoot a deal via SSP diagnostics.

    Calls the SSP's troubleshooting tool (e.g., PubMatic's
    deal_troubleshooting) to diagnose performance issues.
    """
    return await deal_service.troubleshoot_deal_via_ssp(deal_id, ssp_name)


@router.get("/api/v1/curators", tags=["Curators"])
async def list_curators():
    """List all registered curators.

    Returns curators who can create deals against this publisher's
    inventory. Agent Range is pre-registered as a day-one curator.
    """
    return deal_service.list_curators()


@router.get("/api/v1/curators/{curator_id}", tags=["Curators"])
async def get_curator(curator_id: str):
    """Get details for a specific curator."""
    return deal_service.get_curator(curator_id)


@router.post("/api/v1/curators", tags=["Curators"], status_code=201)
async def register_curator(request: CuratorRegistrationRequest):
    """Register a new curator.

    Curators can then create deals against this publisher's inventory
    via the /api/v1/deals/curated endpoint.
    """
    return await deal_service.register_curator(request)


@router.post("/api/v1/deals/curated", tags=["Curators"], status_code=201)
async def create_curated_deal(request: CuratedDealRequest):
    """Create a deal with curator overlay.

    The curator's fee is added on top of the publisher's base price.
    The curator appears as a node in the deal's schain. The buyer
    pays the total CPM (publisher + curator fee).

    The deal is created via the normal from-template flow, then
    enriched with curator identity, fee, and targeting overlay.
    """
    # Product base price from the single cached catalog source (EP-3.3)
    catalog = deps.get_product_catalog()
    return await deal_service.create_curated_deal(request, catalog)


@router.post("/api/v1/deals/{deal_id}/migrate", tags=["Deal Booking"], status_code=201)
async def migrate_deal(
    deal_id: str,
    request: DealMigrationRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Migrate (replace) an existing deal with a new one.

    Creates a replacement deal with parent_deal_id lineage pointing
    to the old deal, then deprecates the old deal. The buyer's Deal
    Jockey can follow the lineage chain to track deal evolution.

    Returns the new deal with lineage metadata.
    """
    return await deal_service.migrate_deal(deal_id, request)


@router.post("/api/v1/deals/{deal_id}/deprecate", tags=["Deal Booking"])
async def deprecate_deal(
    deal_id: str,
    request: DealDeprecationRequest,
):
    """Deprecate a deal with reason and optional replacement.

    Marks the deal as deprecated rather than cancelled — preserving
    the history that this deal was intentionally sunset. If a
    replacement_deal_id is provided, creates a lineage link.

    The buyer's Deal Library uses this to:
    - Know which deals to stop targeting
    - Follow lineage to the replacement deal
    - Feed SPO scoring (why was this path deprecated?)
    """
    return await deal_service.deprecate_deal(deal_id, request)


@router.get("/api/v1/deals/{deal_id}/lineage", tags=["Deal Booking"])
async def get_deal_lineage(deal_id: str):
    """Get the lineage chain for a deal.

    Walks parent_deal_id backwards and replacement_deal_id forwards
    to show the full evolution of a deal through migrations.
    """
    return await deal_service.get_deal_lineage(deal_id)
