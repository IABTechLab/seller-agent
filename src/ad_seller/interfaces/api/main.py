# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""REST API interface for programmatic access.

Provides endpoints for:
- Product catalog
- Pricing queries
- Proposal submission
- Deal generation
"""

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="Ad Seller System API",
    description="IAB OpenDirect 2.1 compliant seller API",
    version="0.1.0",
)


# =============================================================================
# Request/Response Models
# =============================================================================


class PricingRequest(BaseModel):
    """Request for pricing information."""

    product_id: str
    buyer_tier: str = "public"
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    volume: int = 0


class PricingResponse(BaseModel):
    """Pricing response."""

    product_id: str
    base_price: float
    final_price: float
    currency: str
    tier_discount: float
    volume_discount: float
    rationale: str


class ProposalRequest(BaseModel):
    """Request to submit a proposal."""

    product_id: str
    deal_type: str
    price: float
    impressions: int
    start_date: str
    end_date: str
    buyer_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None


class ProposalResponse(BaseModel):
    """Proposal submission response."""

    proposal_id: str
    recommendation: str
    status: str
    counter_terms: Optional[dict[str, Any]] = None
    approval_id: Optional[str] = None
    errors: list[str] = []


class DealRequest(BaseModel):
    """Request to generate a deal."""

    proposal_id: str
    dsp_platform: Optional[str] = None


class DealResponse(BaseModel):
    """Deal generation response."""

    deal_id: str
    deal_type: str
    price: float
    pricing_model: str
    openrtb_params: dict[str, Any]
    activation_instructions: dict[str, str]


class DiscoveryRequest(BaseModel):
    """Discovery query request."""

    query: str
    buyer_tier: str = "public"
    agency_id: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/")
async def root():
    """API root."""
    return {
        "name": "Ad Seller System API",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/products")
async def list_products():
    """List all products in the catalog."""
    from ...flows import ProductSetupFlow

    flow = ProductSetupFlow()
    await flow.kickoff()

    products = []
    for product in flow.state.products.values():
        products.append({
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "inventory_type": product.inventory_type,
            "base_cpm": product.base_cpm,
            "floor_cpm": product.floor_cpm,
            "deal_types": [dt.value for dt in product.supported_deal_types],
        })

    return {"products": products}


@app.get("/products/{product_id}")
async def get_product(product_id: str):
    """Get a specific product."""
    from ...flows import ProductSetupFlow

    flow = ProductSetupFlow()
    await flow.kickoff()

    product = flow.state.products.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "product_id": product.product_id,
        "name": product.name,
        "description": product.description,
        "inventory_type": product.inventory_type,
        "base_cpm": product.base_cpm,
        "floor_cpm": product.floor_cpm,
        "deal_types": [dt.value for dt in product.supported_deal_types],
    }


@app.post("/pricing", response_model=PricingResponse)
async def get_pricing(request: PricingRequest):
    """Get pricing for a product based on buyer context."""
    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...models.buyer_identity import BuyerContext, BuyerIdentity, AccessTier
    from ...models.pricing_tiers import TieredPricingConfig
    from ...flows import ProductSetupFlow

    # Get products
    flow = ProductSetupFlow()
    await flow.kickoff()

    product = flow.state.products.get(request.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Create buyer context
    tier_map = {
        "public": AccessTier.PUBLIC,
        "seat": AccessTier.SEAT,
        "agency": AccessTier.AGENCY,
        "advertiser": AccessTier.ADVERTISER,
    }
    access_tier = tier_map.get(request.buyer_tier.lower(), AccessTier.PUBLIC)

    identity = BuyerIdentity(
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
    )
    context = BuyerContext(
        identity=identity,
        is_authenticated=access_tier != AccessTier.PUBLIC,
    )

    # Calculate price
    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    decision = engine.calculate_price(
        product_id=request.product_id,
        base_price=product.base_cpm,
        buyer_context=context,
        volume=request.volume,
    )

    return PricingResponse(
        product_id=request.product_id,
        base_price=decision.base_price,
        final_price=decision.final_price,
        currency=decision.currency,
        tier_discount=decision.tier_discount,
        volume_discount=decision.volume_discount,
        rationale=decision.rationale,
    )


@app.post("/proposals", response_model=ProposalResponse)
async def submit_proposal(request: ProposalRequest):
    """Submit a proposal for review."""
    from ...flows import ProposalHandlingFlow, ProductSetupFlow
    from ...models.buyer_identity import BuyerContext, BuyerIdentity
    import uuid

    # Get products
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff()

    # Create buyer context
    identity = BuyerIdentity(
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
    )
    context = BuyerContext(
        identity=identity,
        is_authenticated=request.agency_id is not None,
    )

    # Process proposal
    proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
    proposal_data = {
        "product_id": request.product_id,
        "deal_type": request.deal_type,
        "price": request.price,
        "impressions": request.impressions,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "buyer_id": request.buyer_id,
    }

    flow = ProposalHandlingFlow()
    result = flow.handle_proposal(
        proposal_id=proposal_id,
        proposal_data=proposal_data,
        buyer_context=context,
        products=setup_flow.state.products,
    )

    # If pending approval, create the approval request
    if result.get("pending_approval"):
        from ...events.approval import ApprovalGate
        from ...storage.factory import get_storage
        storage = await get_storage()
        gate = ApprovalGate(storage)
        approval_req = await gate.request_approval(
            flow_id=result["flow_id"],
            flow_type="proposal_handling",
            gate_name="proposal_decision",
            context={
                "proposal_id": proposal_id,
                "recommendation": result["recommendation"],
                "evaluation": result.get("evaluation"),
                "counter_terms": result.get("counter_terms"),
            },
            flow_state_snapshot=result.get("_flow_state_snapshot", {}),
            proposal_id=proposal_id,
        )
        return ProposalResponse(
            proposal_id=proposal_id,
            recommendation=result["recommendation"],
            status="pending_approval",
            counter_terms=result.get("counter_terms"),
            approval_id=approval_req.approval_id,
            errors=result.get("errors", []),
        )

    return ProposalResponse(
        proposal_id=proposal_id,
        recommendation=result["recommendation"],
        status=result["status"],
        counter_terms=result.get("counter_terms"),
        errors=result.get("errors", []),
    )


@app.post("/deals", response_model=DealResponse)
async def generate_deal(request: DealRequest):
    """Generate a deal from an accepted proposal."""
    from ...flows import DealGenerationFlow

    flow = DealGenerationFlow()
    result = flow.generate_deal(
        proposal_id=request.proposal_id,
        proposal_data={
            "status": "accepted",
            "deal_type": "preferred_deal",
            "price": 15.0,
            "product_id": "display",
            "impressions": 1000000,
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
        },
    )

    if not result.get("deal_id"):
        raise HTTPException(status_code=400, detail="Failed to generate deal")

    return DealResponse(
        deal_id=result["deal_id"],
        deal_type=result["deal_type"],
        price=result["price"],
        pricing_model=result["pricing_model"],
        openrtb_params=result["openrtb_params"],
        activation_instructions=result["activation_instructions"],
    )


@app.post("/discovery")
async def discovery_query(request: DiscoveryRequest):
    """Process a discovery query about inventory."""
    from ...flows import DiscoveryInquiryFlow, ProductSetupFlow
    from ...models.buyer_identity import BuyerContext, BuyerIdentity, AccessTier

    # Get products
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff()

    # Create buyer context
    tier_map = {
        "public": AccessTier.PUBLIC,
        "agency": AccessTier.AGENCY,
        "advertiser": AccessTier.ADVERTISER,
    }
    access_tier = tier_map.get(request.buyer_tier.lower(), AccessTier.PUBLIC)

    identity = BuyerIdentity(agency_id=request.agency_id)
    context = BuyerContext(
        identity=identity,
        is_authenticated=access_tier != AccessTier.PUBLIC,
    )

    # Process discovery
    flow = DiscoveryInquiryFlow()
    response = flow.query(
        query=request.query,
        buyer_context=context,
        products=setup_flow.state.products,
    )

    return response


# =============================================================================
# Request/Response Models — Events & Approvals
# =============================================================================


class CreateSessionRequest(BaseModel):
    """Request to create a new session."""

    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    is_authenticated: bool = False


class SessionMessageRequest(BaseModel):
    """Request to send a message within a session."""

    message: str


class ApprovalDecisionRequest(BaseModel):
    """Request to submit an approval decision."""

    decision: str  # "approve", "reject", or "counter"
    decided_by: str = "anonymous"
    reason: str = ""
    modifications: dict[str, Any] = {}


# =============================================================================
# Event Endpoints
# =============================================================================


@app.get("/events")
async def list_events(
    flow_id: Optional[str] = None,
    event_type: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 50,
):
    """List events, optionally filtered by flow_id, event_type, or session_id."""
    from ...events.bus import get_event_bus
    bus = await get_event_bus()
    events = await bus.list_events(
        flow_id=flow_id, event_type=event_type, session_id=session_id, limit=limit
    )
    return {"events": [e.model_dump(mode="json") for e in events]}


@app.get("/events/{event_id}")
async def get_event(event_id: str):
    """Get a specific event by ID."""
    from ...events.bus import get_event_bus
    bus = await get_event_bus()
    event = await bus.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.model_dump(mode="json")


# =============================================================================
# Approval Endpoints
# =============================================================================


@app.get("/approvals")
async def list_pending_approvals():
    """List all pending approval requests."""
    from ...events.approval import ApprovalGate
    from ...storage.factory import get_storage
    storage = await get_storage()
    gate = ApprovalGate(storage)
    pending = await gate.list_pending()
    return {"approvals": [r.model_dump(mode="json") for r in pending]}


@app.get("/approvals/{approval_id}")
async def get_approval(approval_id: str):
    """Get a specific approval request and its response (if any)."""
    from ...events.approval import ApprovalGate
    from ...storage.factory import get_storage
    storage = await get_storage()
    gate = ApprovalGate(storage)
    request = await gate.get_request(approval_id)
    if not request:
        raise HTTPException(status_code=404, detail="Approval not found")
    response = await gate.get_response(approval_id)
    return {
        "request": request.model_dump(mode="json"),
        "response": response.model_dump(mode="json") if response else None,
    }


@app.post("/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, body: ApprovalDecisionRequest):
    """Submit a human decision for a pending approval."""
    from ...events.approval import ApprovalGate
    from ...storage.factory import get_storage
    storage = await get_storage()
    gate = ApprovalGate(storage)
    try:
        response = await gate.submit_decision(
            approval_id=approval_id,
            decision=body.decision,
            decided_by=body.decided_by,
            reason=body.reason,
            modifications=body.modifications,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return response.model_dump(mode="json")


@app.post("/approvals/{approval_id}/resume")
async def resume_flow(approval_id: str):
    """Resume a flow after an approval decision has been submitted.

    Loads the flow state snapshot, applies the decision, and returns
    the final result without re-running expensive crew evaluations.
    """
    from ...events.approval import ApprovalGate
    from ...storage.factory import get_storage

    storage = await get_storage()
    gate = ApprovalGate(storage)

    request = await gate.get_request(approval_id)
    if not request:
        raise HTTPException(status_code=404, detail="Approval not found")

    if request.status.value == "pending":
        raise HTTPException(
            status_code=400,
            detail="Approval has not been decided yet. Call /decide first.",
        )

    response = await gate.get_response(approval_id)
    if not response:
        raise HTTPException(status_code=400, detail="No decision found")

    # Route based on flow_type and gate_name
    if request.flow_type == "proposal_handling" and request.gate_name == "proposal_decision":
        return await _resume_proposal_flow(request, response)

    raise HTTPException(
        status_code=400,
        detail=f"Unknown flow_type/gate_name: {request.flow_type}/{request.gate_name}",
    )


async def _resume_proposal_flow(request, response):
    """Resume a proposal handling flow after approval decision."""
    from ...events.helpers import emit_event
    from ...events.models import EventType
    from ...flows.proposal_handling_flow import ProposalHandlingFlow, ProposalState
    from ...models.flow_state import ExecutionStatus
    from datetime import datetime

    snapshot = request.flow_state_snapshot

    # Re-hydrate state from snapshot
    flow = ProposalHandlingFlow()
    flow.state = ProposalState(**snapshot)

    # Apply the human decision
    if response.decision == "approve":
        flow.state.accepted_proposals.append(flow.state.proposal_id)
        flow.state.status = ExecutionStatus.ACCEPTED
    elif response.decision == "reject":
        flow.state.rejected_proposals.append(flow.state.proposal_id)
        flow.state.status = ExecutionStatus.REJECTED
    elif response.decision == "counter":
        if response.modifications:
            flow.state.counter_terms = response.modifications
        flow.state.status = ExecutionStatus.COUNTER_PENDING

    flow.state.completed_at = datetime.utcnow()

    # Emit event for the decision
    event_map = {
        "approve": EventType.PROPOSAL_ACCEPTED,
        "reject": EventType.PROPOSAL_REJECTED,
        "counter": EventType.PROPOSAL_COUNTERED,
    }
    await emit_event(
        event_type=event_map.get(response.decision, EventType.PROPOSAL_REJECTED),
        flow_id=flow.state.flow_id,
        flow_type="proposal_handling",
        proposal_id=flow.state.proposal_id,
        payload={
            "decision": response.decision,
            "decided_by": response.decided_by,
            "reason": response.reason,
        },
    )

    return {
        "proposal_id": flow.state.proposal_id,
        "status": flow.state.status.value,
        "recommendation": response.decision,
        "counter_terms": flow.state.counter_terms,
        "resumed_from_approval": request.approval_id,
    }


# =============================================================================
# Session Endpoints
# =============================================================================


@app.post("/sessions")
async def create_session(request: CreateSessionRequest):
    """Create a new buyer conversation session."""
    from ...interfaces.chat.main import ChatInterface
    from ...models.buyer_identity import BuyerContext, BuyerIdentity
    from ...storage.factory import get_storage

    storage = await get_storage()

    identity = BuyerIdentity(
        seat_id=request.seat_id,
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
    )
    context = BuyerContext(
        identity=identity,
        is_authenticated=request.is_authenticated,
    )

    chat = ChatInterface(storage=storage)
    await chat.initialize()
    session = await chat.start_session(buyer_context=context)

    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "buyer_pricing_key": session.get_buyer_pricing_key(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


@app.get("/sessions")
async def list_sessions(
    buyer_key: Optional[str] = None,
    status: Optional[str] = None,
):
    """List sessions, optionally filtered by buyer identity or status."""
    from ...models.session import Session, SessionStatus
    from ...storage.factory import get_storage

    storage = await get_storage()

    if buyer_key:
        sessions_data = await storage.get_buyer_sessions(buyer_key)
    else:
        sessions_data = await storage.list_sessions()

    results = []
    for data in sessions_data:
        s = Session(**data)
        # Lazy expiration check
        if s.is_expired() and s.status != SessionStatus.EXPIRED:
            s.status = SessionStatus.EXPIRED
            await storage.set_session(s.session_id, s.model_dump(mode="json"))
        # Apply status filter
        if status and s.status.value != status:
            continue
        results.append({
            "session_id": s.session_id,
            "status": s.status.value,
            "buyer_pricing_key": s.get_buyer_pricing_key(),
            "message_count": len(s.messages),
            "negotiation_stage": s.negotiation.stage,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        })

    return {"sessions": results}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details and conversation history."""
    from ...models.session import Session
    from ...storage.factory import get_storage

    storage = await get_storage()
    data = await storage.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    session = Session(**data)
    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "buyer_pricing_key": session.get_buyer_pricing_key(),
        "negotiation": session.negotiation.model_dump(),
        "messages": [m.model_dump(mode="json") for m in session.messages],
        "linked_flow_ids": session.linked_flow_ids,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


@app.post("/sessions/{session_id}/messages")
async def send_session_message(session_id: str, body: SessionMessageRequest):
    """Send a message within a session and get a response."""
    from ...interfaces.chat.main import ChatInterface
    from ...storage.factory import get_storage

    storage = await get_storage()

    chat = ChatInterface(storage=storage)
    await chat.initialize()

    try:
        await chat.resume_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    response = await chat.process_message_async(
        message=body.message,
        session_id=session_id,
    )

    session = chat._current_session
    return {
        "session_id": session_id,
        "text": response.get("text", ""),
        "type": response.get("type", "general"),
        "message_count": len(session.messages) if session else 0,
        "negotiation_stage": session.negotiation.stage if session else "unknown",
    }


@app.post("/sessions/{session_id}/close")
async def close_session_endpoint(session_id: str):
    """Close a session."""
    from ...interfaces.chat.main import ChatInterface
    from ...storage.factory import get_storage

    storage = await get_storage()

    chat = ChatInterface(storage=storage)
    await chat.close_session(session_id)

    return {"session_id": session_id, "status": "closed"}
