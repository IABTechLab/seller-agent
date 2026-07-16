# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Proposal submission and multi-round negotiation endpoints.

EP-12.2: the canonical negotiation surface
``POST /api/v1/negotiations/messages`` now accepts the shared
:class:`iab_agentic_primitives.protocol.NegotiationMessage` — the exact
message the buyer emits (a required ``action`` enum + ``buyer_price`` as
:class:`Money`). This is the SELLER side of the historical 422: the buyer
posted ``{"price": <float>}`` with no ``action`` while the seller's
``CounterOfferRequest`` required ``buyer_price`` and had no ``action`` at
all, so every round failed validation. Both sides now validate the same
model, so the 422 is gone by construction. The legacy
``POST /proposals/{proposal_id}/counter`` route is kept working (both URL
conventions), and the internal ``negotiation_service`` is untouched.
"""

from fastapi import APIRouter, Depends, HTTPException
from iab_agentic_primitives.protocol import NegotiationMessage, NegotiationRoundResponse
from iab_agentic_primitives.protocol.negotiation import PRICED_ACTIONS

from ....services import negotiation_service
from .. import contract_mappers as cm
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

    # EP-5.2: verify the claimed tier against the agent registry and cap at
    # the verified ceiling (blocked agents 403; unverifiable claims floor).
    context = await deps._verified_buyer_context(
        endpoint="POST /proposals",
        buyer_tier="agency" if request.agency_id else "public",
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
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

    EP-5.2: this endpoint previously applied NO trust-tier ceiling — a
    buyer could self-assert ADVERTISER pricing by populating
    advertiser_id. The claimed tier is now verified against the agent
    registry (agent_url) or the API key; unverifiable claims floor.
    """
    buyer_context = await deps._verified_buyer_context(
        endpoint="POST /proposals/{proposal_id}/counter",
        buyer_tier=request.buyer_tier,
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
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


@router.post("/api/v1/negotiations/messages", tags=["Negotiation"])
async def post_negotiation_message(
    message: NegotiationMessage,
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> NegotiationRoundResponse:
    """Canonical negotiation surface — accepts the shared NegotiationMessage.

    The seller now validates the SAME message the buyer emits: a required
    ``action`` enum (accept/counter/reject/final_offer) plus ``buyer_price``
    as :class:`Money`. A well-formed counter no longer 422s (the historical
    bug). Priced actions (counter/final_offer) run the seller's untouched
    negotiation engine; accept/reject are recorded as terminal rounds off
    the existing history.
    """
    # EP-5.2: cap the claimed tier at a verified ceiling. The shared
    # NegotiationMessage carries no agent_url (lib gap — QuoteRequest has
    # one), so verification here keys off the API key; anonymous
    # self-asserted identity claims floor to PUBLIC.
    ident = message.buyer_identity
    buyer_context = await deps._verified_buyer_context(
        endpoint="POST /api/v1/negotiations/messages",
        buyer_tier=(
            "advertiser"
            if (ident and ident.advertiser_id)
            else "agency"
            if (ident and ident.agency_id)
            else "seat"
            if (ident and ident.seat_id)
            else "public"
        ),
        agency_id=ident.agency_id if ident else None,
        advertiser_id=ident.advertiser_id if ident else None,
        seat_id=ident.seat_id if ident else None,
        api_key_record=api_key_record,
    )

    # The seller keys negotiations by proposal_id; negotiation_id doubles as
    # that key for continuation. Quote-led negotiation is not yet served.
    proposal_id = message.proposal_id or message.negotiation_id
    if proposal_id is None:
        raise HTTPException(
            status_code=400,
            detail=cm.unsupported_capability_detail(
                [{"capability": "quote_led_negotiation", "path": "quote_id"}],
                message="Negotiation requires proposal_id or negotiation_id.",
            ),
        )

    buyer_price = cm.money_to_float(message.buyer_price)

    if message.action in PRICED_ACTIONS:
        result = await negotiation_service.counter_proposal(
            proposal_id=proposal_id,
            buyer_price=buyer_price,
            buyer_context=buyer_context,
        )
        return cm.negotiation_round_to_response(result)

    # accept / reject — terminal moves off the recorded history; the price
    # engine is not run (it stays untouched).
    status_data = await negotiation_service.get_negotiation_status(proposal_id)
    return cm.terminal_round_response(status_data, message.action, buyer_price)
