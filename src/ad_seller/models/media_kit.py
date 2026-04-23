# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Media Kit models for inventory discovery and package management.

Packages are a curation layer on top of Products. They bundle products
for buyer discovery via the media kit. All taxonomy fields use IAB
standard identifiers as canonical values:

- Content categories: IAB Content Taxonomy v2/v3 IDs (e.g. "IAB19" for sports)
- Audience segments: IAB Audience Taxonomy 1.1 numeric IDs
- Device types: AdCOM DeviceType integers (1=Mobile, 2=PC, 3=CTV, etc.)
- Ad formats: OpenRTB imp sub-object names ("banner", "video", "native", "audio")
- Geo targets: ISO 3166-2 codes ("US", "US-NY")
- Currency: ISO 4217 codes ("USD")

Storage topology:
- Packages live in local storage (SQLite/Redis) as curation metadata
- Each PackagePlacement references a product_id which maps to ad server
  inventory via Product → InventorySegment → inventory_references
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .core import PricingModel
from .pricing_type import PricingType


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

    # IAB Audience Taxonomy 1.1 segment IDs (canonical)
    audience_segment_ids: list[str] = Field(default_factory=list)  # e.g. ["3", "4", "5"]

    # AdCOM-aligned inventory classification (canonical)
    device_types: list[int] = Field(
        default_factory=list
    )  # 1=Mobile, 2=PC, 3=CTV, 4=Phone, 5=Tablet, 6=Connected Device, 7=STB
    ad_formats: list[str] = Field(default_factory=list)  # ["banner", "video", "native", "audio"]

    # Geo targeting (ISO 3166-2)
    geo_targets: list[str] = Field(default_factory=list)  # ["US", "US-NY", "US-CA"]

    # Pricing (blended from constituent products)
    pricing_type: PricingType = PricingType.FIXED
    base_price: Optional[float] = None
    floor_price: Optional[float] = None
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


class PublicPackageView(BaseModel):
    """Tier-gated public view of a package.

    Shown to unauthenticated buyers. Contains no exact pricing,
    no placement details, no audience segment IDs.
    """

    package_id: str
    name: str
    description: Optional[str] = None
    ad_formats: list[str] = Field(default_factory=list)
    device_types: list[int] = Field(default_factory=list)
    cat: list[str] = Field(default_factory=list)
    cattax: int = 2
    geo_targets: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    price_range: str  # "$28-$42 CPM" via PricingRulesEngine
    rate_type: str = "cpm"
    is_featured: bool = False


class AuthenticatedPackageView(PublicPackageView):
    """Extended view for authenticated buyers.

    Includes exact tier-adjusted pricing, placement details,
    audience segment IDs, and negotiation availability.
    """

    exact_price: float
    floor_price: float
    currency: str = "USD"
    placements: list[PackagePlacement] = Field(default_factory=list)
    audience_segment_ids: list[str] = Field(default_factory=list)
    negotiation_enabled: bool = False
    volume_discounts_available: bool = False
