# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Product catalog, pricing, and discovery endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from ....services import catalog_service, quote_service
from .. import deps
from ..schemas import (
    DiscoveryRequest,
    InventoryTypeOverride,
    InventoryTypeOverrideResponse,
    PricingRequest,
    PricingResponse,
)

router = APIRouter()


@router.get("/products", tags=["Products"])
async def list_products():
    """List all products in the catalog.

    Reads from the cached static catalog (see `_get_static_product_catalog`)
    instead of running ProductSetupFlow per request — kicking off the flow
    spins up an OpenDirect MCP session that hangs in `session.initialize()`.
    """
    catalog = deps.get_product_catalog()
    return {
        "products": [catalog_service.serialize_product(p) for p in catalog["products"].values()],
    }


@router.get("/products/{product_id}", tags=["Products"])
async def get_product(product_id: str):
    """Get a specific product.

    Reads from the cached static catalog instead of running ProductSetupFlow
    per request (see `list_products` for rationale).
    """
    catalog = deps.get_product_catalog()
    product = catalog["products"].get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return catalog_service.serialize_product(product)


@router.post("/pricing", response_model=PricingResponse, tags=["Pricing"])
async def get_pricing(
    request: PricingRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Get pricing for a product based on buyer context."""
    catalog = deps.get_product_catalog()
    product = catalog["products"].get(request.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Enforce agent registry (blocked agents get 403 before any data)
    _, max_tier = await deps._resolve_and_enforce_agent(request.agent_url)

    context = deps._build_buyer_context(
        buyer_tier=request.buyer_tier,
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
        max_access_tier=max_tier,
    )

    pricing = quote_service.get_pricing(
        product_id=request.product_id,
        product=product,
        buyer_context=context,
        volume=request.volume,
    )
    return PricingResponse(**pricing)


@router.post("/discovery", tags=["Discovery"])
async def discovery_query(
    request: DiscoveryRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Process a discovery query about inventory."""
    from ....flows import DiscoveryInquiryFlow

    # Product data from the single cached catalog source (EP-3.3)
    catalog = deps.get_product_catalog()

    # Enforce agent registry
    _, max_tier = await deps._resolve_and_enforce_agent(request.agent_url)

    # Create buyer context (API key identity overrides body params)
    context = deps._build_buyer_context(
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
        products=catalog["products"],
    )

    return response


@router.post("/api/v1/products/{product_id}/inventory-type", tags=["Products"])
async def override_inventory_type(
    product_id: str,
    request: InventoryTypeOverride,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Override the auto-detected inventory type for a product.

    Publishers can correct misclassified inventory types from ad server sync
    or apply custom categorization. The override persists across future syncs.
    """
    result = await catalog_service.override_inventory_type(
        product_id=product_id,
        inventory_type=request.inventory_type,
        reason=request.reason,
    )

    return InventoryTypeOverrideResponse(
        product_id=product_id,
        previous_type=result["previous_type"],
        new_type=request.inventory_type,
        applied_at=result["applied_at"],
    )


@router.get("/api/v1/products/{product_id}/inventory-type", tags=["Products"])
async def get_inventory_type_override(product_id: str):
    """Get the current inventory type override for a product, if any."""
    override = await catalog_service.get_inventory_type_override(product_id)

    if not override:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "no_override",
                "message": f"No inventory type override for product '{product_id}'.",
            },
        )

    return override


@router.delete("/api/v1/products/{product_id}/inventory-type", tags=["Products"])
async def delete_inventory_type_override(product_id: str):
    """Remove an inventory type override, reverting to auto-detected type."""
    removed = await catalog_service.delete_inventory_type_override(product_id)

    if not removed:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "no_override",
                "message": f"No inventory type override for product '{product_id}'.",
            },
        )

    return {"status": "removed", "product_id": product_id}
