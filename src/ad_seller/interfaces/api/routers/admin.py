# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Operator/admin endpoints: root & health, event log inspection, API key
lifecycle, supply-chain self-description, rate card, and inventory sync."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..schemas import (
    CreateApiKeyRequest,
    RateCardEntry,
    RateCardResponse,
    SupplyChainNodeModel,
    SupplyChainResponse,
)

router = APIRouter()


@router.get("/", tags=["Core"])
async def root():
    """API root."""
    return {
        "name": "Ad Seller System API",
        "version": "0.1.0",
        "docs": "/docs",
    }


@router.get("/health", tags=["Core"])
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


# =============================================================================
# Event Endpoints
# =============================================================================


@router.get("/events", tags=["Events"])
async def list_events(
    flow_id: Optional[str] = None,
    event_type: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 50,
):
    """List events, optionally filtered by flow_id, event_type, or session_id."""
    from ....events.bus import get_event_bus

    bus = await get_event_bus()
    events = await bus.list_events(
        flow_id=flow_id, event_type=event_type, session_id=session_id, limit=limit
    )
    return {"events": [e.model_dump(mode="json") for e in events]}


@router.get("/events/{event_id}", tags=["Events"])
async def get_event(event_id: str):
    """Get a specific event by ID."""
    from ....events.bus import get_event_bus

    bus = await get_event_bus()
    event = await bus.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.model_dump(mode="json")


# =============================================================================
# API Key Management Endpoints (Operator-facing)
# =============================================================================


@router.post("/auth/api-keys", tags=["Authentication"])
async def create_api_key(request: CreateApiKeyRequest):
    """Create a new API key for a buyer.

    The response contains the full API key which is shown ONLY ONCE.
    Store it securely — it cannot be retrieved again.
    """
    from ....auth.api_key_service import ApiKeyService
    from ....models.api_key import ApiKeyCreateRequest
    from ....storage.factory import get_storage

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


@router.get("/auth/api-keys", tags=["Authentication"])
async def list_api_keys():
    """List all API keys (metadata only, no secrets)."""
    from ....auth.api_key_service import ApiKeyService
    from ....storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)
    keys = await service.list_keys()
    return {
        "keys": [k.model_dump(mode="json") for k in keys],
        "total": len(keys),
    }


@router.get("/auth/api-keys/{key_id}", tags=["Authentication"])
async def get_api_key_details(key_id: str):
    """Get details for a specific API key."""
    from ....auth.api_key_service import ApiKeyService
    from ....storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)
    info = await service.get_key_info(key_id)
    if not info:
        raise HTTPException(status_code=404, detail="API key not found")
    return info.model_dump(mode="json")


@router.delete("/auth/api-keys/{key_id}", tags=["Authentication"])
async def revoke_api_key(key_id: str):
    """Revoke an API key. Revoked keys return 401 on use."""
    from ....auth.api_key_service import ApiKeyService
    from ....storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)
    revoked = await service.revoke_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"key_id": key_id, "status": "revoked"}


# =============================================================================
# Supply Chain Transparency (Deal Library Phase 4)
# =============================================================================


@router.get("/api/v1/supply-chain", tags=["Supply Chain"], response_model=SupplyChainResponse)
async def get_supply_chain():
    """Return sellers.json-based self-description of this seller instance.

    If SELLERS_JSON_PATH is configured, parses the real sellers.json file
    per IAB spec. Otherwise returns a default single-node chain.
    Also includes an OpenRTB-compatible schain object.
    """
    from ....config import get_settings
    from ....models.supply_chain import build_schain_from_sellers_json, load_sellers_json

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
# Rate Card Management
# =============================================================================


@router.get("/api/v1/rate-card", tags=["Pricing"])
async def get_rate_card():
    """Get the current rate card (base CPMs by inventory type).

    The rate card drives floor pricing during inventory sync and
    deal creation. Can be updated via PUT to reflect ad server rate cards.
    """
    from ....storage.factory import get_storage

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


@router.put("/api/v1/rate-card", tags=["Pricing"])
async def update_rate_card(entries: list[RateCardEntry]):
    """Update the rate card with current base CPMs from ad server.

    Publishers should update this when their ad server rate cards change.
    The pricing engine uses these values as base prices before applying
    tier discounts and volume adjustments.
    """
    from ....storage.factory import get_storage

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


@router.get("/api/v1/inventory-sync/status", tags=["Core"])
async def get_inventory_sync_status():
    """Get the current status of the periodic inventory sync scheduler."""
    from ....services.inventory_sync_scheduler import get_sync_status

    return get_sync_status()


@router.post("/api/v1/inventory-sync/trigger", tags=["Core"])
async def trigger_inventory_sync(
    incremental: bool = False,
):
    """Manually trigger an inventory sync.

    Args:
        incremental: If true, only sync items changed since last sync
            (based on stored sync watermark). Full sync if false or no
            previous watermark exists.
    """
    from ....config import get_settings
    from ....services.inventory_sync_scheduler import _run_sync
    from ....storage.factory import get_storage

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


@router.get("/api/v1/inventory-sync/watermark", tags=["Core"])
async def get_sync_watermark():
    """Get the last sync watermark (used for incremental sync)."""
    from ....storage.factory import get_storage

    storage = await get_storage()
    watermark = await storage.get("sync_watermark:inventory")

    if not watermark:
        return {"last_sync_at": None, "message": "No sync has been performed yet."}

    return watermark
