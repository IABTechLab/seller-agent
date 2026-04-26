# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP Server for the Seller Agent.

Exposes all seller agent capabilities as MCP tools for Claude Desktop,
ChatGPT, and other MCP-compatible AI assistants. This is the publisher's
primary interface for managing their seller agent conversationally.

Two-phase setup model:
  Phase A (Developer / Claude Code): infra, credentials, deployment
  Phase B (Business / Claude Desktop): media kit, pricing, buyers, operations

All tools use the polymorphic ad server abstraction — never import
GAM or FreeWheel directly.

Usage:
    # Standalone:
    python -m ad_seller.interfaces.mcp_server

    # Mounted in FastAPI (see mount_mcp_sse in api/main.py):
    from ad_seller.interfaces.mcp_server import mcp
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "IAB Tech Lab Seller Agent",
    instructions=(
        "You are a publisher's seller agent for programmatic advertising. "
        "You help publishers manage their inventory, set pricing, create deals, "
        "and interact with buyer agents. On first connection, check setup status "
        "and offer the guided setup wizard if configuration is incomplete."
    ),
)


# =============================================================================
# Helper: get settings and storage
# =============================================================================


def _get_settings():
    from ..config import get_settings

    return get_settings()


async def _get_storage():
    from ..storage.factory import get_storage

    return await get_storage()


# =============================================================================
# Setup & Status
# =============================================================================


@mcp.tool()
async def get_setup_status() -> str:
    """Check what's configured and what's missing. Use this on first connection
    to determine if the setup wizard should launch."""
    settings = _get_settings()
    await _get_storage()  # verify storage is accessible

    # Check each area
    identity_configured = settings.seller_organization_name != "Default Publisher"
    ad_server_configured = bool(settings.gam_network_code or settings.freewheel_sh_mcp_url)
    ssp_configured = bool(settings.ssp_connectors)

    # Check if media kit has packages
    packages = []
    try:
        from ..engines.media_kit_service import MediaKitService

        service = MediaKitService()
        packages = await service.list_packages_public()
    except Exception:
        pass

    status = {
        "publisher_identity": {
            "configured": identity_configured,
            "name": settings.seller_organization_name,
        },
        "ad_server": {
            "configured": ad_server_configured,
            "type": settings.ad_server_type if ad_server_configured else None,
        },
        "ssp_connectors": {
            "configured": ssp_configured,
            "connectors": settings.ssp_connectors or "none",
        },
        "media_kit": {"configured": len(packages) > 0, "package_count": len(packages)},
        "pricing": {
            "floor_cpm": settings.default_price_floor_cpm,
            "currency": settings.default_currency,
        },
        "approval_gates": {"enabled": settings.approval_gate_enabled},
        "setup_complete": all([identity_configured, ad_server_configured, len(packages) > 0]),
    }

    if not status["setup_complete"]:
        status["message"] = (
            "Setup is incomplete. I can walk you through the setup wizard step by step. "
            "Say 'start setup' or ask about any specific area."
        )
    else:
        status["message"] = "Your seller agent is fully configured and ready."

    return json.dumps(status, indent=2)


@mcp.tool()
async def health_check() -> str:
    """Check system health — storage, ad server, SSPs."""
    results = {"status": "healthy", "checks": {}}

    # Storage
    try:
        await _get_storage()
        results["checks"]["storage"] = "ok"
    except Exception as e:
        results["checks"]["storage"] = f"error: {e}"
        results["status"] = "degraded"

    # Ad server
    settings = _get_settings()
    if settings.gam_network_code or settings.freewheel_sh_mcp_url:
        try:
            from ..clients.ad_server_base import get_ad_server_client

            get_ad_server_client()
            results["checks"]["ad_server"] = f"configured ({settings.ad_server_type})"
        except Exception as e:
            results["checks"]["ad_server"] = f"error: {e}"
    else:
        results["checks"]["ad_server"] = "not configured"

    # SSPs
    if settings.ssp_connectors:
        results["checks"]["ssp_connectors"] = settings.ssp_connectors
    else:
        results["checks"]["ssp_connectors"] = "none configured"

    return json.dumps(results, indent=2)


@mcp.tool()
async def get_config() -> str:
    """Get current configuration summary (no secrets)."""
    settings = _get_settings()
    return json.dumps(
        {
            "publisher": {
                "name": settings.seller_organization_name,
                "org_id": settings.seller_organization_id,
            },
            "ad_server": {
                "type": settings.ad_server_type,
                "gam_enabled": settings.gam_enabled,
                "freewheel_enabled": settings.freewheel_enabled,
                "freewheel_inventory_mode": getattr(
                    settings, "freewheel_inventory_mode", "deals_only"
                ),
            },
            "ssp": {
                "connectors": settings.ssp_connectors or "none",
                "routing_rules": settings.ssp_routing_rules or "none",
            },
            "pricing": {
                "currency": settings.default_currency,
                "floor_cpm": settings.default_price_floor_cpm,
                "yield_optimization": settings.yield_optimization_enabled,
                "pg_floor_multiplier": settings.programmatic_floor_multiplier,
                "pd_discount_max": settings.preferred_deal_discount_max,
            },
            "approval_gates": {
                "enabled": settings.approval_gate_enabled,
                "timeout_hours": settings.approval_timeout_hours,
                "required_flows": settings.approval_required_flows or "none",
            },
            "agent_registry": {
                "enabled": settings.agent_registry_enabled,
                "url": settings.agent_registry_url,
            },
        },
        indent=2,
    )


# =============================================================================
# Publisher Identity
# =============================================================================


@mcp.tool()
async def set_publisher_identity(name: str, domain: str = "", org_id: str = "") -> str:
    """Set the publisher's identity (name, domain, organization ID).
    This is shown in the agent card and supply chain info."""
    # Write to .env file
    _update_env("SELLER_ORGANIZATION_NAME", name)
    if domain:
        _update_env("SELLER_DOMAIN", domain)
    if org_id:
        _update_env("SELLER_ORGANIZATION_ID", org_id)

    return json.dumps(
        {
            "status": "updated",
            "name": name,
            "domain": domain or "(unchanged)",
            "org_id": org_id or "(unchanged)",
            "note": "Restart the server for changes to take effect.",
        }
    )


# =============================================================================
# Inventory & Products
# =============================================================================


@mcp.tool()
async def list_products(limit: int = 50) -> str:
    """List products in the catalog. These are the inventory items available for deals."""
    from ..flows import ProductSetupFlow

    flow = ProductSetupFlow()
    await flow.kickoff_async()

    products = []
    for pid, product in list(flow.state.products.items())[:limit]:
        products.append(
            {
                "product_id": pid,
                "name": product.name,
                "inventory_type": product.inventory_type,
                "base_cpm": product.base_cpm,
                "floor_cpm": product.floor_cpm,
                "deal_types": [dt.value for dt in product.supported_deal_types],
            }
        )

    return json.dumps({"products": products, "count": len(products)}, indent=2)


@mcp.tool()
async def sync_inventory(incremental: bool = False) -> str:
    """Trigger inventory sync from the ad server (GAM or FreeWheel).
    Use incremental=true to only sync changes since last sync."""
    from ..services.inventory_sync_scheduler import _run_sync

    result = await _run_sync()
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_sync_status() -> str:
    """Check the status of the inventory sync scheduler."""
    from ..services.inventory_sync_scheduler import get_sync_status

    return json.dumps(get_sync_status(), indent=2)


@mcp.tool()
async def list_inventory(limit: int = 100) -> str:
    """List raw inventory from the ad server (before product mapping)."""
    from ..clients.ad_server_base import get_ad_server_client

    client = get_ad_server_client()
    async with client:
        items = await client.list_inventory(limit=limit)

    return json.dumps(
        {
            "items": [
                {
                    "id": i.id,
                    "name": i.name,
                    "status": i.status,
                    "ad_server": i.ad_server_type.value,
                }
                for i in items
            ],
            "count": len(items),
        },
        indent=2,
    )


# =============================================================================
# Media Kit & Packages
# =============================================================================


@mcp.tool()
async def list_packages(featured_only: bool = False) -> str:
    """List packages in the media kit. These are what buyers browse."""
    from ..engines.media_kit_service import MediaKitService

    service = MediaKitService()
    packages = await service.list_packages_public(featured_only=featured_only)
    return json.dumps(
        {
            "packages": [p.model_dump() if hasattr(p, "model_dump") else p for p in packages],
            "count": len(packages),
        },
        indent=2,
    )


@mcp.tool()
async def create_package(
    name: str,
    inventory_type: str,
    base_price: float,
    floor_price: float,
    description: str = "",
    is_featured: bool = False,
) -> str:
    """Create a new curated package in the media kit."""
    import uuid

    from ..models.media_kit import Package, PackageLayer

    package = Package(
        package_id=f"pkg-{uuid.uuid4().hex[:8]}",
        name=name,
        description=description,
        layer=PackageLayer.CURATED,
        base_price=base_price,
        floor_price=floor_price,
        is_featured=is_featured,
    )

    storage = await _get_storage()
    await storage.set_package(package.package_id, package.model_dump(mode="json"))

    return json.dumps(
        {"status": "created", "package_id": package.package_id, "name": name}, indent=2
    )


# =============================================================================
# Pricing
# =============================================================================


@mcp.tool()
async def get_rate_card() -> str:
    """Get the current rate card (base CPMs by inventory type)."""
    storage = await _get_storage()
    rate_card = await storage.get("rate_card:current")

    if not rate_card:
        return json.dumps(
            {
                "entries": [
                    {"inventory_type": "display", "base_cpm": 12.0},
                    {"inventory_type": "video", "base_cpm": 25.0},
                    {"inventory_type": "ctv", "base_cpm": 35.0},
                    {"inventory_type": "mobile_app", "base_cpm": 18.0},
                    {"inventory_type": "native", "base_cpm": 10.0},
                    {"inventory_type": "audio", "base_cpm": 15.0},
                ],
                "source": "defaults",
            },
            indent=2,
        )

    return json.dumps(rate_card, indent=2)


@mcp.tool()
async def update_rate_card(entries: str) -> str:
    """Update the rate card. Pass entries as JSON array:
    [{"inventory_type": "ctv", "base_cpm": 40.0}, ...]"""
    storage = await _get_storage()
    parsed = json.loads(entries)
    now = datetime.now(timezone.utc).isoformat()

    rate_card = {"entries": parsed, "updated_at": now}
    await storage.set("rate_card:current", rate_card)

    return json.dumps({"status": "updated", "entries": len(parsed), "updated_at": now})


@mcp.tool()
async def get_pricing(product_id: str, buyer_tier: str = "public", volume: int = 0) -> str:
    """Calculate tiered pricing for a product based on buyer identity."""
    from ..engines.pricing_rules_engine import PricingRulesEngine
    from ..flows import ProductSetupFlow
    from ..models.buyer_identity import BuyerContext, BuyerIdentity
    from ..models.core import DealType
    from ..models.pricing_tiers import TieredPricingConfig

    setup = ProductSetupFlow()
    await setup.kickoff_async()
    product = setup.state.products.get(product_id)

    if not product:
        return json.dumps({"error": f"Product '{product_id}' not found"})

    context = BuyerContext(identity=BuyerIdentity(), is_authenticated=buyer_tier != "public")
    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    decision = engine.calculate_price(
        product_id=product_id,
        base_price=product.base_cpm,
        buyer_context=context,
        deal_type=DealType.PREFERRED_DEAL,
        volume=volume,
        inventory_type=product.inventory_type,
    )

    return json.dumps(
        {
            "product_id": product_id,
            "base_cpm": decision.base_price,
            "final_cpm": decision.final_price,
            "tier_discount": decision.tier_discount,
            "volume_discount": decision.volume_discount,
            "rationale": decision.rationale,
        },
        indent=2,
    )


# =============================================================================
# Deals
# =============================================================================


@mcp.tool()
async def request_quote(product_id: str, deal_type: str = "PD", impressions: int = 0) -> str:
    """Request a non-binding price quote for a product."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body = {"product_id": product_id, "deal_type": deal_type}
    if impressions:
        body["impressions"] = impressions

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/quotes", json=body)
        return resp.text


@mcp.tool()
async def create_deal_from_template(
    deal_type: str,
    product_id: str,
    max_cpm: float = 0,
    impressions: int = 0,
    flight_start: str = "",
    flight_end: str = "",
) -> str:
    """Create a deal directly from parameters (one-step, no quote needed).
    Returns the deal or a rejection if max_cpm is below floor."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"deal_type": deal_type, "product_id": product_id}
    if max_cpm:
        body["max_cpm"] = max_cpm
    if impressions:
        body["impressions"] = impressions
    if flight_start:
        body["flight_start"] = flight_start
    if flight_end:
        body["flight_end"] = flight_end

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/deals/from-template", json=body)
        return resp.text


@mcp.tool()
async def get_deal_performance(deal_id: str) -> str:
    """Get delivery and performance metrics for a deal."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/api/v1/deals/{deal_id}/performance")
        return resp.text


@mcp.tool()
async def push_deal_to_buyers(deal_id: str, buyer_urls: str) -> str:
    """Push a deal to buyer endpoints via IAB Deals API v1.0.
    Pass buyer_urls as comma-separated list."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    urls = [u.strip() for u in buyer_urls.split(",") if u.strip()]
    body = {"deal_id": deal_id, "buyer_urls": urls}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/deals/push", json=body)
        return resp.text


@mcp.tool()
async def distribute_deal_via_ssp(
    deal_id: str,
    deal_type: str = "PMP",
    cpm: float = 0,
    ssp_name: str = "",
    inventory_type: str = "",
) -> str:
    """Distribute a deal through configured SSP(s).
    Routes based on ssp_name or inventory_type routing rules."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"deal_id": deal_id, "deal_type": deal_type}
    if cpm:
        body["cpm"] = cpm
    if ssp_name:
        body["ssp_name"] = ssp_name
    if inventory_type:
        body["inventory_type"] = inventory_type

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/deals/distribute", json=body)
        return resp.text


@mcp.tool()
async def troubleshoot_deal(deal_id: str, ssp_name: str) -> str:
    """Diagnose deal performance issues via SSP diagnostics."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{url}/api/v1/deals/{deal_id}/ssp-troubleshoot", params={"ssp_name": ssp_name}
        )
        return resp.text


@mcp.tool()
async def migrate_deal(old_deal_id: str, reason: str = "", max_cpm: float = 0) -> str:
    """Replace an existing deal with a new one. Creates lineage tracking."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"old_deal_id": old_deal_id}
    if reason:
        body["reason"] = reason
    if max_cpm:
        body["max_cpm"] = max_cpm

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/deals/{old_deal_id}/migrate", json=body)
        return resp.text


@mcp.tool()
async def deprecate_deal(deal_id: str, reason: str, replacement_deal_id: str = "") -> str:
    """Mark a deal as deprecated with reason and optional replacement."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"reason": reason}
    if replacement_deal_id:
        body["replacement_deal_id"] = replacement_deal_id

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/deals/{deal_id}/deprecate", json=body)
        return resp.text


@mcp.tool()
async def get_deal_lineage(deal_id: str) -> str:
    """Get the lineage chain for a deal — parents and replacements."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/api/v1/deals/{deal_id}/lineage")
        return resp.text


@mcp.tool()
async def export_deals(format: str = "generic", status: str = "") -> str:
    """Export deals in DSP-native format (generic, ttd, dv360, amazon, xandr)."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    params: dict[str, str] = {"format": format}
    if status:
        params["status"] = status

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/api/v1/deals/export", params=params)
        return resp.text


@mcp.tool()
async def bulk_deal_operations(operations: str) -> str:
    """Process multiple deal operations in one batch.
    Pass operations as JSON array: [{"action":"create","quote_id":"..."}, ...]"""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    parsed = json.loads(operations)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/deals/bulk", json={"operations": parsed})
        return resp.text


# =============================================================================
# Orders & Change Requests
# =============================================================================


@mcp.tool()
async def list_orders(limit: int = 50) -> str:
    """List orders and their current states."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/api/v1/orders", params={"limit": limit})
        return resp.text


@mcp.tool()
async def transition_order(order_id: str, new_status: str, reason: str = "") -> str:
    """Transition an order to a new state (e.g., draft→approved→delivering)."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"new_status": new_status}
    if reason:
        body["reason"] = reason

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/orders/{order_id}/transition", json=body)
        return resp.text


# =============================================================================
# Approvals
# =============================================================================


@mcp.tool()
async def list_pending_approvals() -> str:
    """List pending approval requests waiting for human decision."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/approvals")
        return resp.text


@mcp.tool()
async def approve_or_reject(approval_id: str, decision: str, reason: str = "") -> str:
    """Submit an approval decision. decision: 'approve', 'reject', or 'counter'."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"decision": decision}
    if reason:
        body["reason"] = reason

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/approvals/{approval_id}/decide", json=body)
        return resp.text


@mcp.tool()
async def set_approval_gates(
    enabled: bool, required_flows: str = "", timeout_hours: int = 24
) -> str:
    """Configure approval gates. required_flows is comma-separated:
    'proposal_decision,deal_registration'"""
    _update_env("APPROVAL_GATE_ENABLED", str(enabled).lower())
    if required_flows:
        _update_env("APPROVAL_REQUIRED_FLOWS", required_flows)
    _update_env("APPROVAL_TIMEOUT_HOURS", str(timeout_hours))

    return json.dumps(
        {
            "status": "updated",
            "enabled": enabled,
            "required_flows": required_flows or "(unchanged)",
            "timeout_hours": timeout_hours,
            "note": "Restart the server for changes to take effect.",
        }
    )


# =============================================================================
# Supply Chain & Curators
# =============================================================================


@mcp.tool()
async def get_supply_chain() -> str:
    """Get the seller's supply chain transparency info (sellers.json format)."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/api/v1/supply-chain")
        return resp.text


@mcp.tool()
async def list_curators() -> str:
    """List registered curators (Agent Range is pre-registered)."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/api/v1/curators")
        return resp.text


@mcp.tool()
async def create_curated_deal(
    curator_id: str,
    deal_type: str = "PMP",
    product_id: str = "",
    max_cpm: float = 0,
    impressions: int = 0,
) -> str:
    """Create a deal with curator overlay. The curator's fee is added on top."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"curator_id": curator_id, "deal_type": deal_type}
    if product_id:
        body["product_id"] = product_id
    if max_cpm:
        body["max_cpm"] = max_cpm
    if impressions:
        body["impressions"] = impressions

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/api/v1/deals/curated", json=body)
        return resp.text


# =============================================================================
# Buyer Agent Management
# =============================================================================


@mcp.tool()
async def list_buyer_agents() -> str:
    """List registered buyer agents and their trust levels."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/registry/agents")
        return resp.text


@mcp.tool()
async def register_buyer_agent(agent_url: str) -> str:
    """Discover and register a buyer agent by URL.
    Fetches their agent card and adds them to the registry."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/registry/agents/discover", json={"url": agent_url})
        return resp.text


@mcp.tool()
async def set_agent_trust(agent_id: str, trust_level: str) -> str:
    """Set trust level for a buyer agent.
    Levels: unknown, registered, approved, preferred, blocked."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{url}/registry/agents/{agent_id}/trust", json={"trust_status": trust_level}
        )
        return resp.text


# =============================================================================
# API Keys
# =============================================================================


@mcp.tool()
async def create_api_key(name: str = "buyer", seat_id: str = "", agency_id: str = "") -> str:
    """Create an API key for a buyer or agent."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    body: dict[str, Any] = {"name": name}
    if seat_id:
        body["seat_id"] = seat_id
    if agency_id:
        body["agency_id"] = agency_id

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/auth/api-keys", json=body)
        return resp.text


@mcp.tool()
async def list_api_keys() -> str:
    """List active API keys."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/auth/api-keys")
        return resp.text


@mcp.tool()
async def revoke_api_key(key_id: str) -> str:
    """Revoke an API key."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(f"{url}/auth/api-keys/{key_id}")
        return resp.text


# =============================================================================
# Sessions
# =============================================================================


@mcp.tool()
async def list_sessions() -> str:
    """List active buyer conversation sessions."""
    import httpx

    settings = _get_settings()
    url = getattr(settings, "seller_agent_url", "http://localhost:8000")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{url}/sessions")
        return resp.text


# =============================================================================
# SSP Management
# =============================================================================


@mcp.tool()
async def list_ssps() -> str:
    """List configured SSP connectors and routing rules."""
    settings = _get_settings()
    return json.dumps(
        {
            "connectors": settings.ssp_connectors or "none",
            "routing_rules": settings.ssp_routing_rules or "none",
            "pubmatic": {
                "configured": bool(settings.pubmatic_mcp_url),
                "url": settings.pubmatic_mcp_url or "not set",
            },
            "index_exchange": {
                "configured": bool(settings.index_exchange_api_url),
                "url": settings.index_exchange_api_url or "not set",
            },
            "magnite": {
                "configured": bool(settings.magnite_api_url),
                "url": settings.magnite_api_url or "not set",
            },
        },
        indent=2,
    )


# =============================================================================
# Agent Hierarchy
# =============================================================================


@mcp.tool()
async def list_agents() -> str:
    """Show the 3-level agent hierarchy and their roles."""
    return json.dumps(
        {
            "hierarchy": {
                "level_1": {
                    "name": "Inventory Manager",
                    "model": "claude-opus",
                    "role": "Strategic orchestrator — maximizes publisher yield",
                    "optimizes_for": ["revenue", "fill_rate", "pricing_power", "relationships"],
                },
                "level_2": {
                    "agents": [
                        {
                            "name": "Display Specialist",
                            "focus": "IAB display, rich media, programmatic display",
                        },
                        {"name": "Video Specialist", "focus": "In-stream, out-stream, VAST/VPAID"},
                        {
                            "name": "CTV Specialist",
                            "focus": "Streaming, FAST channels, SSAI, household targeting",
                        },
                        {
                            "name": "Mobile App Specialist",
                            "focus": "iOS/Android, rewarded video, interstitials",
                        },
                        {
                            "name": "Native Specialist",
                            "focus": "In-feed, content rec, sponsored content",
                        },
                        {
                            "name": "Linear TV Specialist",
                            "focus": "Broadcast, MVPD, addressable, programmatic linear",
                        },
                    ],
                    "model": "claude-sonnet",
                },
                "level_3": {
                    "agents": [
                        {
                            "name": "Pricing Agent",
                            "focus": "Rate cards, dynamic pricing, floor prices, tier discounts",
                        },
                        {
                            "name": "Availability Agent",
                            "focus": "Forecasting, avails, pacing, delivery monitoring",
                        },
                        {
                            "name": "Proposal Review Agent",
                            "focus": "Evaluate proposals, validate, recommend accept/counter/reject",
                        },
                        {
                            "name": "Audience Validator",
                            "focus": "Validate targeting, coverage, UCP compatibility",
                        },
                        {"name": "Upsell Agent", "focus": "Cross-sell, upsell, alternatives"},
                    ],
                    "model": "claude-sonnet",
                },
            },
        },
        indent=2,
    )


# =============================================================================
# Prompts (Slash Commands)
# =============================================================================

from mcp.server.fastmcp.prompts.base import Message  # noqa: E402


@mcp.prompt(name="setup", description="First-time guided setup wizard")
async def setup_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Check setup status and walk me through configuring everything "
                "that's incomplete. Go step by step: publisher identity, ad server, "
                "SSPs, media kit, pricing, approval gates, and buyer agent access. "
                "Ask me one question at a time.",
    )]


@mcp.prompt(name="status", description="Configuration and health overview")
async def status_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me a complete status overview: configuration state, system "
                "health, ad server connection, SSP connectors, and any issues that "
                "need attention.",
    )]


@mcp.prompt(name="inventory", description="What do I have to sell?")
async def inventory_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me my current inventory: products, media kit packages, and "
                "sync status. Highlight anything that needs attention.",
    )]


@mcp.prompt(name="deals", description="Full deal status report")
async def deals_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Give me a full status report on all deal activity: active deals, "
                "deals in negotiation, recently completed deals, and any deals with "
                "issues. Include SSP distribution status.",
    )]


@mcp.prompt(name="queue", description="Inbound items needing action")
async def queue_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me everything in the inbound queue that needs my action: "
                "pending deal requests, approvals waiting for my decision, and "
                "proposals I need to review. Most urgent first.",
    )]


@mcp.prompt(name="new-deal", description="Guided deal creation")
async def new_deal_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Help me create a new deal. Walk me through it step by step: which "
                "inventory, deal type (PG/PD/PA), pricing, targeting, and which "
                "buyers or SSPs to distribute to.",
    )]


@mcp.prompt(name="configure", description="Event bus flows, approval gates, guard conditions")
async def configure_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me all configurable automation rules: event bus flows, approval "
                "gates, and guard conditions. Tell me what each one does and let me "
                "add, modify, or remove them.",
    )]


@mcp.prompt(name="buyers", description="Buyer agent activity and inbound interest")
async def buyers_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me which buyer agents have been accessing my media kit and "
                "inventory recently. For each buyer, show what they looked at, "
                "whether they initiated any deals, and their current trust level. "
                "I want to know who to follow up with.",
    )]


@mcp.prompt(name="help", description="What can this agent do?")
async def help_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="List all the things I can do with this seller agent, organized by "
                "category. Include the slash commands available and a brief "
                "description of each.",
    )]


# =============================================================================
# Inbound Queue
# =============================================================================

from ..events.approval import ApprovalGate  # noqa: E402
from ..events.bus import get_event_bus  # noqa: E402


@mcp.tool()
async def get_inbound_queue(limit: int = 50) -> str:
    """Get everything waiting for publisher action: pending approvals, unresolved
    proposals. Returns a unified list sorted by urgency (most urgent first)."""
    from datetime import timedelta

    items: list[dict] = []
    warnings: list[str] = []

    # --- Pending approvals ---
    try:
        storage = await _get_storage()
        gate = ApprovalGate(storage)
        pending = await gate.list_pending()
        for req in pending:
            urgency = "normal"
            if req.expires_at:
                # expires_at may be naive (from datetime.utcnow); compare as naive
                now_naive = datetime.utcnow()
                if (req.expires_at - now_naive) < timedelta(hours=2):
                    urgency = "high"

            items.append({
                "type": "approval",
                "id": req.approval_id,
                "summary": req.context.get("summary", f"{req.gate_name} for {req.flow_type}"),
                "timestamp": req.created_at.isoformat() if req.created_at else "",
                "from": req.context.get("buyer", ""),
                "urgency": urgency,
            })
    except Exception as e:
        warnings.append(f"Approval gate unavailable: {e}")

    # --- Unresolved proposals from event bus ---
    try:
        bus = await get_event_bus()
        received = await bus.list_events(event_type="proposal.received", limit=200)
        accepted = await bus.list_events(event_type="proposal.accepted", limit=200)
        rejected = await bus.list_events(event_type="proposal.rejected", limit=200)
        countered = await bus.list_events(event_type="proposal.countered", limit=200)

        resolved_ids = set()
        for ev in accepted + rejected + countered:
            if ev.proposal_id:
                resolved_ids.add(ev.proposal_id)

        for ev in received:
            if ev.proposal_id and ev.proposal_id not in resolved_ids:
                items.append({
                    "type": "proposal",
                    "id": ev.proposal_id,
                    "summary": f"Proposal {ev.proposal_id} received",
                    "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                    "from": ev.payload.get("buyer", ev.session_id or ""),
                    "urgency": "normal",
                })
    except Exception as e:
        warnings.append(f"Event bus unavailable: {e}")

    # Sort by urgency (high first), then by timestamp descending
    urgency_order = {"high": 0, "normal": 1}
    items.sort(key=lambda x: (urgency_order.get(x["urgency"], 1), x.get("timestamp", "")))
    items = items[:limit]

    result: dict = {"items": items, "count": len(items)}
    if warnings:
        result["warnings"] = warnings
    return json.dumps(result, indent=2)


# =============================================================================
# Buyer Activity
# =============================================================================


@mcp.tool()
async def get_buyer_activity(days: int = 7, limit: int = 50) -> str:
    """Show buyer agent engagement: who accessed inventory, initiated deals,
    or negotiated recently. Grouped by buyer identity."""
    from datetime import timedelta

    warnings: list[str] = []
    buyers_map: dict[str, dict] = {}

    try:
        bus = await get_event_bus()
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Fetch events with session_id (buyer-facing interactions)
        all_events = await bus.list_events(limit=500)

        for ev in all_events:
            if not ev.session_id:
                continue
            # Filter by time window
            if ev.timestamp and ev.timestamp < cutoff:
                continue

            agent_id = ev.metadata.get("agent_id", ev.session_id)
            agent_url = ev.metadata.get("agent_url", "")
            buyer_name = ev.payload.get("buyer", agent_id)

            if agent_id not in buyers_map:
                buyers_map[agent_id] = {
                    "agent_id": agent_id,
                    "agent_url": agent_url,
                    "buyer_name": buyer_name,
                    "trust_level": ev.metadata.get("trust_level", "unknown"),
                    "last_seen": ev.timestamp.isoformat() if ev.timestamp else "",
                    "activity_summary": [],
                    "deals_initiated": 0,
                }

            entry = buyers_map[agent_id]
            entry["activity_summary"].append({
                "event_type": ev.event_type.value,
                "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                "proposal_id": ev.proposal_id or None,
                "deal_id": ev.deal_id or None,
            })

            # Update last_seen
            if ev.timestamp:
                ts_iso = ev.timestamp.isoformat()
                if ts_iso > entry["last_seen"]:
                    entry["last_seen"] = ts_iso

            # Count deal initiations
            if ev.event_type.value in ("proposal.received", "deal.created"):
                entry["deals_initiated"] += 1

    except Exception as e:
        warnings.append(f"Event bus unavailable: {e}")

    buyers_list = sorted(
        buyers_map.values(),
        key=lambda b: b["last_seen"],
        reverse=True,
    )[:limit]

    result: dict = {"buyers": buyers_list, "count": len(buyers_list)}
    if warnings:
        result["warnings"] = warnings
    return json.dumps(result, indent=2, default=str)


# =============================================================================
# Configuration Introspection
# =============================================================================


@mcp.tool()
async def list_configurable_flows() -> str:
    """Introspect current automation config: approval gates, guard conditions,
    and event bus subscriptions. Shows what can be customized."""
    from ..models.order_state_machine import _build_default_rules

    settings = _get_settings()

    # --- Approval gates ---
    required = settings.approval_required_flows
    if isinstance(required, str):
        required_list = [f.strip() for f in required.split(",") if f.strip()]
    else:
        required_list = list(required) if required else []

    approval_gates = {
        "enabled": settings.approval_gate_enabled,
        "timeout_hours": settings.approval_timeout_hours,
        "required_flows": required_list,
        "configurable": True,
        "hint": "Use set_approval_gates to enable/disable, change timeout, or "
                "update which flows require human approval.",
    }

    # --- Guard conditions from OrderStateMachine ---
    rules = _build_default_rules()
    guard_rules = []
    for rule in rules:
        guard_rules.append({
            "from_status": rule.from_status.value,
            "to_status": rule.to_status.value,
            "description": rule.description,
            "has_guard": rule.guard is not None,
        })

    guard_conditions = {
        "rules": guard_rules,
        "configurable": True,
        "hint": "Guard conditions control which order state transitions are "
                "allowed. Custom guards can be added via OrderStateMachine.add_rule().",
    }

    # --- Event bus subscriptions (runtime only) ---
    event_flows_list = []
    try:
        bus = await get_event_bus()
        subscribers = getattr(bus, "_subscribers", {})
        for event_type, subs in subscribers.items():
            event_flows_list.append({
                "event_type": event_type,
                "subscriber_count": len(subs),
            })
    except Exception:
        pass

    event_flows = {
        "subscriptions": event_flows_list,
        "configurable": False,
        "hint": "Event bus subscriptions are runtime-only. They reflect what is "
                "actively listening in the current process. On a fresh start with "
                "no flows triggered, this section will be empty.",
    }

    result = {
        "approval_gates": approval_gates,
        "guard_conditions": guard_conditions,
        "event_flows": event_flows,
    }
    return json.dumps(result, indent=2)


# =============================================================================
# .env file helper
# =============================================================================


def _update_env(key: str, value: str) -> None:
    """Update or add a key in the .env file."""
    from pathlib import Path

    env_path = Path(".env")
    lines = []
    found = False

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)


# =============================================================================
# Standalone runner
# =============================================================================


if __name__ == "__main__":
    mcp.run(transport="sse")
