# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""A2A agent discovery (agent card) and agent registry management endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import deps
from ..schemas import DiscoverAgentRequest, UpdateTrustRequest

router = APIRouter()


@router.get("/.well-known/agent.json", tags=["Agent Registry"])
async def agent_card():
    """Serve this seller agent's card for A2A discovery.

    Returns an A2A-protocol-compliant agent card describing this
    seller's capabilities, supported protocols, and inventory types.
    Buyer agents and registries fetch this to discover the seller.
    """
    from ....models.agent_registry import (
        AgentAuthentication,
        AgentCapabilities,
        AgentCard,
        AgentProvider,
        AgentSkill,
    )
    from ....models.audience_capabilities import build_capability_audience_block

    settings = deps._get_api_settings()

    # Read inventory types from the cached static catalog rather than running
    # ProductSetupFlow per request (which hangs in OpenDirect MCP
    # session.initialize() — see `catalog_service` for context).
    try:
        inventory_types = set(deps.get_product_catalog()["inventory_types"])
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
        # Audience capability advertisement (proposal §5.7 layer 1). Demo /
        # MVP defaults: agentic match endpoint not yet shipped (lands in §11),
        # constraints filter not yet shipped (lands in §10) but we advertise
        # support so buyers test the negotiation path. Lock-file hashes are
        # loaded dynamically from data/taxonomies/taxonomies.lock.json so the
        # block stays in sync if the lock file is regenerated.
        audience_capabilities=build_capability_audience_block(),
    )

    return card.model_dump()


@router.get("/registry/agents", tags=["Agent Registry"])
async def list_registered_agents(
    agent_type: Optional[str] = None,
    trust_status: Optional[str] = None,
):
    """List agents in the local registry.

    Filterable by agent_type (buyer, seller, tool_provider, data_provider, other)
    and trust_status (unknown, registered, approved, preferred, blocked).
    """
    from ....models.agent_registry import AgentType, TrustStatus

    service = await deps._get_registry_service()

    at = AgentType(agent_type) if agent_type else None
    ts = TrustStatus(trust_status) if trust_status else None

    agents = await service.list_agents(agent_type=at, trust_status=ts)
    return {
        "agents": [a.model_dump(mode="json") for a in agents],
        "total": len(agents),
    }


@router.get("/registry/agents/{agent_id}", tags=["Agent Registry"])
async def get_registered_agent(agent_id: str):
    """Get details for a specific registered agent."""
    service = await deps._get_registry_service()
    agent = await service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump(mode="json")


@router.post("/registry/agents/discover", tags=["Agent Registry"])
async def discover_agent(request: DiscoverAgentRequest):
    """Discover an agent by URL.

    Fetches the agent's card from .well-known/agent.json, checks
    all configured registries (AAMP + extras) for verification, and
    registers the agent locally with appropriate trust status.
    """
    service = await deps._get_registry_service()
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


@router.put("/registry/agents/{agent_id}/trust", tags=["Agent Registry"])
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
    from ....models.agent_registry import TRUST_TO_TIER_MAP, TrustStatus

    try:
        ts = TrustStatus(request.trust_status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trust_status: {request.trust_status}. "
            f"Valid values: {[s.value for s in TrustStatus]}",
        )

    service = await deps._get_registry_service()
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


@router.delete("/registry/agents/{agent_id}", tags=["Agent Registry"])
async def remove_registered_agent(agent_id: str):
    """Remove an agent from the local registry."""
    service = await deps._get_registry_service()
    removed = await service.remove_agent(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_id, "status": "removed"}
