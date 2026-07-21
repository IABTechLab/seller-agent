# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Media kit (public) and package management (authenticated) endpoints."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import deps
from ..schemas import DynamicPackageRequest, MediaKitSearchRequest, PackageCreateRequest

router = APIRouter()

_VALID_AUDIENCE_TYPES = {"standard", "contextual", "agentic"}


def _build_audience_filter(
    audience_type: Optional[str],
    audience_id: Optional[str],
    audience_taxonomy_version: Optional[str],
):
    """Convert raw query params to an `AudienceFilter`, or None if all unset.

    Validates `audience_type` and the type/id pairing rules:

    - Returns None when all three params are unset (skip filtering).
    - 400 when `audience_type` is set but unrecognized.
    - 400 when `audience_id` is set without `audience_type` (no corpus to
      search in).

    Per scope: agentic per-segment filtering is §11's
    territory; agentic+id collapses to "package supports agentic" at this
    stage and the filter accepts the param without error so existing buyer
    code doesn't have to special-case the type.
    """

    from ....engines.media_kit_service import AudienceFilter

    if audience_type is None and audience_id is None and audience_taxonomy_version is None:
        return None

    if audience_type is not None and audience_type not in _VALID_AUDIENCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid audience_type: {audience_type!r}. Must be one of "
                f"{sorted(_VALID_AUDIENCE_TYPES)}."
            ),
        )

    if audience_id is not None and audience_type is None:
        raise HTTPException(
            status_code=400,
            detail="audience_id requires audience_type to disambiguate corpus.",
        )

    return AudienceFilter(
        audience_type=audience_type,
        audience_id=audience_id,
        taxonomy_version=audience_taxonomy_version,
    )


# =============================================================================
# Media Kit Endpoints (Public — no auth required)
# =============================================================================


@router.get("/media-kit", tags=["Media Kit"])
async def media_kit_overview():
    """Public media kit catalog overview."""
    service = await deps._get_media_kit_service()
    packages = await service.list_packages_public()
    featured = [p for p in packages if p.is_featured]

    return {
        "total_packages": len(packages),
        "featured_count": len(featured),
        "featured": [p.model_dump() for p in featured],
        "all_packages": [p.model_dump() for p in packages],
    }


@router.get("/media-kit/packages", tags=["Media Kit"])
async def list_media_kit_packages(
    layer: Optional[str] = None,
    featured_only: bool = False,
    audience_type: Optional[str] = None,
    audience_id: Optional[str] = None,
    audience_taxonomy_version: Optional[str] = None,
):
    """List packages with public view (price ranges, no exact pricing).

    Accepts the same audience-filter triple as `GET /packages` so public
    discovery callers can narrow by audience type without authenticating.
    """
    from ....models.media_kit import PackageLayer

    pkg_layer = None
    if layer:
        try:
            pkg_layer = PackageLayer(layer)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid layer: {layer}")

    audience_filter = _build_audience_filter(audience_type, audience_id, audience_taxonomy_version)

    service = await deps._get_media_kit_service()
    packages = await service.list_packages_public(
        layer=pkg_layer,
        featured_only=featured_only,
        audience_filter=audience_filter,
    )
    return {"packages": [p.model_dump() for p in packages]}


@router.get("/media-kit/packages/{package_id}", tags=["Media Kit"])
async def get_media_kit_package(package_id: str):
    """Get a single package with public view."""
    service = await deps._get_media_kit_service()
    package = await service.get_package_public(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package.model_dump()


@router.post("/media-kit/search", tags=["Media Kit"])
async def search_media_kit(
    request: MediaKitSearchRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Search packages by keyword. Authenticated buyers get richer results.

    Per proposal §5.7, the scoring corpus now includes
    `audience_capabilities.standard_segment_ids` +
    `audience_capabilities.contextual_segment_ids` alongside keywords/tags
    -- a query mentioning a known IAB segment ID ranks packages that
    declare it higher than packages that don't.

    The optional `audience_filter` body field restricts results to packages
    that match its type/id/version triple, parallel to `GET /packages`.
    """
    context = None
    if api_key_record is not None or request.buyer_tier != "public":
        context = deps._build_buyer_context(
            buyer_tier=request.buyer_tier,
            agency_id=request.agency_id,
            advertiser_id=request.advertiser_id,
            api_key_record=api_key_record,
        )

    audience_filter = None
    if request.audience_filter is not None:
        audience_filter = _build_audience_filter(
            request.audience_filter.audience_type,
            request.audience_filter.audience_id,
            request.audience_filter.taxonomy_version,
        )

    service = await deps._get_media_kit_service()
    results = await service.search_packages(
        request.query, buyer_context=context, audience_filter=audience_filter
    )
    return {"results": [r.model_dump() for r in results]}


# =============================================================================
# Package Endpoints (Authenticated / Admin)
# =============================================================================


@router.get("/packages", tags=["Packages"])
async def list_packages(
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    layer: Optional[str] = None,
    audience_type: Optional[str] = None,
    audience_id: Optional[str] = None,
    audience_taxonomy_version: Optional[str] = None,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """List packages with tier-gated view.

    Audience filter (proposal §5.7):

    - `audience_type`: one of `standard` | `contextual` | `agentic`.
    - `audience_id`: taxonomy ID for standard/contextual; URI for agentic.
      Requires `audience_type` to disambiguate which capability list to
      search.
    - `audience_taxonomy_version`: optional version constraint; when unset
      the seller's lock-file version is authoritative.

    Empty results return `[]`, not 404 -- matches the existing behavior for
    layer/featured filters.
    """
    from ....models.media_kit import PackageLayer

    pkg_layer = None
    if layer:
        try:
            pkg_layer = PackageLayer(layer)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid layer: {layer}")

    audience_filter = _build_audience_filter(audience_type, audience_id, audience_taxonomy_version)

    service = await deps._get_media_kit_service()

    if api_key_record is None and buyer_tier == "public":
        packages = await service.list_packages_public(
            layer=pkg_layer, audience_filter=audience_filter
        )
    else:
        context = deps._build_buyer_context(
            buyer_tier=buyer_tier,
            agency_id=agency_id,
            advertiser_id=advertiser_id,
            api_key_record=api_key_record,
        )
        packages = await service.list_packages_authenticated(
            context, layer=pkg_layer, audience_filter=audience_filter
        )

    return {"packages": [p.model_dump() for p in packages]}


@router.get("/packages/{package_id}", tags=["Packages"])
async def get_package(
    package_id: str,
    buyer_tier: str = "public",
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Get a single package with tier-gated view."""
    service = await deps._get_media_kit_service()

    if api_key_record is None and buyer_tier == "public":
        package = await service.get_package_public(package_id)
    else:
        context = deps._build_buyer_context(
            buyer_tier=buyer_tier,
            agency_id=agency_id,
            advertiser_id=advertiser_id,
            api_key_record=api_key_record,
        )
        package = await service.get_package_authenticated(package_id, context)

    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package.model_dump()


@router.post("/packages", tags=["Packages"])
async def create_package(request: PackageCreateRequest):
    """Create a curated package (Layer 2)."""
    import uuid as _uuid

    from ....events.helpers import emit_event
    from ....events.models import EventType
    from ....models.media_kit import Package, PackageLayer, PackagePlacement, PackageStatus
    from ....storage.factory import get_storage

    storage = await get_storage()

    # Build placements from product_ids
    placements = []
    for pid in request.product_ids:
        prod_data = await storage.get_product(pid)
        if prod_data:
            from ....models.flow_state import ProductDefinition

            prod = ProductDefinition(**prod_data)
            placements.append(
                PackagePlacement(
                    product_id=prod.product_id,
                    product_name=prod.name,
                    ad_formats=request.ad_formats or _default_ad_formats(prod.inventory_type),
                    device_types=request.device_types or _default_device_types(prod.inventory_type),
                )
            )

    # Build kwargs for Package -- prefer the new typed audience_capabilities
    # when supplied; otherwise pass the legacy audience_segment_ids and let
    # the Package's model_validator(mode='before') shim migrate it.
    package_kwargs: dict[str, Any] = {
        "package_id": f"pkg-{_uuid.uuid4().hex[:8]}",
        "name": request.name,
        "description": request.description,
        "layer": PackageLayer.CURATED,
        "status": PackageStatus.ACTIVE,
        "placements": placements,
        "cat": request.cat,
        "cattax": request.cattax,
        "device_types": request.device_types,
        "ad_formats": request.ad_formats,
        "geo_targets": request.geo_targets,
        "base_price": request.base_price,
        "floor_price": request.floor_price,
        "tags": request.tags,
        "is_featured": request.is_featured,
        "seasonal_label": request.seasonal_label,
    }
    if request.audience_capabilities is not None:
        package_kwargs["audience_capabilities"] = request.audience_capabilities
    elif request.audience_segment_ids:
        # Legacy path: forward the flat list, shim will fold it into
        # audience_capabilities at validation time.
        package_kwargs["audience_segment_ids"] = request.audience_segment_ids

    package = Package(**package_kwargs)

    service = await deps._get_media_kit_service()
    created = await service.create_package(package)

    await emit_event(
        event_type=EventType.PACKAGE_CREATED,
        payload={"package_id": created.package_id, "name": created.name, "layer": "curated"},
    )

    return created.model_dump(mode="json")


@router.put("/packages/{package_id}", tags=["Packages"])
async def update_package(package_id: str, updates: dict[str, Any]):
    """Update an existing package."""
    from ....events.helpers import emit_event
    from ....events.models import EventType

    service = await deps._get_media_kit_service()
    package = await service.update_package(package_id, updates)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    await emit_event(
        event_type=EventType.PACKAGE_UPDATED,
        payload={"package_id": package_id, "updated_fields": list(updates.keys())},
    )

    return package.model_dump(mode="json")


@router.delete("/packages/{package_id}", tags=["Packages"])
async def delete_package(package_id: str):
    """Archive a package (soft delete)."""
    service = await deps._get_media_kit_service()
    deleted = await service.delete_package(package_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Package not found")
    return {"package_id": package_id, "status": "archived"}


@router.post("/packages/assemble", tags=["Packages"])
async def assemble_package(request: DynamicPackageRequest):
    """Assemble a dynamic package (Layer 3) from product IDs."""
    service = await deps._get_media_kit_service()
    package = await service.assemble_dynamic_package(request.name, request.product_ids)
    if not package:
        raise HTTPException(status_code=400, detail="No valid products found for assembly")
    return package.model_dump(mode="json")


@router.post("/packages/sync", tags=["Packages"])
async def sync_packages():
    """Trigger ad server inventory sync (Layer 1)."""
    from ....events.helpers import emit_event
    from ....events.models import EventType
    from ....flows import ProductSetupFlow

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
