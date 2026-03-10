# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""REST API interface for programmatic access.

Provides endpoints for:
- Product catalog
- Pricing queries
- Proposal submission
- Deal generation
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
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
    agent_url: Optional[str] = None


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
    agent_url: Optional[str] = None


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
    agent_url: Optional[str] = None


class PackageCreateRequest(BaseModel):
    """Request to create a curated package."""

    name: str
    description: Optional[str] = None
    product_ids: list[str] = []
    cat: list[str] = []
    cattax: int = 2
    audience_segment_ids: list[str] = []
    device_types: list[int] = []
    ad_formats: list[str] = []
    geo_targets: list[str] = []
    base_price: float
    floor_price: float
    tags: list[str] = []
    is_featured: bool = False
    seasonal_label: Optional[str] = None


class DynamicPackageRequest(BaseModel):
    """Request to assemble a dynamic package from product IDs."""

    name: str
    product_ids: list[str]


class MediaKitSearchRequest(BaseModel):
    """Request to search packages."""

    query: str
    buyer_tier: str = "public"
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None


class CounterOfferRequest(BaseModel):
    """Request to submit a counter-offer in a negotiation."""

    buyer_price: float
    buyer_tier: str = "public"
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None


class QuoteBuyerIdentityModel(BaseModel):
    """Buyer identity in a quote request."""

    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    dsp_platform: Optional[str] = None


class QuoteRequestModel(BaseModel):
    """API request model for POST /api/v1/quotes."""

    product_id: str
    deal_type: str
    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    target_cpm: Optional[float] = None
    buyer_identity: Optional[QuoteBuyerIdentityModel] = None


class DealBookingRequestModel(BaseModel):
    """API request model for POST /api/v1/deals."""

    quote_id: str
    buyer_identity: Optional[QuoteBuyerIdentityModel] = None
    notes: Optional[str] = None


# =============================================================================
# Auth & Context Helpers (must be defined before endpoints that use Depends)
# =============================================================================


async def _get_optional_api_key_record(
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
):
    """FastAPI dependency: validate API key from headers if present.

    Returns None for anonymous requests (no key in headers).
    Raises HTTPException(401) for invalid, revoked, or expired keys.
    Accepts ``Authorization: Bearer <key>`` or ``X-Api-Key: <key>``.
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
    from ...models.buyer_identity import BuyerContext, BuyerIdentity, AccessTier

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
async def get_pricing(
    request: PricingRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Get pricing for a product based on buyer context."""
    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...models.pricing_tiers import TieredPricingConfig
    from ...flows import ProductSetupFlow

    # Get products
    flow = ProductSetupFlow()
    await flow.kickoff()

    product = flow.state.products.get(request.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Enforce agent registry (blocked agents get 403 before any data)
    _, max_tier = await _resolve_and_enforce_agent(request.agent_url)

    context = _build_buyer_context(
        buyer_tier=request.buyer_tier,
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
        max_access_tier=max_tier,
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
async def submit_proposal(
    request: ProposalRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Submit a proposal for review."""
    from ...flows import ProposalHandlingFlow, ProductSetupFlow
    import uuid

    # Get products
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff()

    # Enforce agent registry
    _, max_tier = await _resolve_and_enforce_agent(request.agent_url)

    # Create buyer context (API key identity overrides body params)
    context = _build_buyer_context(
        buyer_tier="agency" if request.agency_id else "public",
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
        max_access_tier=max_tier,
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
async def discovery_query(
    request: DiscoveryRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Process a discovery query about inventory."""
    from ...flows import DiscoveryInquiryFlow, ProductSetupFlow

    # Get products
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff()

    # Enforce agent registry
    _, max_tier = await _resolve_and_enforce_agent(request.agent_url)

    # Create buyer context (API key identity overrides body params)
    context = _build_buyer_context(
        buyer_tier=request.buyer_tier,
        agency_id=request.agency_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
        max_access_tier=max_tier,
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
    agent_url: Optional[str] = None


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
async def create_session(
    request: CreateSessionRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Create a new buyer conversation session."""
    from ...interfaces.chat.main import ChatInterface
    from ...storage.factory import get_storage

    # Enforce agent registry
    _, max_tier = await _resolve_and_enforce_agent(request.agent_url)

    storage = await get_storage()

    # API key identity overrides body params; is_authenticated derived from key
    context = _build_buyer_context(
        buyer_tier="advertiser" if request.advertiser_id else (
            "agency" if request.agency_id else (
                "seat" if request.seat_id else "public"
            )
        ),
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        seat_id=request.seat_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
        max_access_tier=max_tier,
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


# =============================================================================
# Negotiation Endpoints
# =============================================================================


@app.post("/proposals/{proposal_id}/counter")
async def counter_proposal(
    proposal_id: str,
    request: CounterOfferRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Submit a counter-offer in an ongoing negotiation.

    Loads or creates a NegotiationHistory, evaluates the buyer's offer,
    persists the updated history, and emits a NEGOTIATION_ROUND event.
    """
    from ...engines.negotiation_engine import NegotiationEngine
    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...engines.yield_optimizer import YieldOptimizer
    from ...events.helpers import emit_event
    from ...events.models import EventType
    from ...models.negotiation import NegotiationHistory
    from ...models.pricing_tiers import TieredPricingConfig
    from ...storage.factory import get_storage

    storage = await get_storage()
    config = TieredPricingConfig(seller_organization_id="default")
    pricing_engine = PricingRulesEngine(config)
    yield_opt = YieldOptimizer()
    neg_engine = NegotiationEngine(pricing_engine, yield_opt)

    buyer_context = _build_buyer_context(
        buyer_tier=request.buyer_tier,
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
    )

    # Load existing negotiation or start new one
    existing = await storage.get_negotiation(proposal_id)
    if existing:
        history = NegotiationHistory(**existing)
        if history.status != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Negotiation is {history.status}, cannot counter",
            )
    else:
        # Look up proposal to get product info
        proposal_data = await storage.get_proposal(proposal_id)
        if not proposal_data:
            raise HTTPException(status_code=404, detail="Proposal not found")

        product_id = proposal_data.get("product_id", "")
        product_data = await storage.get_product(product_id)
        if not product_data:
            raise HTTPException(status_code=404, detail="Product not found")

        history = neg_engine.start_negotiation(
            proposal_id=proposal_id,
            product_id=product_id,
            buyer_context=buyer_context,
            base_price=product_data.get("base_cpm", 0),
            floor_price=product_data.get("floor_cpm", 0),
        )

        await emit_event(
            event_type=EventType.NEGOTIATION_STARTED,
            proposal_id=proposal_id,
            payload={
                "negotiation_id": history.negotiation_id,
                "strategy": history.strategy.value,
                "base_price": history.base_price,
            },
        )

    # Evaluate buyer's offer
    round_result = neg_engine.evaluate_buyer_offer(
        history, request.buyer_price, buyer_context
    )
    history = neg_engine.record_round(history, round_result)

    # Persist
    await storage.set_negotiation(proposal_id, history.model_dump(mode="json"))

    # Emit round event
    await emit_event(
        event_type=EventType.NEGOTIATION_ROUND,
        proposal_id=proposal_id,
        payload={
            "negotiation_id": history.negotiation_id,
            "round_number": round_result.round_number,
            "action": round_result.action.value,
            "buyer_price": round_result.buyer_price,
            "seller_price": round_result.seller_price,
        },
    )

    # Emit concluded event if terminal
    if history.status in ("accepted", "rejected"):
        await emit_event(
            event_type=EventType.NEGOTIATION_CONCLUDED,
            proposal_id=proposal_id,
            payload={
                "negotiation_id": history.negotiation_id,
                "status": history.status,
                "total_rounds": len(history.rounds),
                "final_price": round_result.seller_price,
            },
        )

    return {
        "negotiation_id": history.negotiation_id,
        "round_number": round_result.round_number,
        "action": round_result.action.value,
        "buyer_price": round_result.buyer_price,
        "seller_price": round_result.seller_price,
        "concession_pct": round_result.concession_pct,
        "cumulative_concession_pct": round_result.cumulative_concession_pct,
        "rationale": round_result.rationale,
        "status": history.status,
        "rounds_remaining": history.limits.max_rounds - round_result.round_number,
    }


@app.get("/proposals/{proposal_id}/negotiation")
async def get_negotiation_status(proposal_id: str):
    """Get full negotiation history for a proposal."""
    from ...models.negotiation import NegotiationHistory
    from ...storage.factory import get_storage

    storage = await get_storage()
    data = await storage.get_negotiation(proposal_id)
    if not data:
        raise HTTPException(status_code=404, detail="No negotiation found for this proposal")

    history = NegotiationHistory(**data)
    return {
        "negotiation_id": history.negotiation_id,
        "proposal_id": history.proposal_id,
        "product_id": history.product_id,
        "buyer_tier": history.buyer_tier.value,
        "strategy": history.strategy.value,
        "base_price": history.base_price,
        "floor_price": history.floor_price,
        "status": history.status,
        "total_rounds": len(history.rounds),
        "max_rounds": history.limits.max_rounds,
        "rounds": [r.model_dump(mode="json") for r in history.rounds],
        "started_at": history.started_at.isoformat(),
        "completed_at": history.completed_at.isoformat() if history.completed_at else None,
        "package_id": history.package_id,
    }


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


# =============================================================================
# Media Kit Endpoints (Public — no auth required)
# =============================================================================


@app.get("/media-kit")
async def media_kit_overview():
    """Public media kit catalog overview."""
    service = await _get_media_kit_service()
    packages = await service.list_packages_public()
    featured = [p for p in packages if p.is_featured]

    return {
        "total_packages": len(packages),
        "featured_count": len(featured),
        "featured": [p.model_dump() for p in featured],
        "all_packages": [p.model_dump() for p in packages],
    }


@app.get("/media-kit/packages")
async def list_media_kit_packages(
    layer: Optional[str] = None,
    featured_only: bool = False,
):
    """List packages with public view (price ranges, no exact pricing)."""
    from ...models.media_kit import PackageLayer

    pkg_layer = None
    if layer:
        try:
            pkg_layer = PackageLayer(layer)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid layer: {layer}")

    service = await _get_media_kit_service()
    packages = await service.list_packages_public(layer=pkg_layer, featured_only=featured_only)
    return {"packages": [p.model_dump() for p in packages]}


@app.get("/media-kit/packages/{package_id}")
async def get_media_kit_package(package_id: str):
    """Get a single package with public view."""
    service = await _get_media_kit_service()
    package = await service.get_package_public(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package.model_dump()


@app.post("/media-kit/search")
async def search_media_kit(
    request: MediaKitSearchRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Search packages by keyword. Authenticated buyers get richer results."""
    context = None
    if api_key_record is not None or request.buyer_tier != "public":
        context = _build_buyer_context(
            buyer_tier=request.buyer_tier,
            agency_id=request.agency_id,
            advertiser_id=request.advertiser_id,
            api_key_record=api_key_record,
        )

    service = await _get_media_kit_service()
    results = await service.search_packages(request.query, buyer_context=context)
    return {"results": [r.model_dump() for r in results]}


# =============================================================================
# Package Endpoints (Authenticated / Admin)
# =============================================================================


@app.get("/packages")
async def list_packages(
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    layer: Optional[str] = None,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """List packages with tier-gated view."""
    from ...models.media_kit import PackageLayer

    pkg_layer = None
    if layer:
        try:
            pkg_layer = PackageLayer(layer)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid layer: {layer}")

    service = await _get_media_kit_service()

    if api_key_record is None and buyer_tier == "public":
        packages = await service.list_packages_public(layer=pkg_layer)
    else:
        context = _build_buyer_context(
            buyer_tier=buyer_tier,
            agency_id=agency_id,
            advertiser_id=advertiser_id,
            api_key_record=api_key_record,
        )
        packages = await service.list_packages_authenticated(context, layer=pkg_layer)

    return {"packages": [p.model_dump() for p in packages]}


@app.get("/packages/{package_id}")
async def get_package(
    package_id: str,
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Get a single package with tier-gated view."""
    service = await _get_media_kit_service()

    if api_key_record is None and buyer_tier == "public":
        package = await service.get_package_public(package_id)
    else:
        context = _build_buyer_context(
            buyer_tier=buyer_tier,
            agency_id=agency_id,
            advertiser_id=advertiser_id,
            api_key_record=api_key_record,
        )
        package = await service.get_package_authenticated(package_id, context)

    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package.model_dump()


@app.post("/packages")
async def create_package(request: PackageCreateRequest):
    """Create a curated package (Layer 2)."""
    import uuid as _uuid
    from ...models.media_kit import Package, PackageLayer, PackagePlacement, PackageStatus
    from ...storage.factory import get_storage
    from ...events.helpers import emit_event
    from ...events.models import EventType

    storage = await get_storage()

    # Build placements from product_ids
    placements = []
    for pid in request.product_ids:
        prod_data = await storage.get_product(pid)
        if prod_data:
            from ...models.flow_state import ProductDefinition
            prod = ProductDefinition(**prod_data)
            placements.append(PackagePlacement(
                product_id=prod.product_id,
                product_name=prod.name,
                ad_formats=request.ad_formats or _default_ad_formats(prod.inventory_type),
                device_types=request.device_types or _default_device_types(prod.inventory_type),
            ))

    package = Package(
        package_id=f"pkg-{_uuid.uuid4().hex[:8]}",
        name=request.name,
        description=request.description,
        layer=PackageLayer.CURATED,
        status=PackageStatus.ACTIVE,
        placements=placements,
        cat=request.cat,
        cattax=request.cattax,
        audience_segment_ids=request.audience_segment_ids,
        device_types=request.device_types,
        ad_formats=request.ad_formats,
        geo_targets=request.geo_targets,
        base_price=request.base_price,
        floor_price=request.floor_price,
        tags=request.tags,
        is_featured=request.is_featured,
        seasonal_label=request.seasonal_label,
    )

    service = await _get_media_kit_service()
    created = await service.create_package(package)

    await emit_event(
        event_type=EventType.PACKAGE_CREATED,
        payload={"package_id": created.package_id, "name": created.name, "layer": "curated"},
    )

    return created.model_dump(mode="json")


@app.put("/packages/{package_id}")
async def update_package(package_id: str, updates: dict[str, Any]):
    """Update an existing package."""
    from ...events.helpers import emit_event
    from ...events.models import EventType

    service = await _get_media_kit_service()
    package = await service.update_package(package_id, updates)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    await emit_event(
        event_type=EventType.PACKAGE_UPDATED,
        payload={"package_id": package_id, "updated_fields": list(updates.keys())},
    )

    return package.model_dump(mode="json")


@app.delete("/packages/{package_id}")
async def delete_package(package_id: str):
    """Archive a package (soft delete)."""
    service = await _get_media_kit_service()
    deleted = await service.delete_package(package_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Package not found")
    return {"package_id": package_id, "status": "archived"}


@app.post("/packages/assemble")
async def assemble_package(request: DynamicPackageRequest):
    """Assemble a dynamic package (Layer 3) from product IDs."""
    service = await _get_media_kit_service()
    package = await service.assemble_dynamic_package(request.name, request.product_ids)
    if not package:
        raise HTTPException(status_code=400, detail="No valid products found for assembly")
    return package.model_dump(mode="json")


@app.post("/packages/sync")
async def sync_packages():
    """Trigger ad server inventory sync (Layer 1)."""
    from ...flows import ProductSetupFlow
    from ...events.helpers import emit_event
    from ...events.models import EventType

    flow = ProductSetupFlow()
    await flow.kickoff()

    await emit_event(
        event_type=EventType.PACKAGE_SYNCED,
        payload={"synced_count": len(flow.state.synced_segments)},
    )

    return {
        "status": "synced",
        "synced_packages": flow.state.synced_segments,
        "warnings": flow.state.warnings,
    }


# =============================================================================
# Package endpoint helpers
# =============================================================================


def _default_ad_formats(inventory_type: str) -> list[str]:
    """Default ad formats for an inventory type."""
    return {
        "display": ["banner"],
        "video": ["video"],
        "ctv": ["video"],
        "mobile_app": ["banner", "video"],
        "native": ["native"],
    }.get(inventory_type, ["banner"])


def _default_device_types(inventory_type: str) -> list[int]:
    """Default AdCOM device types for an inventory type."""
    return {
        "display": [2, 4, 5],
        "video": [2, 4, 5],
        "ctv": [3, 7],
        "mobile_app": [4, 5],
        "native": [2, 4, 5],
    }.get(inventory_type, [2])


# =============================================================================
# Agent Registry Helpers
# =============================================================================


async def _get_registry_service():
    """Create an AgentRegistryService with storage + AAMP client."""
    from ...registry import AgentRegistryService
    from ...clients.agent_registry_client import AAMPRegistryClient
    from ...storage.factory import get_storage

    storage = await get_storage()
    settings = _get_api_settings()
    aamp = AAMPRegistryClient(registry_url=settings.agent_registry_url)

    # Build client list: AAMP primary + any extra registries
    clients = [aamp]
    if settings.agent_registry_extra_urls:
        for url in settings.agent_registry_extra_urls.split(","):
            url = url.strip()
            if url:
                # Extra registries use AAMP client for now (same protocol)
                # Subclass BaseRegistryClient for vendor-specific registries
                clients.append(AAMPRegistryClient(registry_url=url))

    return AgentRegistryService(storage, registry_clients=clients)


def _get_api_settings():
    """Get settings for API use."""
    from ...config import get_settings
    return get_settings()


# =============================================================================
# API Key Management Endpoints (Operator-facing)
# =============================================================================


class CreateApiKeyRequest(BaseModel):
    """Request to create a new API key for a buyer."""

    seat_id: Optional[str] = None
    seat_name: Optional[str] = None
    dsp_platform: Optional[str] = None
    agency_id: Optional[str] = None
    agency_name: Optional[str] = None
    agency_holding_company: Optional[str] = None
    advertiser_id: Optional[str] = None
    advertiser_name: Optional[str] = None
    label: str = ""
    expires_in_days: Optional[int] = None


@app.post("/auth/api-keys")
async def create_api_key(request: CreateApiKeyRequest):
    """Create a new API key for a buyer.

    The response contains the full API key which is shown ONLY ONCE.
    Store it securely — it cannot be retrieved again.
    """
    from ...auth.api_key_service import ApiKeyService
    from ...models.api_key import ApiKeyCreateRequest
    from ...storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)

    create_req = ApiKeyCreateRequest(
        seat_id=request.seat_id,
        seat_name=request.seat_name,
        dsp_platform=request.dsp_platform,
        agency_id=request.agency_id,
        agency_name=request.agency_name,
        agency_holding_company=request.agency_holding_company,
        advertiser_id=request.advertiser_id,
        advertiser_name=request.advertiser_name,
        label=request.label,
        expires_in_days=request.expires_in_days,
    )

    response = await service.create_key(create_req)
    return response.model_dump(mode="json")


@app.get("/auth/api-keys")
async def list_api_keys():
    """List all API keys (metadata only, no secrets)."""
    from ...auth.api_key_service import ApiKeyService
    from ...storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)
    keys = await service.list_keys()
    return {
        "keys": [k.model_dump(mode="json") for k in keys],
        "total": len(keys),
    }


@app.get("/auth/api-keys/{key_id}")
async def get_api_key_details(key_id: str):
    """Get details for a specific API key."""
    from ...auth.api_key_service import ApiKeyService
    from ...storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)
    info = await service.get_key_info(key_id)
    if not info:
        raise HTTPException(status_code=404, detail="API key not found")
    return info.model_dump(mode="json")


@app.delete("/auth/api-keys/{key_id}")
async def revoke_api_key(key_id: str):
    """Revoke an API key. Revoked keys return 401 on use."""
    from ...auth.api_key_service import ApiKeyService
    from ...storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)
    revoked = await service.revoke_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"key_id": key_id, "status": "revoked"}


# =============================================================================
# Agent Card Endpoint (Public Discovery)
# =============================================================================


@app.get("/.well-known/agent.json")
async def agent_card():
    """Serve this seller agent's card for A2A discovery.

    Returns an A2A-protocol-compliant agent card describing this
    seller's capabilities, supported protocols, and inventory types.
    Buyer agents and registries fetch this to discover the seller.
    """
    from ...models.agent_registry import (
        AgentCard,
        AgentAuthentication,
        AgentCapabilities,
        AgentProvider,
        AgentSkill,
    )
    from ...flows import ProductSetupFlow

    settings = _get_api_settings()

    # Discover inventory types from product catalog
    inventory_types = set()
    try:
        flow = ProductSetupFlow()
        await flow.kickoff()
        for product in flow.state.products.values():
            inventory_types.add(product.inventory_type)
    except Exception:
        inventory_types = {"display", "video", "ctv", "native", "mobile_app"}

    card = AgentCard(
        name=settings.seller_agent_name,
        description=(
            "IAB OpenDirect 2.1 compliant seller agent for programmatic "
            "advertising. Supports product discovery, tiered pricing, "
            "proposal evaluation, multi-round negotiation, and deal execution."
        ),
        url=settings.seller_agent_url,
        version="0.1.0",
        provider=AgentProvider(
            name=settings.seller_organization_name,
            url=settings.seller_agent_url,
        ),
        capabilities=AgentCapabilities(
            protocols=["opendirect21", "a2a"],
            streaming=False,
            push_notifications=False,
        ),
        skills=[
            AgentSkill(
                id="discovery",
                name="Inventory Discovery",
                description="Search and browse available inventory, media kits, and packages",
                tags=["inventory", "search", "media-kit"],
            ),
            AgentSkill(
                id="pricing",
                name="Tiered Pricing",
                description="Get pricing based on buyer identity with volume discounts",
                tags=["pricing", "cpm", "negotiation"],
            ),
            AgentSkill(
                id="proposals",
                name="Proposal Evaluation",
                description="Submit and evaluate advertising proposals",
                tags=["proposals", "evaluation", "counter-offers"],
            ),
            AgentSkill(
                id="negotiation",
                name="Multi-Round Negotiation",
                description="Engage in automated price negotiation with strategy-based responses",
                tags=["negotiation", "deals"],
            ),
            AgentSkill(
                id="deals",
                name="Deal Execution",
                description="Generate OpenRTB-compatible deal IDs for DSP activation",
                tags=["deals", "openrtb", "execution"],
            ),
        ],
        authentication=AgentAuthentication(
            schemes=["api_key", "bearer"],
        ),
        inventory_types=sorted(inventory_types),
        supported_deal_types=["pg", "pmp", "preferred_deal", "private_auction"],
    )

    return card.model_dump()


# =============================================================================
# Agent Registry Management Endpoints (Operator-facing)
# =============================================================================


class DiscoverAgentRequest(BaseModel):
    """Request to discover an agent by URL."""
    agent_url: str


class UpdateTrustRequest(BaseModel):
    """Request to update an agent's trust status."""
    trust_status: str  # TrustStatus value
    notes: Optional[str] = None


@app.get("/registry/agents")
async def list_registered_agents(
    agent_type: Optional[str] = None,
    trust_status: Optional[str] = None,
):
    """List agents in the local registry.

    Filterable by agent_type (buyer, seller, tool_provider, data_provider, other)
    and trust_status (unknown, registered, approved, preferred, blocked).
    """
    from ...models.agent_registry import AgentType, TrustStatus

    service = await _get_registry_service()

    at = AgentType(agent_type) if agent_type else None
    ts = TrustStatus(trust_status) if trust_status else None

    agents = await service.list_agents(agent_type=at, trust_status=ts)
    return {
        "agents": [a.model_dump(mode="json") for a in agents],
        "total": len(agents),
    }


@app.get("/registry/agents/{agent_id}")
async def get_registered_agent(agent_id: str):
    """Get details for a specific registered agent."""
    service = await _get_registry_service()
    agent = await service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump(mode="json")


@app.post("/registry/agents/discover")
async def discover_agent(request: DiscoverAgentRequest):
    """Discover an agent by URL.

    Fetches the agent's card from .well-known/agent.json, checks
    all configured registries (AAMP + extras) for verification, and
    registers the agent locally with appropriate trust status.
    """
    service = await _get_registry_service()
    agent, tier = await service.resolve_agent_access(request.agent_url)

    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch agent card from {request.agent_url}",
        )

    return {
        "agent": agent.model_dump(mode="json"),
        "max_access_tier": tier.value if tier else None,
        "is_blocked": agent.is_blocked,
    }


@app.put("/registry/agents/{agent_id}/trust")
async def update_agent_trust(agent_id: str, request: UpdateTrustRequest):
    """Update an agent's trust status.

    Use this to approve, prefer, or block agents. Trust status determines
    the maximum access tier:
    - unknown → PUBLIC (price ranges only)
    - registered → SEAT (exact prices, no negotiation)
    - approved → ADVERTISER (full access)
    - preferred → ADVERTISER + custom pricing rules
    - blocked → 403 rejected, zero data access
    """
    from ...models.agent_registry import TrustStatus, TRUST_TO_TIER_MAP

    try:
        ts = TrustStatus(request.trust_status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trust_status: {request.trust_status}. "
            f"Valid values: {[s.value for s in TrustStatus]}",
        )

    service = await _get_registry_service()
    agent = await service.update_trust_status(agent_id, ts, request.notes)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    tier = TRUST_TO_TIER_MAP.get(ts)
    return {
        "agent_id": agent_id,
        "trust_status": ts.value,
        "max_access_tier": tier.value if tier else None,
        "notes": request.notes,
    }


@app.delete("/registry/agents/{agent_id}")
async def remove_registered_agent(agent_id: str):
    """Remove an agent from the local registry."""
    service = await _get_registry_service()
    removed = await service.remove_agent(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_id, "status": "removed"}


# =============================================================================
# IAB Deals API v1.0 — Quote & Deal Booking Endpoints
# =============================================================================


@app.post("/api/v1/quotes")
async def create_quote(
    request: QuoteRequestModel,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Request a non-binding price quote from the seller.

    The seller evaluates the request against existing pricing rules and
    returns a quote with pricing, terms, and availability. Quotes are
    ephemeral with a 24-hour TTL — no Deal ID is created.
    """
    import uuid
    from datetime import timedelta

    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...flows import ProductSetupFlow
    from ...models.core import DealType
    from ...models.pricing_tiers import TieredPricingConfig
    from ...models.quotes import (
        QuoteAvailability,
        QuotePricing,
        QuoteProductInfo,
        QuoteResponse,
        QuoteStatus,
        QuoteTerms,
    )
    from ...storage.factory import get_storage

    # Map deal type string to enum
    deal_type_map = {
        "PG": DealType.PROGRAMMATIC_GUARANTEED,
        "PD": DealType.PREFERRED_DEAL,
        "PA": DealType.PRIVATE_AUCTION,
    }
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

    # Get product catalog
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff()

    product = setup_flow.state.products.get(request.product_id)
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

    # Resolve buyer identity — API key takes priority over body
    buyer_ident = request.buyer_identity
    context = _build_buyer_context(
        buyer_tier=(
            "advertiser" if (buyer_ident and buyer_ident.advertiser_id) else
            "agency" if (buyer_ident and buyer_ident.agency_id) else
            "seat" if (buyer_ident and buyer_ident.seat_id) else
            "public"
        ),
        agency_id=buyer_ident.agency_id if buyer_ident else None,
        advertiser_id=buyer_ident.advertiser_id if buyer_ident else None,
        seat_id=buyer_ident.seat_id if buyer_ident else None,
        api_key_record=api_key_record,
    )

    # Calculate price via PricingRulesEngine
    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    deal_type_enum = deal_type_map[deal_type_str]
    decision = engine.calculate_price(
        product_id=request.product_id,
        base_price=product.base_cpm,
        buyer_context=context,
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
            buyer_context=context,
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
        buyer_tier=context.effective_tier.value,
        expires_at=expires_at.isoformat() + "Z",
        created_at=now.isoformat() + "Z",
    )

    # Persist with 24-hour TTL
    storage = await get_storage()
    await storage.set_quote(quote_id, quote.model_dump(mode="json"), ttl=86400)

    return quote.model_dump(mode="json")


@app.get("/api/v1/quotes/{quote_id}")
async def get_quote(quote_id: str):
    """Retrieve a previously issued quote.

    Returns 410 Gone if the quote has expired.
    """
    from ...models.quotes import QuoteStatus
    from ...storage.factory import get_storage

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
                detail={"error": "quote_expired", "message": "Quote has expired. Request a new quote."},
            )

    return quote


@app.post("/api/v1/deals")
async def book_deal(
    request: DealBookingRequestModel,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Book a deal from a previously issued quote.

    The seller validates the quote, generates a Deal ID, and returns
    confirmed terms. This is the commit point — the quote becomes bound.
    """
    import uuid
    from datetime import timedelta

    from ...models.quotes import DealBookingResponse, DealBookingStatus, QuoteStatus
    from ...storage.factory import get_storage

    storage = await get_storage()

    # Retrieve the quote
    quote = await storage.get_quote(request.quote_id)
    if not quote:
        raise HTTPException(
            status_code=404,
            detail={"error": "quote_not_found", "message": f"Quote '{request.quote_id}' not found."},
        )

    # Lazy expiry check
    if quote.get("expires_at"):
        expires = datetime.fromisoformat(quote["expires_at"].rstrip("Z"))
        if datetime.utcnow() > expires:
            quote["status"] = QuoteStatus.EXPIRED.value
            await storage.set_quote(request.quote_id, quote, ttl=3600)
            raise HTTPException(
                status_code=410,
                detail={"error": "quote_expired", "message": "Quote has expired. Request a new quote."},
            )

    # Validate status — must be "available"
    if quote.get("status") != QuoteStatus.AVAILABLE.value:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "quote_already_booked",
                "message": f"Quote status is '{quote.get('status')}', expected 'available'.",
            },
        )

    # Generate deal
    now = datetime.utcnow()
    deal_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"
    deal_expires = now + timedelta(days=30)

    from ...models.quotes import QuotePricing, QuoteProductInfo, QuoteTerms

    deal = DealBookingResponse(
        deal_id=deal_id,
        deal_type=quote["deal_type"],
        status=DealBookingStatus.PROPOSED,
        quote_id=request.quote_id,
        product=QuoteProductInfo(**quote["product"]),
        pricing=QuotePricing(**quote["pricing"]),
        terms=QuoteTerms(**quote["terms"]),
        buyer_tier=quote.get("buyer_tier", "public"),
        expires_at=deal_expires.isoformat() + "Z",
        activation_instructions={
            "ttd": f"In The Trade Desk, create a new PMP deal with Deal ID: {deal_id}",
            "dv360": f"In DV360, add deal {deal_id} under Inventory > My Inventory > Deals",
            "xandr": f"In Xandr, navigate to Deals and enter Deal ID: {deal_id}",
        },
        openrtb_params={
            "id": deal_id,
            "bidfloor": quote["pricing"]["final_cpm"],
            "bidfloorcur": "USD",
            "at": 3 if quote["deal_type"] == "PA" else 1,
            "wseat": [],
        },
        created_at=now.isoformat() + "Z",
    )

    deal_data = deal.model_dump(mode="json")

    # Update quote status to "booked" and link deal_id
    quote["status"] = QuoteStatus.BOOKED.value
    quote["deal_id"] = deal_id
    await storage.set_quote(request.quote_id, quote, ttl=86400)

    # Store the deal in deal storage (coexists with proposal-based deals)
    await storage.set_deal(deal_id, deal_data)

    return deal_data


@app.get("/api/v1/deals/{deal_id}")
async def get_deal_by_id(deal_id: str):
    """Get the current status of a deal.

    Performs a lazy expiry check for deals in 'proposed' status.
    """
    from ...storage.factory import get_storage

    storage = await get_storage()
    deal = await storage.get_deal(deal_id)

    if not deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    # Lazy expiry check for proposed deals
    if deal.get("status") == "proposed" and deal.get("expires_at"):
        expires = datetime.fromisoformat(deal["expires_at"].rstrip("Z"))
        if datetime.utcnow() > expires:
            deal["status"] = "expired"
            await storage.set_deal(deal_id, deal)

    return deal


# =============================================================================
# Order Workflow endpoints (seller-cnd)
# =============================================================================


class CreateOrderRequest(BaseModel):
    """Request to create a new order."""
    deal_id: Optional[str] = None
    quote_id: Optional[str] = None
    metadata: Optional[dict] = None


class TransitionOrderRequest(BaseModel):
    """Request to transition an order to a new state."""
    to_status: str
    actor: str = "system"
    reason: str = ""
    metadata: Optional[dict] = None


@app.post("/api/v1/orders")
async def create_order(
    request: CreateOrderRequest,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Create a new order and persist its state machine."""
    from ...storage.factory import get_storage
    from ...models.order_state_machine import OrderStateMachine, OrderStatus

    storage = await get_storage()

    order_id = f"ORD-{uuid.uuid4().hex[:12].upper()}"
    machine = OrderStateMachine(order_id=order_id)

    order_data = machine.to_dict()
    order_data["deal_id"] = request.deal_id
    order_data["quote_id"] = request.quote_id
    order_data["created_at"] = datetime.utcnow().isoformat() + "Z"
    order_data["metadata"] = request.metadata or {}

    await storage.set_order(order_id, order_data)

    return order_data


@app.get("/api/v1/orders")
async def list_orders(
    status: Optional[str] = None,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """List orders, optionally filtered by status."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    filters = {}
    if status:
        filters["status"] = status
    orders = await storage.list_orders(filters if filters else None)
    return {"orders": orders, "count": len(orders)}


@app.get("/api/v1/orders/{order_id}")
async def get_order(
    order_id: str,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Get order current status and audit trail."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    order = await storage.get_order(order_id)

    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    return order


@app.get("/api/v1/orders/{order_id}/history")
async def get_order_history(
    order_id: str,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Get the full transition history for an order."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    order = await storage.get_order(order_id)

    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    audit_log = order.get("audit_log", {})
    transitions = audit_log.get("transitions", [])

    return {
        "order_id": order_id,
        "current_status": order.get("status"),
        "transitions": transitions,
        "transition_count": len(transitions),
    }


@app.post("/api/v1/orders/{order_id}/transition")
async def transition_order(
    order_id: str,
    request: TransitionOrderRequest,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Transition an order to a new state.

    Validates the transition against the state machine rules and
    records the change in the audit log.
    """
    from ...storage.factory import get_storage
    from ...models.order_state_machine import (
        InvalidTransitionError,
        OrderStateMachine,
        OrderStatus,
    )

    storage = await get_storage()
    order = await storage.get_order(order_id)

    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    # Validate target status
    try:
        to_status = OrderStatus(request.to_status)
    except ValueError:
        valid = [s.value for s in OrderStatus]
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_status",
                "message": f"'{request.to_status}' is not a valid order status.",
                "valid_statuses": valid,
            },
        )

    # Restore state machine from stored data
    machine = OrderStateMachine.from_dict(order)

    try:
        record = machine.transition(
            to_status,
            actor=request.actor,
            reason=request.reason,
            metadata=request.metadata,
        )
    except InvalidTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "invalid_transition",
                "message": str(e),
                "current_status": machine.status.value,
                "allowed_transitions": [s.value for s in machine.allowed_transitions()],
            },
        )

    # Persist updated state
    updated = machine.to_dict()
    # Preserve extra fields not managed by the state machine
    for key in ("deal_id", "quote_id", "created_at", "metadata"):
        if key in order:
            updated[key] = order[key]

    await storage.set_order(order_id, updated)

    return {
        "order_id": order_id,
        "status": machine.status.value,
        "transition": record.model_dump(mode="json"),
        "allowed_next": [s.value for s in machine.allowed_transitions()],
    }
