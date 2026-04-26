# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Media Kit models for inventory discovery and package management.

Packages are a curation layer on top of Products. They bundle products
for buyer discovery via the media kit. All taxonomy fields use IAB
standard identifiers as canonical values:

- Content categories: IAB Content Taxonomy v2/v3 IDs (e.g. "IAB19" for sports)
- Audience capabilities: typed AudienceCapabilities (standard/contextual/agentic)
- Device types: AdCOM DeviceType integers (1=Mobile, 2=PC, 3=CTV, etc.)
- Ad formats: OpenRTB imp sub-object names ("banner", "video", "native", "audio")
- Geo targets: ISO 3166-2 codes ("US", "US-NY")
- Currency: ISO 4217 codes ("USD")

Storage topology:
- Packages live in local storage (SQLite/Redis) as curation metadata
- Each PackagePlacement references a product_id which maps to ad server
  inventory via Product → InventorySegment → inventory_references
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from .audience_capabilities import AgenticCapabilities, AudienceCapabilities
from .core import PricingModel

# Migration log for legacy `audience_segment_ids` -> `audience_capabilities`
# coming through StorageBackend rows or seed flows. Quiet INFO on a dedicated
# logger so callers can filter or count it.
_migration_logger = logging.getLogger("ad_seller.audience.migration")


class PackageLayer(str, Enum):
    """Layer indicating how a package was created."""

    SYNCED = "synced"  # Layer 1: imported from ad server
    CURATED = "curated"  # Layer 2: seller-created
    DYNAMIC = "dynamic"  # Layer 3: agent-assembled on the fly


class PackageStatus(str, Enum):
    """Lifecycle status of a package."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class PackagePlacement(BaseModel):
    """A product within a package with its inventory characteristics."""

    product_id: str
    product_name: str
    ad_formats: list[str] = Field(default_factory=list)  # ["banner", "video", "native", "audio"]
    device_types: list[int] = Field(default_factory=list)  # AdCOM DeviceType ints
    weight: float = 1.0  # relative weight in package


class Package(BaseModel):
    """Curated inventory package for media kit discovery.

    Uses IAB taxonomy IDs as canonical values. Human-readable descriptions
    are derived from taxonomy lookups at the API/presentation layer.
    """

    package_id: str  # "pkg-{uuid8}"
    name: str
    description: Optional[str] = None
    layer: PackageLayer
    status: PackageStatus = PackageStatus.DRAFT

    # Constituent products
    placements: list[PackagePlacement] = Field(default_factory=list)

    # IAB Content Taxonomy categories (canonical)
    cat: list[str] = Field(default_factory=list)  # e.g. ["IAB19", "IAB19-29"]
    cattax: int = 2  # 1=CT1.0, 2=CT2.0, 3=CT3.0

    # Typed audience capability declaration -- replaces the flat
    # `audience_segment_ids: list[str]` field. Carries standard segments,
    # contextual-as-audience-intent, and optional agentic capabilities.
    # See proposal §5.7 / bead ar-roi5.
    audience_capabilities: AudienceCapabilities = Field(default_factory=AudienceCapabilities)

    # AdCOM-aligned inventory classification (canonical)
    device_types: list[int] = Field(
        default_factory=list
    )  # 1=Mobile, 2=PC, 3=CTV, 4=Phone, 5=Tablet, 6=Connected Device, 7=STB
    ad_formats: list[str] = Field(default_factory=list)  # ["banner", "video", "native", "audio"]

    # Geo targeting (ISO 3166-2)
    geo_targets: list[str] = Field(default_factory=list)  # ["US", "US-NY", "US-CA"]

    # Pricing (blended from constituent products)
    base_price: float
    floor_price: float
    rate_type: PricingModel = PricingModel.CPM
    currency: str = "USD"  # ISO 4217

    # Human-readable tags for search/display (NOT taxonomy replacements)
    tags: list[str] = Field(default_factory=list)  # ["premium", "sports", "live events"]

    # Curation metadata
    is_featured: bool = False
    seasonal_label: Optional[str] = None
    ad_server_source: Optional[str] = None  # "gam", "freewheel", None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_audience_segment_ids(cls, data: Any) -> Any:
        """Backward-compat shim: legacy `audience_segment_ids` -> typed shape.

        Accepts inputs containing the deprecated flat `audience_segment_ids:
        list[str]` (from old SQLite rows, seed flows, or external callers
        that have not yet migrated) and rewrites them into the typed
        `audience_capabilities` field with `standard_taxonomy_version="1.1"`
        (the implicit version the legacy field always carried).

        Logs each conversion at INFO on `ad_seller.audience.migration` so
        operators can monitor the deprecation runway.

        No-op when:
        - input is not a dict (e.g., already a Package instance)
        - `audience_segment_ids` is absent
        - both `audience_segment_ids` and `audience_capabilities` are
          provided (caller is explicitly setting both -- last write wins,
          but we drop the legacy field so it never gets stored)
        """

        if not isinstance(data, dict):
            return data
        if "audience_segment_ids" not in data:
            return data

        legacy = data.pop("audience_segment_ids")

        # If caller already supplied audience_capabilities, prefer it and
        # just drop the legacy alias (don't clobber explicit config).
        if "audience_capabilities" in data:
            _migration_logger.info(
                "Dropping legacy audience_segment_ids=%r in favor of "
                "explicit audience_capabilities for package %s",
                legacy,
                data.get("package_id", "<unknown>"),
            )
            return data

        legacy_list = list(legacy) if legacy else []
        data["audience_capabilities"] = {
            "standard_segment_ids": legacy_list,
            "standard_taxonomy_version": "1.1",
        }
        _migration_logger.info(
            "Migrated legacy audience_segment_ids=%r -> "
            "audience_capabilities(standard, AT 1.1) for package %s",
            legacy_list,
            data.get("package_id", "<unknown>"),
        )
        return data


class AudienceCapabilityPublicSummary(BaseModel):
    """Capability-discovery view of a package's audience capabilities.

    Public-tier callers see only the *shape* of what the package can target:
    taxonomy versions and "supports X?" flags. The segment lists themselves
    are not disclosed -- they live behind the authenticated tier (proposal
    §5.7: "Public view exposes capabilities only, Authenticated view exposes
    segment lists"). This is the same separation `cat`/`cattax` already
    has at the content layer, extended to audience.
    """

    standard_taxonomy_version: str = "1.1"
    contextual_taxonomy_version: str = "3.1"
    supports_standard: bool = False
    supports_contextual: bool = False
    supports_agentic: bool = False
    agentic_spec_version: Optional[str] = None


def _public_summary_from_capabilities(
    caps: AudienceCapabilities,
) -> AudienceCapabilityPublicSummary:
    """Project full AudienceCapabilities -> public capability summary."""

    return AudienceCapabilityPublicSummary(
        standard_taxonomy_version=caps.standard_taxonomy_version,
        contextual_taxonomy_version=caps.contextual_taxonomy_version,
        supports_standard=bool(caps.standard_segment_ids),
        supports_contextual=bool(caps.contextual_segment_ids),
        supports_agentic=caps.agentic_capabilities is not None,
        agentic_spec_version=(
            caps.agentic_capabilities.spec_version
            if caps.agentic_capabilities is not None
            else None
        ),
    )


class PublicPackageView(BaseModel):
    """Tier-gated public view of a package.

    Shown to unauthenticated buyers. Contains no exact pricing,
    no placement details, and ONLY capability metadata for audience
    (taxonomy versions + supports-X flags); no segment lists.
    """

    package_id: str
    name: str
    description: Optional[str] = None
    ad_formats: list[str] = Field(default_factory=list)
    device_types: list[int] = Field(default_factory=list)
    cat: list[str] = Field(default_factory=list)
    cattax: int = 2
    # Capability metadata only -- versions + supports flags. Segment lists
    # are deliberately absent at this tier.
    audience_capabilities: AudienceCapabilityPublicSummary = Field(
        default_factory=AudienceCapabilityPublicSummary
    )
    geo_targets: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    price_range: str  # "$28-$42 CPM" via PricingRulesEngine
    rate_type: str = "cpm"
    is_featured: bool = False


class AuthenticatedPackageView(PublicPackageView):
    """Extended view for authenticated buyers.

    Includes exact tier-adjusted pricing, placement details, the full typed
    `audience_capabilities` object (with segment lists), and negotiation
    availability.

    Note: `audience_capabilities` is *redeclared* here as the full
    `AudienceCapabilities` type (overriding the public summary on the base
    class) so authenticated callers see the segment lists.
    """

    exact_price: float
    floor_price: float
    currency: str = "USD"
    placements: list[PackagePlacement] = Field(default_factory=list)
    audience_capabilities: AudienceCapabilities = Field(
        default_factory=AudienceCapabilities
    )
    negotiation_enabled: bool = False
    volume_discounts_available: bool = False
