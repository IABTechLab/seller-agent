# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""REST API interface for programmatic access.

Provides endpoints for:
- Product catalog
- Pricing queries
- Proposal submission
- Deal generation
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ad Seller System API",
    description=(
        "IAB OpenDirect 2.1 compliant seller agent for programmatic advertising. "
        "Supports product discovery, tiered pricing, proposal evaluation, "
        "multi-round negotiation, deal execution, order management, and change requests."
    ),
    version="1.0.0",
    contact={"name": "IAB Tech Lab", "url": "https://iabtechlab.com"},
    license_info={"name": "Apache 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
    openapi_tags=[
        {"name": "Core", "description": "Health check and API root"},
        {"name": "Products", "description": "Product catalog browsing"},
        {"name": "Pricing", "description": "Tiered pricing with buyer context"},
        {"name": "Proposals", "description": "Proposal submission and evaluation"},
        {"name": "Deals", "description": "Deal generation from accepted proposals"},
        {"name": "Discovery", "description": "Natural language inventory discovery"},
        {"name": "Events", "description": "Event bus log inspection"},
        {"name": "Approvals", "description": "Human-in-the-loop approval workflow"},
        {"name": "Sessions", "description": "Multi-turn buyer conversation sessions"},
        {"name": "Negotiation", "description": "Multi-round price negotiation"},
        {"name": "Media Kit", "description": "Public media kit and package catalog"},
        {"name": "Packages", "description": "Package management (authenticated/admin)"},
        {"name": "Authentication", "description": "API key lifecycle management"},
        {"name": "Agent Registry", "description": "A2A agent discovery and trust management"},
        {"name": "Quotes", "description": "Non-binding price quotes (IAB Deals API v1.0)"},
        {"name": "Deal Booking", "description": "Quote-to-deal booking (IAB Deals API v1.0)"},
        {"name": "Orders", "description": "Order state machine and lifecycle management"},
        {"name": "Change Requests", "description": "Post-deal modification requests"},
        {"name": "Audit", "description": "Order audit logs and operational reports"},
        {
            "name": "Supply Chain",
            "description": "Supply chain transparency (sellers.json-like self-description)",
        },
        {"name": "Deal Performance", "description": "Deal delivery and performance metrics"},
        {"name": "Bulk Operations", "description": "Batch deal create/update/cancel"},
    ],
)


# =============================================================================
# Lifecycle: start/stop background services
# =============================================================================


@app.on_event("startup")
async def _startup():
    from ...services.inventory_sync_scheduler import start_sync_scheduler

    start_sync_scheduler()

    # Mount MCP server with both transports:
    # - Streamable HTTP at /mcp (current MCP standard, protocol 2025-06-18) — default
    #   for buyer agents and current MCP SDK clients.
    # - HTTP+SSE at /mcp-sse (deprecated but kept for backwards compat with older
    #   Claude Desktop / ChatGPT clients on protocol 2024-11-05).
    try:
        from ..mcp_server import mcp as mcp_server

        app.mount("/mcp", mcp_server.streamable_http_app())
        app.mount("/mcp-sse", mcp_server.sse_app())
        logger.info("MCP server mounted: Streamable HTTP at /mcp, legacy SSE at /mcp-sse/sse")
    except Exception as e:
        logger.warning("MCP server not mounted: %s", e)


@app.on_event("shutdown")
async def _shutdown():
    from ...services.inventory_sync_scheduler import stop_sync_scheduler

    stop_sync_scheduler()


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
    from ...models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity

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


async def _get_registry_service():
    """Create an AgentRegistryService with storage + AAMP client."""
    from ...clients.agent_registry_client import AAMPRegistryClient
    from ...registry import AgentRegistryService
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


@app.get("/", tags=["Core"])
async def root():
    """API root."""
    return {
        "name": "Ad Seller System API",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health", tags=["Core"])
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/products", tags=["Products"])
async def list_products():
    """List all products in the catalog."""
    from ...flows import ProductSetupFlow

    flow = ProductSetupFlow()
    await flow.kickoff_async()

    products = []
    for product in flow.state.products.values():
        products.append(
            {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "inventory_type": product.inventory_type,
                "base_cpm": product.base_cpm,
                "floor_cpm": product.floor_cpm,
                "deal_types": [dt.value for dt in product.supported_deal_types],
            }
        )

    return {"products": products}


@app.get("/products/{product_id}", tags=["Products"])
async def get_product(product_id: str):
    """Get a specific product."""
    from ...flows import ProductSetupFlow

    flow = ProductSetupFlow()
    await flow.kickoff_async()

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


@app.post("/pricing", response_model=PricingResponse, tags=["Pricing"])
async def get_pricing(
    request: PricingRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Get pricing for a product based on buyer context."""
    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...flows import ProductSetupFlow
    from ...models.pricing_tiers import TieredPricingConfig

    # Get products
    flow = ProductSetupFlow()
    await flow.kickoff_async()

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


@app.post("/proposals", response_model=ProposalResponse, tags=["Proposals"])
async def submit_proposal(
    request: ProposalRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Submit a proposal for review."""
    import uuid

    from ...flows import ProductSetupFlow, ProposalHandlingFlow

    # Get products
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff_async()

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


@app.post("/deals", response_model=DealResponse, tags=["Deals"])
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


@app.post("/discovery", tags=["Discovery"])
async def discovery_query(
    request: DiscoveryRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Process a discovery query about inventory."""
    from ...flows import DiscoveryInquiryFlow, ProductSetupFlow

    # Get products
    setup_flow = ProductSetupFlow()
    await setup_flow.kickoff_async()

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


@app.get("/events", tags=["Events"])
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


@app.get("/events/{event_id}", tags=["Events"])
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


@app.get("/approvals", tags=["Approvals"])
async def list_pending_approvals():
    """List all pending approval requests."""
    from ...events.approval import ApprovalGate
    from ...storage.factory import get_storage

    storage = await get_storage()
    gate = ApprovalGate(storage)
    pending = await gate.list_pending()
    return {"approvals": [r.model_dump(mode="json") for r in pending]}


@app.get("/approvals/{approval_id}", tags=["Approvals"])
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


@app.post("/approvals/{approval_id}/decide", tags=["Approvals"])
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


@app.post("/approvals/{approval_id}/resume", tags=["Approvals"])
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
    from datetime import datetime

    from ...events.helpers import emit_event
    from ...events.models import EventType
    from ...flows.proposal_handling_flow import ProposalHandlingFlow, ProposalState
    from ...models.flow_state import ExecutionStatus

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


@app.post("/sessions", tags=["Sessions"])
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
        buyer_tier="advertiser"
        if request.advertiser_id
        else ("agency" if request.agency_id else ("seat" if request.seat_id else "public")),
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


@app.get("/sessions", tags=["Sessions"])
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
        results.append(
            {
                "session_id": s.session_id,
                "status": s.status.value,
                "buyer_pricing_key": s.get_buyer_pricing_key(),
                "message_count": len(s.messages),
                "negotiation_stage": s.negotiation.stage,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
        )

    return {"sessions": results}


@app.get("/sessions/{session_id}", tags=["Sessions"])
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


@app.post("/sessions/{session_id}/messages", tags=["Sessions"])
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


@app.post("/sessions/{session_id}/close", tags=["Sessions"])
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


@app.post("/proposals/{proposal_id}/counter", tags=["Negotiation"])
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
    round_result = neg_engine.evaluate_buyer_offer(history, request.buyer_price, buyer_context)
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


@app.get("/proposals/{proposal_id}/negotiation", tags=["Negotiation"])
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


@app.get("/media-kit", tags=["Media Kit"])
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


@app.get("/media-kit/packages", tags=["Media Kit"])
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


@app.get("/media-kit/packages/{package_id}", tags=["Media Kit"])
async def get_media_kit_package(package_id: str):
    """Get a single package with public view."""
    service = await _get_media_kit_service()
    package = await service.get_package_public(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package.model_dump()


@app.post("/media-kit/search", tags=["Media Kit"])
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


@app.get("/packages", tags=["Packages"])
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


@app.get("/packages/{package_id}", tags=["Packages"])
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


@app.post("/packages", tags=["Packages"])
async def create_package(request: PackageCreateRequest):
    """Create a curated package (Layer 2)."""
    import uuid as _uuid

    from ...events.helpers import emit_event
    from ...events.models import EventType
    from ...models.media_kit import Package, PackageLayer, PackagePlacement, PackageStatus
    from ...storage.factory import get_storage

    storage = await get_storage()

    # Build placements from product_ids
    placements = []
    for pid in request.product_ids:
        prod_data = await storage.get_product(pid)
        if prod_data:
            from ...models.flow_state import ProductDefinition

            prod = ProductDefinition(**prod_data)
            placements.append(
                PackagePlacement(
                    product_id=prod.product_id,
                    product_name=prod.name,
                    ad_formats=request.ad_formats or _default_ad_formats(prod.inventory_type),
                    device_types=request.device_types or _default_device_types(prod.inventory_type),
                )
            )

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


@app.put("/packages/{package_id}", tags=["Packages"])
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


@app.delete("/packages/{package_id}", tags=["Packages"])
async def delete_package(package_id: str):
    """Archive a package (soft delete)."""
    service = await _get_media_kit_service()
    deleted = await service.delete_package(package_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Package not found")
    return {"package_id": package_id, "status": "archived"}


@app.post("/packages/assemble", tags=["Packages"])
async def assemble_package(request: DynamicPackageRequest):
    """Assemble a dynamic package (Layer 3) from product IDs."""
    service = await _get_media_kit_service()
    package = await service.assemble_dynamic_package(request.name, request.product_ids)
    if not package:
        raise HTTPException(status_code=400, detail="No valid products found for assembly")
    return package.model_dump(mode="json")


@app.post("/packages/sync", tags=["Packages"])
async def sync_packages():
    """Trigger ad server inventory sync (Layer 1)."""
    from ...events.helpers import emit_event
    from ...events.models import EventType
    from ...flows import ProductSetupFlow

    flow = ProductSetupFlow()
    await flow.kickoff_async()

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


@app.post("/auth/api-keys", tags=["Authentication"])
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


@app.get("/auth/api-keys", tags=["Authentication"])
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


@app.get("/auth/api-keys/{key_id}", tags=["Authentication"])
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


@app.delete("/auth/api-keys/{key_id}", tags=["Authentication"])
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


@app.get("/.well-known/agent.json", tags=["Agent Registry"])
async def agent_card():
    """Serve this seller agent's card for A2A discovery.

    Returns an A2A-protocol-compliant agent card describing this
    seller's capabilities, supported protocols, and inventory types.
    Buyer agents and registries fetch this to discover the seller.
    """
    from ...flows import ProductSetupFlow
    from ...models.agent_registry import (
        AgentAuthentication,
        AgentCapabilities,
        AgentCard,
        AgentProvider,
        AgentSkill,
    )

    settings = _get_api_settings()

    # Discover inventory types from product catalog
    inventory_types = set()
    try:
        flow = ProductSetupFlow()
        await flow.kickoff_async()
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


@app.get("/registry/agents", tags=["Agent Registry"])
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


@app.get("/registry/agents/{agent_id}", tags=["Agent Registry"])
async def get_registered_agent(agent_id: str):
    """Get details for a specific registered agent."""
    service = await _get_registry_service()
    agent = await service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump(mode="json")


@app.post("/registry/agents/discover", tags=["Agent Registry"])
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


@app.put("/registry/agents/{agent_id}/trust", tags=["Agent Registry"])
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
    from ...models.agent_registry import TRUST_TO_TIER_MAP, TrustStatus

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


@app.delete("/registry/agents/{agent_id}", tags=["Agent Registry"])
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


@app.post("/api/v1/quotes", tags=["Quotes"])
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
    await setup_flow.kickoff_async()

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


@app.get("/api/v1/quotes/{quote_id}", tags=["Quotes"])
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
                detail={
                    "error": "quote_expired",
                    "message": "Quote has expired. Request a new quote.",
                },
            )

    return quote


@app.post("/api/v1/deals", tags=["Deal Booking"])
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
            detail={
                "error": "quote_not_found",
                "message": f"Quote '{request.quote_id}' not found.",
            },
        )

    # Lazy expiry check
    if quote.get("expires_at"):
        expires = datetime.fromisoformat(quote["expires_at"].rstrip("Z"))
        if datetime.utcnow() > expires:
            quote["status"] = QuoteStatus.EXPIRED.value
            await storage.set_quote(request.quote_id, quote, ttl=3600)
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "quote_expired",
                    "message": "Quote has expired. Request a new quote.",
                },
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


@app.get("/api/v1/deals/{deal_id}", tags=["Deal Booking"])
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


@app.post("/api/v1/orders", tags=["Orders"])
async def create_order(
    request: CreateOrderRequest,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Create a new order and persist its state machine."""
    from ...models.order_state_machine import OrderStateMachine
    from ...storage.factory import get_storage

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


@app.get("/api/v1/orders", tags=["Orders"])
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


@app.get("/api/v1/orders/report", tags=["Orders", "Audit"])
async def get_orders_report(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Summary report across all orders.

    Returns counts by status, transition frequency by actor type,
    and average time-in-state metrics.
    """
    from ...storage.factory import get_storage

    storage = await get_storage()
    all_orders = await storage.list_orders()

    # Filter by date range if specified
    if from_date or to_date:
        filtered_orders = []
        for o in all_orders:
            created = o.get("created_at", "")
            if from_date and created < from_date:
                continue
            if to_date and created > to_date + "T23:59:59":
                continue
            filtered_orders.append(o)
        all_orders = filtered_orders

    # Counts by status
    status_counts: dict[str, int] = {}
    for o in all_orders:
        s = o.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Transition frequency by actor type
    actor_counts: dict[str, int] = {}
    total_transitions = 0
    for o in all_orders:
        transitions = o.get("audit_log", {}).get("transitions", [])
        total_transitions += len(transitions)
        for t in transitions:
            actor_type = t.get("actor", "system").split(":")[0]
            actor_counts[actor_type] = actor_counts.get(actor_type, 0) + 1

    # Average transitions per order
    order_count = len(all_orders)
    avg_transitions = round(total_transitions / order_count, 1) if order_count else 0

    # Change request summary
    all_crs = await storage.list_change_requests()
    cr_status_counts: dict[str, int] = {}
    for cr in all_crs:
        s = cr.get("status", "unknown")
        cr_status_counts[s] = cr_status_counts.get(s, 0) + 1

    return {
        "total_orders": order_count,
        "status_counts": status_counts,
        "total_transitions": total_transitions,
        "avg_transitions_per_order": avg_transitions,
        "actor_type_counts": actor_counts,
        "change_requests": {
            "total": len(all_crs),
            "by_status": cr_status_counts,
        },
    }


@app.get("/api/v1/orders/{order_id}", tags=["Orders"])
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


@app.get("/api/v1/orders/{order_id}/history", tags=["Orders"])
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


@app.post("/api/v1/orders/{order_id}/transition", tags=["Orders"])
async def transition_order(
    order_id: str,
    request: TransitionOrderRequest,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Transition an order to a new state.

    Validates the transition against the state machine rules and
    records the change in the audit log.
    """
    from ...models.order_state_machine import (
        InvalidTransitionError,
        OrderStateMachine,
        OrderStatus,
    )
    from ...storage.factory import get_storage

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


# =============================================================================
# Change Request endpoints (seller-ju5)
# =============================================================================


class FieldDiffModel(BaseModel):
    field: str
    old_value: Any = None
    new_value: Any = None


class CreateChangeRequestModel(BaseModel):
    """Request to create a change request for an order."""

    order_id: str
    change_type: str
    diffs: list[FieldDiffModel] = []
    proposed_values: Optional[dict] = None
    reason: str = ""
    requested_by: str = "system"


class ReviewChangeRequestModel(BaseModel):
    """Approve or reject a change request."""

    decision: str  # "approve" or "reject"
    decided_by: str = "system"
    reason: str = ""


@app.post("/api/v1/change-requests", tags=["Change Requests"])
async def create_change_request(
    request: CreateChangeRequestModel,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Submit a change request for an existing order.

    Validates the change against the current order state, classifies
    severity, and routes to approval if needed.
    """
    from ...models.change_request import (
        ChangeRequest,
        ChangeRequestStatus,
        ChangeSeverity,
        ChangeType,
        FieldDiff,
        classify_severity,
        validate_change_request,
    )
    from ...storage.factory import get_storage

    storage = await get_storage()

    # Validate change_type
    try:
        change_type = ChangeType(request.change_type)
    except ValueError:
        valid = [t.value for t in ChangeType]
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_change_type",
                "message": f"'{request.change_type}' is not a valid change type.",
                "valid_types": valid,
            },
        )

    # Verify order exists
    order = await storage.get_order(request.order_id)
    if not order:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "order_not_found",
                "message": f"Order '{request.order_id}' not found.",
            },
        )

    # Build diffs
    diffs = [
        FieldDiff(field=d.field, old_value=d.old_value, new_value=d.new_value)
        for d in request.diffs
    ]

    # Classify severity
    severity = classify_severity(change_type, diffs)

    # Create the change request
    cr = ChangeRequest(
        order_id=request.order_id,
        deal_id=order.get("deal_id", ""),
        change_type=change_type,
        severity=severity,
        requested_by=request.requested_by,
        reason=request.reason,
        diffs=diffs,
        proposed_values=request.proposed_values or {},
        rollback_snapshot=order.copy(),
    )

    # Validate against order state
    errors = validate_change_request(cr, order)
    if errors:
        cr.status = ChangeRequestStatus.FAILED
        cr.validation_errors = errors
        cr_data = cr.model_dump(mode="json")
        await storage.set_change_request(cr.change_request_id, cr_data)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_failed",
                "change_request_id": cr.change_request_id,
                "validation_errors": errors,
            },
        )

    # Auto-approve minor changes, route material/critical to approval
    if severity == ChangeSeverity.MINOR:
        cr.status = ChangeRequestStatus.APPROVED
        cr.approved_by = "system:auto-approve"
        cr.approved_at = datetime.utcnow()
    else:
        cr.status = ChangeRequestStatus.PENDING_APPROVAL

    cr_data = cr.model_dump(mode="json")
    await storage.set_change_request(cr.change_request_id, cr_data)

    return cr_data


@app.get("/api/v1/change-requests", tags=["Change Requests"])
async def list_change_requests(
    order_id: Optional[str] = None,
    status: Optional[str] = None,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """List change requests, optionally filtered by order or status."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    filters = {}
    if order_id:
        filters["order_id"] = order_id
    if status:
        filters["status"] = status
    results = await storage.list_change_requests(filters if filters else None)
    return {"change_requests": results, "count": len(results)}


@app.get("/api/v1/change-requests/{cr_id}", tags=["Change Requests"])
async def get_change_request(
    cr_id: str,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Get a change request by ID."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    cr = await storage.get_change_request(cr_id)
    if not cr:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "change_request_not_found",
                "message": f"Change request '{cr_id}' not found.",
            },
        )
    return cr


@app.post("/api/v1/change-requests/{cr_id}/review", tags=["Change Requests"])
async def review_change_request(
    cr_id: str,
    request: ReviewChangeRequestModel,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Approve or reject a pending change request."""
    from ...models.change_request import ChangeRequestStatus
    from ...storage.factory import get_storage

    storage = await get_storage()
    cr = await storage.get_change_request(cr_id)

    if not cr:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "change_request_not_found",
                "message": f"Change request '{cr_id}' not found.",
            },
        )

    if cr.get("status") != ChangeRequestStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_pending_approval",
                "message": f"Change request is in '{cr.get('status')}' status, not 'pending_approval'.",
            },
        )

    if request.decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_decision",
                "message": "Decision must be 'approve' or 'reject'.",
            },
        )

    now = datetime.utcnow().isoformat() + "Z"

    if request.decision == "approve":
        cr["status"] = ChangeRequestStatus.APPROVED.value
        cr["approved_by"] = request.decided_by
        cr["approved_at"] = now
    else:
        cr["status"] = ChangeRequestStatus.REJECTED.value
        cr["rejection_reason"] = request.reason
        cr["approved_by"] = request.decided_by
        cr["approved_at"] = now

    await storage.set_change_request(cr_id, cr)
    return cr


@app.post("/api/v1/change-requests/{cr_id}/apply", tags=["Change Requests"])
async def apply_change_request(
    cr_id: str,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Apply an approved change request to the order.

    Updates the order with the proposed values from the change request.
    """
    from ...models.change_request import ChangeRequestStatus
    from ...storage.factory import get_storage

    storage = await get_storage()
    cr = await storage.get_change_request(cr_id)

    if not cr:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "change_request_not_found",
                "message": f"Change request '{cr_id}' not found.",
            },
        )

    if cr.get("status") != ChangeRequestStatus.APPROVED.value:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_approved",
                "message": f"Change request is in '{cr.get('status')}' status, not 'approved'.",
            },
        )

    # Load the order
    order_id = cr.get("order_id")
    order = await storage.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    # Apply proposed values to order metadata
    proposed = cr.get("proposed_values", {})
    order_meta = order.get("metadata", {})
    order_meta.update(proposed)
    order["metadata"] = order_meta

    # Apply diffs directly to order where applicable
    for diff in cr.get("diffs", []):
        field = diff.get("field", "")
        new_val = diff.get("new_value")
        if field and new_val is not None:
            order_meta[f"_changed_{field}"] = new_val

    await storage.set_order(order_id, order)

    # Mark change request as applied
    cr["status"] = ChangeRequestStatus.APPLIED.value
    cr["applied_at"] = datetime.utcnow().isoformat() + "Z"
    cr["applied_by"] = "system"
    await storage.set_change_request(cr_id, cr)

    return {
        "change_request_id": cr_id,
        "status": "applied",
        "order_id": order_id,
    }


# =============================================================================
# Order Audit & Reporting endpoints (seller-5ks)
# =============================================================================


@app.get("/api/v1/orders/{order_id}/audit", tags=["Audit"])
async def get_order_audit(
    order_id: str,
    actor: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    _auth: None = Depends(_get_optional_api_key_record),
):
    """Detailed audit log for an order with optional filters.

    Filters:
      - actor: filter transitions by actor (exact or prefix match)
      - from_date: ISO date, only transitions on or after this date
      - to_date: ISO date, only transitions on or before this date
    """
    from ...storage.factory import get_storage

    storage = await get_storage()
    order = await storage.get_order(order_id)

    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    transitions = order.get("audit_log", {}).get("transitions", [])

    # Also include change requests for this order
    change_requests = await storage.list_change_requests({"order_id": order_id})

    # Filter transitions
    filtered = []
    for t in transitions:
        if actor and not t.get("actor", "").startswith(actor):
            continue
        ts = t.get("timestamp", "")
        if from_date and ts < from_date:
            continue
        if to_date and ts > to_date + "T23:59:59":
            continue
        filtered.append(t)

    return {
        "order_id": order_id,
        "current_status": order.get("status"),
        "created_at": order.get("created_at"),
        "transitions": filtered,
        "transition_count": len(filtered),
        "change_requests": change_requests,
        "change_request_count": len(change_requests),
    }


# =============================================================================
# Template-Based Deal Creation (Deal Library Phase 4)
# =============================================================================


class DealFromTemplateRequest(BaseModel):
    """Request model for POST /api/v1/deals/from-template."""

    deal_type: str  # PG, PD, PA
    product_id: str
    impressions: Optional[int] = None
    max_cpm: Optional[float] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    buyer_identity: Optional[QuoteBuyerIdentityModel] = None
    notes: Optional[str] = None


class DealFromTemplateResponse(BaseModel):
    """Response for template-based deal creation."""

    deal_id: str
    status: str
    deal_type: str
    product_id: str
    actual_price_cpm: float
    currency: str = "USD"
    impressions: Optional[int] = None
    flight_start: str
    flight_end: str
    buyer_tier: str
    activation_instructions: dict[str, str]
    schain: Optional[dict[str, Any]] = None
    created_at: str


class DealRejectionDetail(BaseModel):
    """Rejection detail when max_cpm is below seller floor."""

    error: str
    message: str
    seller_minimum_cpm: float
    buyer_max_cpm: float
    product_id: str
    deal_type: str


@app.post(
    "/api/v1/deals/from-template",
    tags=["Deal Booking"],
    response_model=DealFromTemplateResponse,
    status_code=201,
)
async def create_deal_from_template(
    request: DealFromTemplateRequest,
    api_key_record=Depends(_get_optional_api_key_record),
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
    from datetime import timedelta

    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...flows import ProductSetupFlow
    from ...models.core import DealType
    from ...models.pricing_tiers import TieredPricingConfig
    from ...models.quotes import DealBookingStatus
    from ...storage.factory import get_storage

    # Require authentication
    if not api_key_record:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authentication_required",
                "message": "API key required for deal creation.",
            },
        )

    # Validate deal type
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

    # PG requires impressions
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
    await setup_flow.kickoff_async()

    product = setup_flow.state.products.get(request.product_id)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "product_not_found",
                "message": f"Product '{request.product_id}' not found in catalog.",
            },
        )

    # Resolve buyer context from API key + body
    buyer_ident = request.buyer_identity
    context = _build_buyer_context(
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

    # Calculate price
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

    final_cpm = decision.final_price

    # Check max_cpm against floor
    if request.max_cpm is not None and request.max_cpm < final_cpm:
        raise HTTPException(
            status_code=422,
            detail=DealRejectionDetail(
                error="below_floor_price",
                message=f"Buyer max CPM ${request.max_cpm:.2f} is below seller minimum ${final_cpm:.2f}.",
                seller_minimum_cpm=final_cpm,
                buyer_max_cpm=request.max_cpm,
                product_id=request.product_id,
                deal_type=deal_type_str,
            ).model_dump(),
        )

    # Auto-book: generate deal directly (skip separate quote step)
    now = datetime.utcnow()
    deal_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"

    flight_start = request.flight_start or now.strftime("%Y-%m-%d")
    flight_end = request.flight_end or (now + timedelta(days=30)).strftime("%Y-%m-%d")

    deal_data = {
        "deal_id": deal_id,
        "deal_type": deal_type_str,
        "status": DealBookingStatus.CONFIRMED.value,
        "product_id": request.product_id,
        "actual_price_cpm": final_cpm,
        "currency": "USD",
        "impressions": request.impressions,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "buyer_tier": context.effective_tier.value,
        "notes": request.notes,
        "created_at": now.isoformat() + "Z",
        "activation_instructions": {
            "ttd": f"In The Trade Desk, create a new PMP deal with Deal ID: {deal_id}",
            "dv360": f"In DV360, add deal {deal_id} under Inventory > My Inventory > Deals",
            "amazon": f"In Amazon DSP, navigate to Supply > Deals and add Deal ID: {deal_id}",
            "xandr": f"In Xandr, navigate to Deals and enter Deal ID: {deal_id}",
        },
    }

    # Build schain for the deal response
    from ...config import get_settings as _get_settings
    from ...models.supply_chain import build_schain_from_sellers_json, load_sellers_json

    _settings = _get_settings()
    _sellers_json_path = getattr(_settings, "sellers_json_path", None)
    _sellers_json = load_sellers_json(_sellers_json_path) if _sellers_json_path else None
    schain_data = None
    if _sellers_json:
        _seller_id = getattr(_settings, "seller_organization_id", "default")
        schain_obj = build_schain_from_sellers_json(_sellers_json, _seller_id)
        schain_data = schain_obj.model_dump()
        deal_data["schain"] = schain_data
    else:
        _seller_domain = getattr(_settings, "seller_domain", "demo-publisher.example.com")
        _seller_org_name = getattr(_settings, "seller_organization_name", "Demo Publisher")
        schain_data = {
            "ver": "1.0",
            "complete": 1,
            "nodes": [
                {
                    "asi": _seller_domain,
                    "sid": "default",
                    "hp": 1,
                    "name": _seller_org_name,
                    "domain": _seller_domain,
                }
            ],
        }
        deal_data["schain"] = schain_data

    storage = await get_storage()
    await storage.set_deal(deal_id, deal_data)

    return DealFromTemplateResponse(
        deal_id=deal_id,
        status="confirmed",
        deal_type=deal_type_str,
        product_id=request.product_id,
        actual_price_cpm=final_cpm,
        impressions=request.impressions,
        flight_start=flight_start,
        flight_end=flight_end,
        buyer_tier=context.effective_tier.value,
        activation_instructions=deal_data["activation_instructions"],
        schain=schain_data,
        created_at=deal_data["created_at"],
    )


# =============================================================================
# Supply Chain Transparency (Deal Library Phase 4)
# =============================================================================


class SupplyChainNodeModel(BaseModel):
    """A node in the supply chain (sellers.json format)."""

    asi: str  # Account System Identifier (domain)
    sid: str  # Seller ID within the exchange
    name: str
    domain: str
    seller_type: str  # PUBLISHER, INTERMEDIARY, BOTH
    is_direct: bool
    comment: Optional[str] = None


class SupplyChainResponse(BaseModel):
    """Supply chain transparency response (sellers.json-like self-description)."""

    seller_id: str
    seller_name: str
    seller_type: str  # PUBLISHER, INTERMEDIARY, BOTH
    domain: str
    is_direct: bool
    supported_deal_types: list[str]
    contact_email: Optional[str] = None
    schain: list[SupplyChainNodeModel]
    version: str = "1.0"


@app.get("/api/v1/supply-chain", tags=["Supply Chain"], response_model=SupplyChainResponse)
async def get_supply_chain():
    """Return sellers.json-based self-description of this seller instance.

    If SELLERS_JSON_PATH is configured, parses the real sellers.json file
    per IAB spec. Otherwise returns a default single-node chain.
    Also includes an OpenRTB-compatible schain object.
    """
    from ...config import get_settings
    from ...models.supply_chain import build_schain_from_sellers_json, load_sellers_json

    settings = get_settings()
    seller_domain = getattr(settings, "seller_domain", "demo-publisher.example.com")
    seller_name = getattr(settings, "seller_name", "Demo Publisher")
    seller_id = getattr(settings, "seller_organization_id", "default")
    sellers_json_path = getattr(settings, "sellers_json_path", None)

    sellers_json = load_sellers_json(sellers_json_path)

    if sellers_json:
        # Build from real sellers.json
        primary = next(
            (s for s in sellers_json.sellers if s.seller_id == seller_id),
            sellers_json.sellers[0] if sellers_json.sellers else None,
        )

        schain_obj = build_schain_from_sellers_json(sellers_json, seller_id)
        schain_nodes = [
            SupplyChainNodeModel(
                asi=node.asi,
                sid=node.sid,
                name=node.name or "",
                domain=node.domain or node.asi,
                seller_type=(
                    next(
                        (s.seller_type for s in sellers_json.sellers if s.seller_id == node.sid),
                        "PUBLISHER",
                    )
                ),
                is_direct=(node == schain_obj.nodes[0]) if schain_obj.nodes else False,
                comment=next(
                    (s.comment for s in sellers_json.sellers if s.seller_id == node.sid), None
                ),
            )
            for node in schain_obj.nodes
        ]

        return SupplyChainResponse(
            seller_id=primary.seller_id if primary else seller_id,
            seller_name=primary.name if primary else seller_name,
            seller_type=primary.seller_type if primary else "PUBLISHER",
            domain=primary.domain if primary else seller_domain,
            is_direct=primary.seller_type == "PUBLISHER" if primary else True,
            supported_deal_types=["programmatic_guaranteed", "preferred_deal", "private_auction"],
            contact_email=sellers_json.contact_email,
            schain=schain_nodes,
            version=sellers_json.version,
        )

    # Default: single-node chain (no sellers.json configured)
    return SupplyChainResponse(
        seller_id=seller_id,
        seller_name=seller_name,
        seller_type="PUBLISHER",
        domain=seller_domain,
        is_direct=True,
        supported_deal_types=["programmatic_guaranteed", "preferred_deal", "private_auction"],
        schain=[
            SupplyChainNodeModel(
                asi=seller_domain,
                sid=seller_id,
                name=seller_name,
                domain=seller_domain,
                seller_type="PUBLISHER",
                is_direct=True,
                comment="Direct seller — no intermediaries",
            ),
        ],
    )


# =============================================================================
# Deal Performance Data (Deal Library Phase 5)
# =============================================================================


class DealPerformanceResponse(BaseModel):
    """Deal delivery and performance metrics."""

    deal_id: str
    impressions_available: int
    impressions_served: int
    fill_rate: float
    win_rate: float
    avg_cpm_actual: float
    delivery_pacing: str  # ahead, on_track, behind, not_started
    last_updated: str


@app.get("/api/v1/deals/{deal_id}/performance", tags=["Deal Performance"])
async def get_deal_performance(deal_id: str):
    """Return delivery stats for a deal.

    Provides performance feedback for buyer SPO (Supply Path Optimization).
    Returns placeholder/mock stats initially — real ad server integration
    comes in a future phase.
    """
    from ...storage.factory import get_storage

    storage = await get_storage()
    deal = await storage.get_deal(deal_id)

    if not deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    # Placeholder performance data — real stats come from ad server integration
    now = datetime.utcnow().isoformat() + "Z"
    return DealPerformanceResponse(
        deal_id=deal_id,
        impressions_available=1000000,
        impressions_served=0,
        fill_rate=0.0,
        win_rate=0.0,
        avg_cpm_actual=0.0,
        delivery_pacing="not_started",
        last_updated=now,
    )


# =============================================================================
# Bulk Deal Operations (Deal Library Phase 5)
# =============================================================================


class BulkDealOperation(BaseModel):
    """A single operation in a bulk deal request."""

    action: str  # create, update, cancel
    deal_id: Optional[str] = None  # required for update/cancel
    quote_id: Optional[str] = None  # required for create
    buyer_identity: Optional[QuoteBuyerIdentityModel] = None
    notes: Optional[str] = None


class BulkDealRequest(BaseModel):
    """Batch of deal operations."""

    operations: list[BulkDealOperation]


class BulkDealOperationResult(BaseModel):
    """Result of a single bulk operation."""

    index: int
    action: str
    success: bool
    deal_id: Optional[str] = None
    error: Optional[str] = None


class BulkDealResponse(BaseModel):
    """Batch results for bulk deal operations."""

    total: int
    succeeded: int
    failed: int
    results: list[BulkDealOperationResult]


@app.post("/api/v1/deals/bulk", tags=["Bulk Operations"], response_model=BulkDealResponse)
async def bulk_deal_operations(
    request: BulkDealRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Process a batch of deal operations (create/update/cancel).

    Enables the Deal Library buyer agent to efficiently manage multiple
    deals in a single request. Each operation is processed independently
    and returns per-operation success/failure.
    """
    import uuid as uuid_mod

    from ...models.quotes import DealBookingStatus, QuoteStatus
    from ...storage.factory import get_storage

    storage = await get_storage()
    results: list[BulkDealOperationResult] = []

    for i, op in enumerate(request.operations):
        try:
            if op.action == "create":
                if not op.quote_id:
                    results.append(
                        BulkDealOperationResult(
                            index=i,
                            action="create",
                            success=False,
                            error="quote_id is required for create",
                        )
                    )
                    continue

                quote = await storage.get_quote(op.quote_id)
                if not quote:
                    results.append(
                        BulkDealOperationResult(
                            index=i,
                            action="create",
                            success=False,
                            error=f"Quote '{op.quote_id}' not found",
                        )
                    )
                    continue

                if quote.get("status") != QuoteStatus.AVAILABLE.value:
                    results.append(
                        BulkDealOperationResult(
                            index=i,
                            action="create",
                            success=False,
                            error=f"Quote status is '{quote.get('status')}', expected 'available'",
                        )
                    )
                    continue

                # Generate deal
                now = datetime.utcnow()
                deal_id = f"DEMO-{uuid_mod.uuid4().hex[:12].upper()}"

                deal_data = {
                    "deal_id": deal_id,
                    "quote_id": op.quote_id,
                    "status": DealBookingStatus.CONFIRMED.value,
                    "created_at": now.isoformat() + "Z",
                    "notes": op.notes,
                }
                await storage.set_deal(deal_id, deal_data)

                # Mark quote as booked
                quote["status"] = QuoteStatus.BOOKED.value
                await storage.set_quote(op.quote_id, quote)

                results.append(
                    BulkDealOperationResult(
                        index=i,
                        action="create",
                        success=True,
                        deal_id=deal_id,
                    )
                )

            elif op.action == "cancel":
                if not op.deal_id:
                    results.append(
                        BulkDealOperationResult(
                            index=i,
                            action="cancel",
                            success=False,
                            error="deal_id is required for cancel",
                        )
                    )
                    continue

                deal = await storage.get_deal(op.deal_id)
                if not deal:
                    results.append(
                        BulkDealOperationResult(
                            index=i,
                            action="cancel",
                            success=False,
                            error=f"Deal '{op.deal_id}' not found",
                        )
                    )
                    continue

                deal["status"] = "cancelled"
                deal["cancelled_at"] = datetime.utcnow().isoformat() + "Z"
                deal["cancel_reason"] = op.notes or "Cancelled via bulk operation"
                await storage.set_deal(op.deal_id, deal)

                results.append(
                    BulkDealOperationResult(
                        index=i,
                        action="cancel",
                        success=True,
                        deal_id=op.deal_id,
                    )
                )

            elif op.action == "update":
                if not op.deal_id:
                    results.append(
                        BulkDealOperationResult(
                            index=i,
                            action="update",
                            success=False,
                            error="deal_id is required for update",
                        )
                    )
                    continue

                deal = await storage.get_deal(op.deal_id)
                if not deal:
                    results.append(
                        BulkDealOperationResult(
                            index=i,
                            action="update",
                            success=False,
                            error=f"Deal '{op.deal_id}' not found",
                        )
                    )
                    continue

                if op.notes:
                    deal["notes"] = op.notes
                deal["updated_at"] = datetime.utcnow().isoformat() + "Z"
                await storage.set_deal(op.deal_id, deal)

                results.append(
                    BulkDealOperationResult(
                        index=i,
                        action="update",
                        success=True,
                        deal_id=op.deal_id,
                    )
                )

            else:
                results.append(
                    BulkDealOperationResult(
                        index=i,
                        action=op.action,
                        success=False,
                        error=f"Unknown action '{op.action}'. Must be create, update, or cancel.",
                    )
                )

        except Exception as e:
            results.append(
                BulkDealOperationResult(
                    index=i,
                    action=op.action,
                    success=False,
                    error=str(e),
                )
            )

    succeeded = sum(1 for r in results if r.success)
    return BulkDealResponse(
        total=len(request.operations),
        succeeded=succeeded,
        failed=len(request.operations) - succeeded,
        results=results,
    )


# =============================================================================
# Inventory Sync Scheduler
# =============================================================================


# =============================================================================
# Inventory Type Mapping / Override
# =============================================================================


class InventoryTypeOverride(BaseModel):
    """Override inventory type classification for a product."""

    product_id: str
    inventory_type: str  # display, video, ctv, mobile_app, native, audio
    reason: Optional[str] = None


class InventoryTypeOverrideResponse(BaseModel):
    """Response confirming the override."""

    product_id: str
    previous_type: Optional[str] = None
    new_type: str
    applied_at: str


@app.post("/api/v1/products/{product_id}/inventory-type", tags=["Products"])
async def override_inventory_type(
    product_id: str,
    request: InventoryTypeOverride,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Override the auto-detected inventory type for a product.

    Publishers can correct misclassified inventory types from ad server sync
    or apply custom categorization. The override persists across future syncs.
    """
    from ...storage.factory import get_storage

    storage = await get_storage()

    # Get current product data
    product_data = await storage.get(f"product:{product_id}")
    previous_type = None

    if product_data:
        previous_type = product_data.get("inventory_type")
        product_data["inventory_type"] = request.inventory_type
        product_data["inventory_type_override"] = True
        product_data["inventory_type_override_reason"] = request.reason
        await storage.set(f"product:{product_id}", product_data)
    else:
        # Create override record even if product not yet synced
        override_data = {
            "product_id": product_id,
            "inventory_type": request.inventory_type,
            "inventory_type_override": True,
            "inventory_type_override_reason": request.reason,
        }
        await storage.set(f"product:{product_id}", override_data)

    now = datetime.utcnow().isoformat() + "Z"

    # Store override in a separate key for persistence across syncs
    await storage.set(
        f"inventory_override:{product_id}",
        {
            "product_id": product_id,
            "inventory_type": request.inventory_type,
            "reason": request.reason,
            "applied_at": now,
        },
    )

    return InventoryTypeOverrideResponse(
        product_id=product_id,
        previous_type=previous_type,
        new_type=request.inventory_type,
        applied_at=now,
    )


@app.get("/api/v1/products/{product_id}/inventory-type", tags=["Products"])
async def get_inventory_type_override(product_id: str):
    """Get the current inventory type override for a product, if any."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    override = await storage.get(f"inventory_override:{product_id}")

    if not override:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "no_override",
                "message": f"No inventory type override for product '{product_id}'.",
            },
        )

    return override


@app.delete("/api/v1/products/{product_id}/inventory-type", tags=["Products"])
async def delete_inventory_type_override(product_id: str):
    """Remove an inventory type override, reverting to auto-detected type."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    override = await storage.get(f"inventory_override:{product_id}")

    if not override:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "no_override",
                "message": f"No inventory type override for product '{product_id}'.",
            },
        )

    await storage.delete(f"inventory_override:{product_id}")

    # Remove override flag from product
    product_data = await storage.get(f"product:{product_id}")
    if product_data:
        product_data.pop("inventory_type_override", None)
        product_data.pop("inventory_type_override_reason", None)
        await storage.set(f"product:{product_id}", product_data)

    return {"status": "removed", "product_id": product_id}


# =============================================================================
# Rate Card Management
# =============================================================================


class RateCardEntry(BaseModel):
    """Rate card entry mapping inventory type to base CPM."""

    inventory_type: str  # display, video, ctv, mobile_app, native, audio
    base_cpm: float
    currency: str = "USD"
    effective_date: Optional[str] = None
    notes: Optional[str] = None


class RateCardResponse(BaseModel):
    """Full rate card for the seller."""

    entries: list[RateCardEntry]
    updated_at: str


@app.get("/api/v1/rate-card", tags=["Pricing"])
async def get_rate_card():
    """Get the current rate card (base CPMs by inventory type).

    The rate card drives floor pricing during inventory sync and
    deal creation. Can be updated via PUT to reflect ad server rate cards.
    """
    from ...storage.factory import get_storage

    storage = await get_storage()
    rate_card = await storage.get("rate_card:current")

    if not rate_card:
        # Return default rate card
        return RateCardResponse(
            entries=[
                RateCardEntry(inventory_type="display", base_cpm=12.0),
                RateCardEntry(inventory_type="video", base_cpm=25.0),
                RateCardEntry(inventory_type="ctv", base_cpm=35.0),
                RateCardEntry(inventory_type="mobile_app", base_cpm=18.0),
                RateCardEntry(inventory_type="native", base_cpm=10.0),
                RateCardEntry(inventory_type="audio", base_cpm=15.0),
            ],
            updated_at="default",
        )

    return rate_card


@app.put("/api/v1/rate-card", tags=["Pricing"])
async def update_rate_card(entries: list[RateCardEntry]):
    """Update the rate card with current base CPMs from ad server.

    Publishers should update this when their ad server rate cards change.
    The pricing engine uses these values as base prices before applying
    tier discounts and volume adjustments.
    """
    from ...storage.factory import get_storage

    storage = await get_storage()
    now = datetime.utcnow().isoformat() + "Z"

    rate_card = {
        "entries": [e.model_dump() for e in entries],
        "updated_at": now,
    }
    await storage.set("rate_card:current", rate_card)

    return RateCardResponse(entries=entries, updated_at=now)


# =============================================================================
# Inventory Sync Status & Trigger
# =============================================================================


@app.get("/api/v1/inventory-sync/status", tags=["Core"])
async def get_inventory_sync_status():
    """Get the current status of the periodic inventory sync scheduler."""
    from ...services.inventory_sync_scheduler import get_sync_status

    return get_sync_status()


@app.post("/api/v1/inventory-sync/trigger", tags=["Core"])
async def trigger_inventory_sync(
    incremental: bool = False,
):
    """Manually trigger an inventory sync.

    Args:
        incremental: If true, only sync items changed since last sync
            (based on stored sync watermark). Full sync if false or no
            previous watermark exists.
    """
    from ...config import get_settings
    from ...services.inventory_sync_scheduler import _run_sync
    from ...storage.factory import get_storage

    settings = get_settings()
    storage = await get_storage()

    since_timestamp = None
    if incremental:
        watermark = await storage.get("sync_watermark:inventory")
        if watermark:
            since_timestamp = watermark.get("last_sync_at")

    result = await _run_sync(include_archived=settings.inventory_sync_include_archived)

    # Store sync watermark for incremental support
    now = datetime.utcnow().isoformat() + "Z"
    await storage.set(
        "sync_watermark:inventory",
        {
            "last_sync_at": now,
            "was_incremental": incremental,
            "since_timestamp": since_timestamp,
        },
    )

    result["incremental"] = incremental
    result["since_timestamp"] = since_timestamp
    return result


# =============================================================================
# Deal Export Formats for DSP Connectors (Deal Library Phase 4)
# =============================================================================


@app.get("/api/v1/deals/export", tags=["Deal Booking"])
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
    """
    from ...storage.factory import get_storage

    storage = await get_storage()

    # Collect all deals (scan deal:* keys)
    all_deals = []
    # Storage doesn't have a list_deals method, so we track deal IDs
    deal_index = await storage.get("deal_index") or {"deal_ids": []}

    for deal_id in deal_index.get("deal_ids", []):
        deal = await storage.get_deal(deal_id)
        if deal:
            if status and deal.get("status") != status:
                continue
            all_deals.append(deal)

    if format == "ttd":
        # The Trade Desk format
        return {
            "format": "ttd",
            "deals": [
                {
                    "DealId": d.get("deal_id"),
                    "DealType": "ProgrammaticGuaranteed"
                    if d.get("deal_type") == "PG"
                    else "PreferredDeal"
                    if d.get("deal_type") == "PD"
                    else "PrivateAuction",
                    "BidFloor": d.get("actual_price_cpm")
                    or d.get("pricing", {}).get("final_cpm", 0),
                    "Currency": "USD",
                    "Status": "Active" if d.get("status") == "confirmed" else "Inactive",
                }
                for d in all_deals
            ],
        }
    elif format == "dv360":
        # Display & Video 360 format
        return {
            "format": "dv360",
            "deals": [
                {
                    "dealId": d.get("deal_id"),
                    "displayName": f"Deal {d.get('deal_id')}",
                    "dealType": d.get("deal_type", "PD"),
                    "fixedCpm": {
                        "currencyCode": "USD",
                        "units": str(int(d.get("actual_price_cpm", 0) or 0)),
                        "nanos": 0,
                    },
                    "status": "ACCEPTED" if d.get("status") == "confirmed" else "PENDING",
                }
                for d in all_deals
            ],
        }
    elif format == "amazon":
        # Amazon DSP format
        return {
            "format": "amazon",
            "deals": [
                {
                    "dealId": d.get("deal_id"),
                    "dealName": f"Deal {d.get('deal_id')}",
                    "auctionType": "FIXED_PRICE"
                    if d.get("deal_type") in ("PG", "PD")
                    else "SECOND_PRICE",
                    "priceAmount": d.get("actual_price_cpm")
                    or d.get("pricing", {}).get("final_cpm", 0),
                    "priceCurrency": "USD",
                }
                for d in all_deals
            ],
        }
    elif format == "xandr":
        # Xandr format
        return {
            "format": "xandr",
            "deals": [
                {
                    "id": d.get("deal_id"),
                    "name": f"Deal {d.get('deal_id')}",
                    "type": {"1": "PG", "2": "PD", "3": "PA"}.get(
                        d.get("deal_type"), d.get("deal_type")
                    ),
                    "floor_price": d.get("actual_price_cpm")
                    or d.get("pricing", {}).get("final_cpm", 0),
                    "currency": "USD",
                    "active": d.get("status") == "confirmed",
                }
                for d in all_deals
            ],
        }
    else:
        # Generic format
        return {
            "format": "generic",
            "deals": all_deals,
            "count": len(all_deals),
        }


@app.get("/api/v1/inventory-sync/watermark", tags=["Core"])
async def get_sync_watermark():
    """Get the last sync watermark (used for incremental sync)."""
    from ...storage.factory import get_storage

    storage = await get_storage()
    watermark = await storage.get("sync_watermark:inventory")

    if not watermark:
        return {"last_sync_at": None, "message": "No sync has been performed yet."}

    return watermark


# =============================================================================
# IAB Deals API v1.0 — Deal Push & Status
# =============================================================================


class DealPushRequest(BaseModel):
    """Request to push a deal to buyer(s)."""

    deal_id: str
    buyer_urls: list[str]  # Buyer deal receiving endpoints
    buyer_api_keys: Optional[list[str]] = None  # Optional per-buyer API keys
    # Deal data (if not already stored — allows ad-hoc push)
    deal_type: Optional[str] = None
    price: Optional[float] = None
    name: Optional[str] = None
    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    buyer_seat_ids: Optional[list[str]] = None


@app.post("/api/v1/deals/push", tags=["Deal Booking"])
async def push_deal_to_buyers(request: DealPushRequest):
    """Push a deal to one or more buyer endpoints via IAB Deals API v1.0.

    The seller sends deal terms to buyer DSPs. Each buyer receives an
    HTTP POST with the full IAB Deal object and responds with acceptance status.

    This is the standardized deal distribution path — alternative to
    SSP-mediated distribution (PubMatic, Index Exchange, etc.).
    """
    from ...config import get_settings
    from ...services.deals_api import DealsAPIService
    from ...storage.factory import get_storage

    settings = get_settings()
    service = DealsAPIService()

    # Try to load deal from storage first
    storage = await get_storage()
    stored_deal = await storage.get_deal(request.deal_id)

    # Build IAB Deal object
    deal_type = request.deal_type or (stored_deal or {}).get("deal_type", "PD")
    price = (
        request.price
        or (stored_deal or {}).get("actual_price_cpm")
        or (stored_deal or {}).get("pricing", {}).get("final_cpm", 0)
    )

    deal_obj = service.build_deal_object(
        deal_id=request.deal_id,
        deal_type=deal_type,
        price=price,
        name=request.name or (stored_deal or {}).get("name"),
        impressions=request.impressions or (stored_deal or {}).get("impressions"),
        flight_start=request.flight_start or (stored_deal or {}).get("flight_start"),
        flight_end=request.flight_end or (stored_deal or {}).get("flight_end"),
        buyer_seat_ids=request.buyer_seat_ids or (stored_deal or {}).get("buyer_seat_ids", []),
        seller_id=getattr(settings, "seller_organization_id", None),
        seller_domain=getattr(settings, "seller_domain", None),
    )

    # Build buyer configs
    buyer_configs = []
    for i, url in enumerate(request.buyer_urls):
        config = {"url": url}
        if request.buyer_api_keys and i < len(request.buyer_api_keys):
            config["api_key"] = request.buyer_api_keys[i]
        buyer_configs.append(config)

    # Push to all buyers
    results = await service.push_deal_to_multiple_buyers(deal_obj, buyer_configs)

    return {
        "deal_id": request.deal_id,
        "pushed_to": len(results),
        "succeeded": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "results": [r.model_dump() for r in results],
    }


@app.get("/api/v1/deals/{deal_id}/buyer-status", tags=["Deal Booking"])
async def get_deal_buyer_status(deal_id: str, buyer_url: str):
    """Query a buyer for their acceptance status of a deal.

    Polls the buyer's deal status endpoint to check if the deal
    has been approved, rejected, or is ready to serve.
    """
    from ...services.deals_api import DealsAPIService

    service = DealsAPIService()
    result = await service.query_deal_status(deal_id, buyer_url)
    return result.model_dump()


# =============================================================================
# SSP Deal Distribution
# =============================================================================


class SSPDealDistributeRequest(BaseModel):
    """Request to distribute a deal through configured SSPs."""

    deal_id: str
    deal_type: Optional[str] = "PMP"
    name: Optional[str] = None
    advertiser: Optional[str] = None
    cpm: Optional[float] = None
    buyer_seat_ids: Optional[list[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    targeting: Optional[dict[str, Any]] = None
    # Routing hint — if set, routes to this SSP. Otherwise uses routing rules.
    ssp_name: Optional[str] = None
    inventory_type: Optional[str] = None  # for routing: ctv, display, video, etc.


@app.post("/api/v1/deals/distribute", tags=["Deal Booking"])
async def distribute_deal_via_ssp(request: SSPDealDistributeRequest):
    """Distribute a deal through configured SSP(s).

    Routes the deal to the appropriate SSP based on routing rules
    or explicit ssp_name. The SSP handles DSP-side distribution.

    Supports multiple SSPs: PubMatic (MCP), Index Exchange (REST),
    Magnite (REST), or any configured SSP connector.
    """
    from ...clients.ssp_base import SSPDealCreateRequest, SSPDealType
    from ...clients.ssp_factory import build_ssp_registry

    registry = build_ssp_registry()

    if not registry.list_ssps():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "no_ssps_configured",
                "message": "No SSP connectors configured. Set SSP_CONNECTORS in environment.",
            },
        )

    # Get the right SSP client
    try:
        if request.ssp_name:
            ssp = registry.get_client(request.ssp_name)
        else:
            ssp = registry.get_client_for(
                inventory_type=request.inventory_type,
                deal_type=request.deal_type,
            )
    except (KeyError, RuntimeError) as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "ssp_routing_failed",
                "message": str(e),
                "available_ssps": registry.list_ssps(),
            },
        )

    # Map deal type
    deal_type_map = {
        "PMP": SSPDealType.PMP,
        "PG": SSPDealType.PG,
        "PREFERRED": SSPDealType.PREFERRED,
        "pmp": SSPDealType.PMP,
        "pg": SSPDealType.PG,
        "preferred": SSPDealType.PREFERRED,
    }

    create_request = SSPDealCreateRequest(
        deal_type=deal_type_map.get(request.deal_type or "PMP", SSPDealType.PMP),
        name=request.name,
        advertiser=request.advertiser,
        cpm=request.cpm,
        buyer_seat_ids=request.buyer_seat_ids or [],
        start_date=request.start_date,
        end_date=request.end_date,
        targeting=request.targeting,
    )

    async with ssp:
        result = await ssp.create_deal(create_request)

    return {
        "deal_id": result.deal_id,
        "ssp": result.ssp_name,
        "ssp_type": result.ssp_type.value,
        "status": result.status.value,
        "deal": result.model_dump(exclude={"raw"}),
    }


@app.get("/api/v1/deals/{deal_id}/ssp-troubleshoot", tags=["Deal Booking"])
async def troubleshoot_deal_via_ssp(deal_id: str, ssp_name: str):
    """Troubleshoot a deal via SSP diagnostics.

    Calls the SSP's troubleshooting tool (e.g., PubMatic's
    deal_troubleshooting) to diagnose performance issues.
    """
    from ...clients.ssp_factory import build_ssp_registry

    registry = build_ssp_registry()

    try:
        ssp = registry.get_client(ssp_name)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_ssp",
                "message": f"SSP '{ssp_name}' not configured.",
                "available_ssps": registry.list_ssps(),
            },
        )

    async with ssp:
        result = await ssp.troubleshoot_deal(deal_id)

    return result.model_dump(exclude={"raw"})


# =============================================================================
# Curator Support
# =============================================================================


class CuratorRegistrationRequest(BaseModel):
    """Request to register a new curator."""

    curator_id: str
    name: str
    domain: str
    curator_type: str = "full_service"  # audience, content, package, optimization, full_service
    description: Optional[str] = None
    fee_type: str = "percent"  # cpm_flat, percent, fixed, none
    fee_value: float = 0.0
    contact_email: Optional[str] = None
    api_key: Optional[str] = None
    audience_segments: list[str] = []
    content_categories: list[str] = []
    supported_deal_types: list[str] = ["pmp", "preferred", "pg"]


class CuratedDealRequest(BaseModel):
    """Request to create a curated deal."""

    curator_id: str
    deal_type: str = "PMP"
    product_id: Optional[str] = None
    max_cpm: Optional[float] = None  # Buyer's max CPM (curator fee included)
    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    buyer_seat_ids: list[str] = []
    # Curator targeting overlay
    audience_segments: list[str] = []
    content_categories: list[str] = []


@app.get("/api/v1/curators", tags=["Curators"])
async def list_curators():
    """List all registered curators.

    Returns curators who can create deals against this publisher's
    inventory. Agent Range is pre-registered as a day-one curator.
    """
    from ...services.curator_registry import build_curator_registry

    registry = build_curator_registry()
    curators = registry.list_active()

    return {
        "curators": [
            {
                "curator_id": c.curator_id,
                "name": c.name,
                "domain": c.domain,
                "type": c.curator_type.value,
                "description": c.description,
                "fee": c.fee.model_dump(),
                "supported_deal_types": c.supported_deal_types,
                "is_active": c.is_active,
            }
            for c in curators
        ],
        "count": len(curators),
    }


@app.get("/api/v1/curators/{curator_id}", tags=["Curators"])
async def get_curator(curator_id: str):
    """Get details for a specific curator."""
    from ...services.curator_registry import build_curator_registry

    registry = build_curator_registry()
    try:
        curator = registry.get(curator_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "curator_not_found",
                "message": f"Curator '{curator_id}' not registered.",
            },
        )

    return {
        "curator_id": curator.curator_id,
        "name": curator.name,
        "domain": curator.domain,
        "type": curator.curator_type.value,
        "description": curator.description,
        "fee": curator.fee.model_dump(),
        "audience_segments": curator.audience_segments,
        "content_categories": curator.content_categories,
        "supported_deal_types": curator.supported_deal_types,
        "is_active": curator.is_active,
        "tags": curator.tags,
    }


@app.post("/api/v1/curators", tags=["Curators"], status_code=201)
async def register_curator(request: CuratorRegistrationRequest):
    """Register a new curator.

    Curators can then create deals against this publisher's inventory
    via the /api/v1/deals/curated endpoint.
    """
    from ...models.curator import Curator, CuratorFee, CuratorFeeType, CuratorType
    from ...storage.factory import get_storage

    curator = Curator(
        curator_id=request.curator_id,
        name=request.name,
        domain=request.domain,
        curator_type=CuratorType(request.curator_type),
        description=request.description,
        fee=CuratorFee(
            fee_type=CuratorFeeType(request.fee_type),
            fee_value=request.fee_value,
        ),
        contact_email=request.contact_email,
        api_key=request.api_key,
        audience_segments=request.audience_segments,
        content_categories=request.content_categories,
        supported_deal_types=request.supported_deal_types,
    )

    # Persist to storage
    storage = await get_storage()
    await storage.set(f"curator:{curator.curator_id}", curator.model_dump())

    return {
        "curator_id": curator.curator_id,
        "name": curator.name,
        "status": "registered",
    }


@app.post("/api/v1/deals/curated", tags=["Curators"], status_code=201)
async def create_curated_deal(request: CuratedDealRequest):
    """Create a deal with curator overlay.

    The curator's fee is added on top of the publisher's base price.
    The curator appears as a node in the deal's schain. The buyer
    pays the total CPM (publisher + curator fee).

    The deal is created via the normal from-template flow, then
    enriched with curator identity, fee, and targeting overlay.
    """
    import uuid as uuid_mod
    from datetime import timedelta

    from ...flows import ProductSetupFlow
    from ...services.curator_registry import build_curator_registry
    from ...storage.factory import get_storage

    # Get curator
    registry = build_curator_registry()
    try:
        curator = registry.get(request.curator_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "curator_not_found",
                "message": f"Curator '{request.curator_id}' not registered.",
            },
        )

    if not curator.is_active:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "curator_inactive",
                "message": f"Curator '{curator.name}' is not active.",
            },
        )

    # Get base price from product catalog
    base_cpm = 12.0  # Default
    if request.product_id:
        setup_flow = ProductSetupFlow()
        await setup_flow.kickoff_async()
        product = setup_flow.state.products.get(request.product_id)
        if product:
            base_cpm = product.base_cpm

    # Calculate curated pricing
    curated_deal = registry.create_curated_deal(
        curator_id=request.curator_id,
        deal_id="pending",
        base_cpm=base_cpm,
        audience_segments=request.audience_segments,
        content_categories=request.content_categories,
        impressions=request.impressions or 0,
    )

    # Check buyer's max CPM against curated price
    if request.max_cpm is not None and request.max_cpm < curated_deal.total_cpm:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "below_curated_floor",
                "message": f"Buyer max CPM ${request.max_cpm:.2f} is below curated price ${curated_deal.total_cpm:.2f} (base ${base_cpm:.2f} + curator fee ${curated_deal.curator_fee_cpm:.2f}).",
                "base_cpm": base_cpm,
                "curator_fee_cpm": curated_deal.curator_fee_cpm,
                "total_cpm": curated_deal.total_cpm,
                "curator": curator.name,
            },
        )

    # Generate deal
    now = datetime.utcnow()
    deal_id = f"CUR-{uuid_mod.uuid4().hex[:12].upper()}"
    flight_start = request.flight_start or now.strftime("%Y-%m-%d")
    flight_end = request.flight_end or (now + timedelta(days=30)).strftime("%Y-%m-%d")

    # Build schain with publisher + curator nodes
    from ...config import get_settings

    settings = get_settings()
    seller_domain = getattr(settings, "seller_domain", "demo-publisher.example.com")
    seller_name = getattr(settings, "seller_organization_name", "Demo Publisher")

    schain = {
        "ver": "1.0",
        "complete": 1,
        "nodes": [
            # Publisher node
            {
                "asi": seller_domain,
                "sid": "default",
                "hp": 1,
                "name": seller_name,
                "domain": seller_domain,
            },
            # Curator node
            {
                "asi": curator.domain,
                "sid": curator.curator_id,
                "hp": 1,
                "name": curator.name,
                "domain": curator.domain,
            },
        ],
    }

    deal_data = {
        "deal_id": deal_id,
        "deal_type": request.deal_type,
        "status": "confirmed",
        "product_id": request.product_id,
        "base_cpm": base_cpm,
        "curator_fee_cpm": curated_deal.curator_fee_cpm,
        "total_cpm": curated_deal.total_cpm,
        "currency": "USD",
        "impressions": request.impressions,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "buyer_seat_ids": request.buyer_seat_ids,
        "curator": {
            "curator_id": curator.curator_id,
            "name": curator.name,
            "domain": curator.domain,
            "type": curator.curator_type.value,
            "fee": curator.fee.model_dump(),
        },
        "curator_targeting": {
            "audience_segments": request.audience_segments,
            "content_categories": request.content_categories,
        },
        "schain": schain,
        "created_at": now.isoformat() + "Z",
    }

    storage = await get_storage()
    await storage.set_deal(deal_id, deal_data)

    return {
        "deal_id": deal_id,
        "status": "confirmed",
        "deal_type": request.deal_type,
        "pricing": {
            "base_cpm": base_cpm,
            "curator_fee_cpm": curated_deal.curator_fee_cpm,
            "total_cpm": curated_deal.total_cpm,
            "currency": "USD",
        },
        "curator": {
            "curator_id": curator.curator_id,
            "name": curator.name,
            "type": curator.curator_type.value,
        },
        "schain": schain,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "created_at": deal_data["created_at"],
    }


# =============================================================================
# Deal Migration & Deprecation (Deal Library Phase 4)
# =============================================================================


class DealMigrationRequest(BaseModel):
    """Request to migrate (replace) an existing deal."""

    old_deal_id: str
    # New deal params
    deal_type: Optional[str] = None  # PG, PD, PA — defaults to old deal's type
    product_id: Optional[str] = None  # defaults to old deal's product
    max_cpm: Optional[float] = None
    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    buyer_seat_ids: Optional[list[str]] = None
    reason: Optional[str] = None  # Why migrating (e.g., "better supply path")
    buyer_identity: Optional[QuoteBuyerIdentityModel] = None


@app.post("/api/v1/deals/{deal_id}/migrate", tags=["Deal Booking"], status_code=201)
async def migrate_deal(
    deal_id: str,
    request: DealMigrationRequest,
    api_key_record=Depends(_get_optional_api_key_record),
):
    """Migrate (replace) an existing deal with a new one.

    Creates a replacement deal with parent_deal_id lineage pointing
    to the old deal, then deprecates the old deal. The buyer's Deal
    Jockey can follow the lineage chain to track deal evolution.

    Returns the new deal with lineage metadata.
    """
    import uuid as uuid_mod
    from datetime import timedelta

    from ...storage.factory import get_storage

    storage = await get_storage()

    # Load old deal
    old_deal = await storage.get_deal(deal_id)
    if not old_deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    # Build new deal from old deal + overrides
    now = datetime.utcnow()
    new_deal_id = f"DEMO-{uuid_mod.uuid4().hex[:12].upper()}"

    new_deal = {
        "deal_id": new_deal_id,
        "deal_type": request.deal_type or old_deal.get("deal_type", "PD"),
        "status": "confirmed",
        "product_id": request.product_id or old_deal.get("product_id"),
        "actual_price_cpm": request.max_cpm
        or old_deal.get("actual_price_cpm")
        or old_deal.get("pricing", {}).get("final_cpm"),
        "currency": old_deal.get("currency", "USD"),
        "impressions": request.impressions or old_deal.get("impressions"),
        "flight_start": request.flight_start
        or old_deal.get("flight_start")
        or now.strftime("%Y-%m-%d"),
        "flight_end": request.flight_end
        or old_deal.get("flight_end")
        or (now + timedelta(days=30)).strftime("%Y-%m-%d"),
        "buyer_seat_ids": request.buyer_seat_ids or old_deal.get("buyer_seat_ids", []),
        # Lineage
        "parent_deal_id": deal_id,
        "migration_reason": request.reason,
        # Carry forward schain and activation instructions
        "schain": old_deal.get("schain"),
        "activation_instructions": old_deal.get("activation_instructions"),
        "created_at": now.isoformat() + "Z",
    }

    await storage.set_deal(new_deal_id, new_deal)

    # Deprecate old deal
    old_deal["status"] = "deprecated"
    old_deal["deprecated_at"] = now.isoformat() + "Z"
    old_deal["deprecated_reason"] = request.reason or "Replaced by migration"
    old_deal["replacement_deal_id"] = new_deal_id
    await storage.set_deal(deal_id, old_deal)

    return {
        "new_deal_id": new_deal_id,
        "old_deal_id": deal_id,
        "status": "migrated",
        "lineage": {
            "parent_deal_id": deal_id,
            "replacement_deal_id": new_deal_id,
            "reason": request.reason,
        },
        "new_deal": new_deal,
    }


class DealDeprecationRequest(BaseModel):
    """Request to deprecate a deal."""

    reason: str
    replacement_deal_id: Optional[str] = None  # If replaced by another deal


@app.post("/api/v1/deals/{deal_id}/deprecate", tags=["Deal Booking"])
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
    from ...storage.factory import get_storage

    storage = await get_storage()

    deal = await storage.get_deal(deal_id)
    if not deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    if deal.get("status") == "deprecated":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_deprecated",
                "message": f"Deal '{deal_id}' is already deprecated.",
                "deprecated_at": deal.get("deprecated_at"),
                "reason": deal.get("deprecated_reason"),
            },
        )

    now = datetime.utcnow().isoformat() + "Z"

    deal["status"] = "deprecated"
    deal["deprecated_at"] = now
    deal["deprecated_reason"] = request.reason
    if request.replacement_deal_id:
        deal["replacement_deal_id"] = request.replacement_deal_id

    await storage.set_deal(deal_id, deal)

    return {
        "deal_id": deal_id,
        "status": "deprecated",
        "deprecated_at": now,
        "reason": request.reason,
        "replacement_deal_id": request.replacement_deal_id,
    }


@app.get("/api/v1/deals/{deal_id}/lineage", tags=["Deal Booking"])
async def get_deal_lineage(deal_id: str):
    """Get the lineage chain for a deal.

    Walks parent_deal_id backwards and replacement_deal_id forwards
    to show the full evolution of a deal through migrations.
    """
    from ...storage.factory import get_storage

    storage = await get_storage()

    # Walk backwards (parents)
    parents = []
    visited = set()
    walk_id = deal_id
    while walk_id and walk_id not in visited:
        visited.add(walk_id)
        deal = await storage.get_deal(walk_id)
        if not deal:
            break
        parent_id = deal.get("parent_deal_id")
        if parent_id:
            parents.insert(
                0,
                {
                    "deal_id": parent_id,
                    "status": (await storage.get_deal(parent_id) or {}).get("status", "unknown"),
                },
            )
        walk_id = parent_id

    # Current deal
    current_deal = await storage.get_deal(deal_id)

    # Walk forwards (replacements)
    replacements = []
    visited = set()
    walk_id = (current_deal or {}).get("replacement_deal_id")
    while walk_id and walk_id not in visited:
        visited.add(walk_id)
        deal = await storage.get_deal(walk_id)
        if not deal:
            break
        replacements.append(
            {
                "deal_id": walk_id,
                "status": deal.get("status", "unknown"),
                "reason": deal.get("migration_reason"),
            }
        )
        walk_id = deal.get("replacement_deal_id")

    return {
        "deal_id": deal_id,
        "status": (current_deal or {}).get("status", "unknown"),
        "parents": parents,
        "replacements": replacements,
        "chain_length": len(parents) + 1 + len(replacements),
    }
