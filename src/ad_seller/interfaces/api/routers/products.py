# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Product catalog, pricing, and discovery endpoints.

EP-12.2: the catalog wire edge speaks the shared
``iab-agentic-primitives`` contract. ``GET /products`` returns the shared
:class:`ProductListResponse` (paginated Product primitives) and
``GET /products/{product_id}`` returns the shared Product primitive
directly. Internal ``ProductDefinition`` is mapped at the boundary via
:mod:`..contract_mappers`; the catalog service is untouched.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from iab_agentic_primitives.primitives import Product
from iab_agentic_primitives.protocol import ProductListResponse

from ....services import catalog_service, quote_service
from .. import contract_mappers as cm
from .. import deps
from ..schemas import (
    AvailsRequest,
    AvailsResponse,
    DiscoveryRequest,
    InventoryTypeOverride,
    InventoryTypeOverrideResponse,
    PricingRequest,
    PricingResponse,
)

router = APIRouter()


@router.get("/products", tags=["Products"])
async def list_products(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ProductListResponse:
    """List products in the catalog (shared ProductListResponse).

    Reads from the cached static catalog (see `_get_static_product_catalog`)
    instead of running ProductSetupFlow per request — kicking off the flow
    spins up an OpenDirect MCP session that hangs in `session.initialize()`.
    Buyers filter client-side over the returned Product records (there is
    deliberately no POST /products/search on the shared catalog surface).
    """
    catalog = deps.get_product_catalog()
    return cm.products_to_list_response(
        list(catalog["products"].values()), limit=limit, offset=offset
    )


@router.post("/products/avails", response_model=AvailsResponse, tags=["Products"])
async def check_avails(request: AvailsRequest) -> AvailsResponse:
    """OpenDirect availability check for a product (camelCase wire shape).

    Called by the buyer agent's OpenDirect client (``check_avails``).
    Availability is derived honestly from the cached static catalog:
    requested impressions come from ``requestedImpressions``, else are
    budget-derived at the product CPM, else fall back to the product's
    ``minimum_impressions``; ``maximum_impressions`` (when set) caps
    availability. ``deliveryConfidence`` is always null (no forecast data
    source) and products with neither ``base_cpm`` nor ``floor_cpm`` are a
    422 — the reference implementation never fabricates numbers. The
    request's ``targeting`` field is accepted but not used for filtering.
    See :func:`ad_seller.services.catalog_service.check_avails` for the
    full policy.
    """
    catalog = deps.get_product_catalog()
    product = catalog["products"].get(request.product_id)
    if not product:
        raise HTTPException(
            status_code=404,
            detail=f"Product '{request.product_id}' not found",
        )

    result = catalog_service.check_avails(
        product,
        requested_impressions=request.requested_impressions,
        budget=request.budget,
    )
    return AvailsResponse(**result)


@router.get("/products/{product_id}", tags=["Products"])
async def get_product(product_id: str) -> Product:
    """Get a specific product (shared Product primitive, no wrapper).

    Reads from the cached static catalog instead of running ProductSetupFlow
    per request (see `list_products` for rationale).
    """
    catalog = deps.get_product_catalog()
    product = catalog["products"].get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return cm.internal_product_to_shared(product)


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

    # EP-5.2: verify the claimed tier against the agent registry and cap at
    # the verified ceiling (blocked agents 403; unverifiable claims floor).
    context = await deps._verified_buyer_context(
        endpoint="POST /pricing",
        buyer_tier=request.buyer_tier,
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
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
