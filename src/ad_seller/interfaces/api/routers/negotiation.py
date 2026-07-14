# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Proposal submission and multi-round negotiation endpoints."""

from fastapi import APIRouter, Depends

from ....services import negotiation_service
from .. import deps
from ..schemas import CounterOfferRequest, ProposalRequest, ProposalResponse

router = APIRouter()


@router.post("/proposals", response_model=ProposalResponse, tags=["Proposals"])
async def submit_proposal(
    request: ProposalRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Submit a proposal for review."""
    # Product data from the single cached catalog source (EP-3.3)
    catalog = deps.get_product_catalog()

    # Enforce agent registry
    _, max_tier = await deps._resolve_and_enforce_agent(request.agent_url)

    # Create buyer context (API key identity overrides body params)
    context = deps._build_buyer_context(
        buyer_tier="agency" if request.agency_id else "public",
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
        max_access_tier=max_tier,
    )

    result = await negotiation_service.submit_proposal(request, context, catalog)
    return ProposalResponse(**result)


@router.post("/proposals/{proposal_id}/counter", tags=["Negotiation"])
async def counter_proposal(
    proposal_id: str,
    request: CounterOfferRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Submit a counter-offer in an ongoing negotiation.

    Loads or creates a NegotiationHistory, evaluates the buyer's offer,
    persists the updated history, and emits a NEGOTIATION_ROUND event.
    """
    buyer_context = deps._build_buyer_context(
        buyer_tier=request.buyer_tier,
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
    )

    return await negotiation_service.counter_proposal(
        proposal_id=proposal_id,
        buyer_price=request.buyer_price,
        buyer_context=buyer_context,
    )


@router.get("/proposals/{proposal_id}/negotiation", tags=["Negotiation"])
async def get_negotiation_status(proposal_id: str):
    """Get full negotiation history for a proposal."""
    return await negotiation_service.get_negotiation_status(proposal_id)
