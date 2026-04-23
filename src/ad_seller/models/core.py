# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Core data models for programmatic advertising.

These models represent common ad tech entities used across OpenDirect and
other programmatic advertising protocols. They provide a shared vocabulary for:
- Deal types and pricing models
- Organizations and accounts
- Products and inventory
- Orders and line items
- Creatives and assignments
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .pricing_type import PricingType

# =============================================================================
# Enums
# =============================================================================


class OrganizationRole(str, Enum):
    """Role of an organization in the advertising ecosystem."""

    BUYER = "buyer"
    SELLER = "seller"
    AGENT = "agent"
    CURATOR = "curator"
    PLATFORM = "platform"


class AccountStatus(str, Enum):
    """Status of a buyer-seller account relationship."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class DealType(str, Enum):
    """Types of programmatic deals."""

    PROGRAMMATIC_GUARANTEED = "programmaticguaranteed"
    PREFERRED_DEAL = "preferreddeal"
    PRIVATE_AUCTION = "privateauction"


class PricingModel(str, Enum):
    """Pricing models for advertising transactions."""

    CPM = "cpm"
    CPV = "cpv"
    CPC = "cpc"
    CPCV = "cpcv"
    FLAT_FEE = "flat_fee"


class GoalType(str, Enum):
    """Types of delivery goals."""

    IMPRESSIONS = "impressions"
    CLICKS = "clicks"
    VIEWABLE_IMPRESSIONS = "viewableimpressions"
    COMPLETIONS = "completions"


class BillableEvent(str, Enum):
    """Events that trigger billing."""

    IMPRESSION = "impression"
    VIEWABLE_IMPRESSION = "viewableimpression"
    CLICK = "click"
    COMPLETION = "completion"


class RevisionType(str, Enum):
    """Type of proposal revision."""

    BUYER_AMENDMENT = "BUYER_AMENDMENT"
    SELLER_COUNTER = "SELLER_COUNTER"
    SYSTEM = "SYSTEM"


class RevisionStatus(str, Enum):
    """Status of a proposal revision."""

    DRAFT = "DRAFT"
    SENT = "SENT"
    COUNTERED = "COUNTERED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ProposalStatus(str, Enum):
    """Status of a proposal."""

    DRAFT = "draft"
    SENT = "sent"
    COUNTERED = "countered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ChangeClassification(str, Enum):
    """Classification of changes in a revision."""

    MATERIAL = "MATERIAL"
    ADMINISTRATIVE = "ADMINISTRATIVE"


class ActorType(str, Enum):
    """Type of actor making a change."""

    HUMAN = "human"
    SYSTEM = "system"
    AI_AGENT = "ai_agent"


class ExecutionOrderStatus(str, Enum):
    """Status of an execution order."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    BOOKED = "booked"
    UNBOOKED = "unbooked"
    CANCELED = "canceled"


class PlacementStatus(str, Enum):
    """Status of a placement."""

    CREATED = "created"
    ACTIVE = "active"
    INACTIVE = "inactive"
    CANCELED = "canceled"


class AdProfile(str, Enum):
    """Type of creative profile."""

    METADATA_ONLY = "metadataonly"
    FULL_ADCOM = "fulladcom"


class ReviewStatus(str, Enum):
    """Creative review status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RotationMode(str, Enum):
    """Creative rotation mode."""

    EVEN = "even"
    WEIGHTED = "weighted"


# =============================================================================
# Core Models
# =============================================================================


class Organization(BaseModel):
    """Legal/commercial entity with specific roles in the ecosystem.

    Organization roles:
    - seller: Owns/controls inventory, approves creatives, executes delivery
    - buyer: Purchases advertising, selects products, provides creatives
    - agent: Negotiates on behalf of buyer or seller
    - curator: Packages inventory (doesn't own or execute)
    - platform: Execution/infrastructure provider
    """

    model_config = ConfigDict(populate_by_name=True)

    organization_id: str = Field(alias="organizationid")
    name: str
    role: OrganizationRole
    status: str = "active"
    metadata: Optional[dict[str, Any]] = None


class Account(BaseModel):
    """Commercial relationship between a buyer and seller organization.

    Accounts establish the commercial framework for transactions between
    a specific buyer and seller pair.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str = Field(alias="accountid")
    buyer_organization_id: str = Field(alias="buyerorganizationid")
    seller_organization_id: str = Field(alias="sellerorganizationid")
    status: AccountStatus = AccountStatus.ACTIVE


class InventorySegment(BaseModel):
    """Execution-level inventory backing products.

    Inventory segments contain references to external ad server entities
    (e.g., GAM ad units) and optional structural targeting constraints.
    """

    model_config = ConfigDict(populate_by_name=True)

    inventory_segment_id: str = Field(alias="inventorysegmentid")
    inventory_references: dict[str, Any] = Field(alias="inventoryreferences")
    segment_targeting: Optional[dict[str, Any]] = Field(default=None, alias="segmenttargeting")
    segment_content: Optional[dict[str, Any]] = Field(default=None, alias="segmentcontent")


class CommercialTerms(BaseModel):
    """Commercial capabilities for a product (not binding terms)."""

    model_config = ConfigDict(populate_by_name=True)

    supported_deal_types: list[str] = Field(alias="supporteddealtypes")
    supported_pricing_models: list[str] = Field(alias="supportedpricingmodels")
    minimum_deal_value: Optional[float] = Field(default=None, alias="minimumdealvalue")
    currency: Optional[str] = None
    guarantee_allowed: Optional[bool] = Field(default=None, alias="guaranteeallowed")
    makegood_allowed: Optional[bool] = Field(default=None, alias="makegoodallowed")


class Product(BaseModel):
    """Sellable unit of inventory with intent expressed through taxonomies.

    Products combine inventory segments with targeting intent using three
    IAB taxonomies:
    - Audience Taxonomy: Who sees the ad
    - Ad Product Taxonomy: What's being advertised
    - Content Taxonomy: Where ads appear
    """

    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(alias="productid")
    seller_organization_id: str = Field(alias="sellerorganizationid")
    name: str
    description: Optional[str] = None
    inventory_segments: list[str] = Field(alias="inventorysegments")
    audience_targeting: Optional[dict[str, Any]] = Field(default=None, alias="audiencetargeting")
    ad_product_targeting: Optional[dict[str, Any]] = Field(default=None, alias="adproducttargeting")
    content_targeting: Optional[dict[str, Any]] = Field(default=None, alias="contenttargeting")
    commercial_terms: Optional[CommercialTerms] = Field(default=None, alias="commercialterms")


# =============================================================================
# Proposal Models
# =============================================================================


class ProposalThread(BaseModel):
    """Negotiation thread container for proposals.

    A thread maintains a stable identifier across all revisions of a
    proposal negotiation.
    """

    model_config = ConfigDict(populate_by_name=True)

    proposal_thread_id: str = Field(alias="proposalthreadid")
    account_id: str = Field(alias="accountid")


class RevisionCreator(BaseModel):
    """Information about who created a proposal revision."""

    organization_id: Optional[str] = Field(default=None, alias="organizationid")
    role: str  # BUYER, SELLER, SYSTEM
    actor_type: ActorType = Field(alias="actortype")


class ProposalRevision(BaseModel):
    """Immutable revision of a proposal using RFC 6902 JSON Patch.

    Each revision creates an audit trail with cryptographic hashing
    for conflict detection and change tracking.
    """

    model_config = ConfigDict(populate_by_name=True)

    proposal_revision_id: str = Field(alias="proposalrevisionid")
    proposal_thread_id: str = Field(alias="proposalthreadid")
    revision_number: int = Field(alias="revisionnumber")
    parent_revision_number: Optional[int] = Field(default=None, alias="parentrevisionnumber")
    created_at: datetime = Field(alias="createdat")
    created_by: RevisionCreator = Field(alias="createdby")
    revision_type: RevisionType = Field(alias="revisiontype")
    status: RevisionStatus
    document: dict[str, Any]
    json_patch: list[dict[str, Any]] = Field(alias="jsonpatch")
    patch_base_hash: Optional[str] = Field(default=None, alias="patchbasehash")
    resulting_hash: str = Field(alias="resultinghash")
    change_classification: ChangeClassification = Field(alias="changeclassification")


class Proposal(BaseModel):
    """Current pointer to latest proposal revision.

    The proposal entity provides a stable reference to the current
    state of a negotiation, pointing to the latest revision.
    """

    model_config = ConfigDict(populate_by_name=True)

    proposal_id: str = Field(alias="proposalid")
    proposal_thread_id: str = Field(alias="proposalthreadid")
    current_revision_number: int = Field(alias="current_revisionnumber")
    account_id: str = Field(alias="accountid")
    status: ProposalStatus
    start_date: str = Field(alias="startdate")
    end_date: str = Field(alias="enddate")
    metadata: Optional[dict[str, Any]] = None


class DeliveryGoal(BaseModel):
    """Delivery goal for a proposal line."""

    model_config = ConfigDict(populate_by_name=True)

    goal_type: GoalType = Field(alias="goaltype")
    goal_amount: int = Field(alias="goalamount")
    billable_event: BillableEvent = Field(alias="billableevent")


class Pricing(BaseModel):
    """Pricing terms for a proposal line."""

    model_config = ConfigDict(populate_by_name=True)

    pricing_type: PricingType = PricingType.FIXED
    pricing_model: Optional[PricingModel] = Field(default=None, alias="pricingmodel")
    price: Optional[float] = None
    currency: Optional[str] = None


class ProposalLine(BaseModel):
    """Individual line item within a proposal.

    Proposal lines specify the product, deal type, targeting intent,
    delivery goals, and pricing for a specific inventory request.
    """

    model_config = ConfigDict(populate_by_name=True)

    proposal_line_id: str = Field(alias="proposallineid")
    proposal_id: str = Field(alias="proposalid")
    product_id: str = Field(alias="productid")
    deal_type: DealType = Field(alias="dealtype")
    audience_targeting: Optional[dict[str, Any]] = Field(default=None, alias="audiencetargeting")
    ad_product_targeting: Optional[dict[str, Any]] = Field(default=None, alias="adproducttargeting")
    content_targeting: Optional[dict[str, Any]] = Field(default=None, alias="contenttargeting")
    delivery_goal: DeliveryGoal = Field(alias="deliverygoal")
    pricing: Pricing
    external_ids: Optional[dict[str, Any]] = Field(default=None, alias="externalids")


# =============================================================================
# Execution Models
# =============================================================================


class ExecutionOrder(BaseModel):
    """Execution container mapping to ad server orders.

    Execution orders represent the materialization of accepted proposals
    into ad server entities (GAM Orders, FreeWheel IOs).
    """

    model_config = ConfigDict(populate_by_name=True)

    execution_order_id: str = Field(alias="executionorderid")
    proposal_id: str = Field(alias="proposalid")
    status: ExecutionOrderStatus
    actions: Optional[list[str]] = None
    external_ids: dict[str, Any] = Field(alias="externalids")
    metadata: Optional[dict[str, Any]] = None


class Placement(BaseModel):
    """Execution-level delivery unit mapping to ad server line items.

    Placements represent the specific inventory allocations within an
    execution order.
    """

    model_config = ConfigDict(populate_by_name=True)

    placement_id: str = Field(alias="placementid")
    execution_order_id: str = Field(alias="executionorderid")
    inventory_segment_id: str = Field(alias="inventorysegmentid")
    status: PlacementStatus
    metadata: Optional[dict[str, Any]] = None


# =============================================================================
# Creative Models
# =============================================================================


class CreativeAsset(BaseModel):
    """Individual asset within a creative manifest."""

    model_config = ConfigDict(populate_by_name=True)

    asset_id: str = Field(alias="assetid")
    asset_url: str = Field(alias="asseturl")
    mimetype: str
    width: Optional[int] = None
    height: Optional[int] = None
    role: str  # main, companion, icon, endcard, subtitle


class CreativeManifest(BaseModel):
    """Creative metadata manifest (no executable markup)."""

    model_config = ConfigDict(populate_by_name=True)

    assets: list[CreativeAsset]
    landing_page_urls: Optional[list[str]] = Field(default=None, alias="landingpageurls")
    declared_advertiser_domains: Optional[list[str]] = Field(
        default=None, alias="declaredadvertiserdomains"
    )
    duration_ms: Optional[int] = Field(default=None, alias="durationms")
    file_size_bytes: Optional[int] = Field(default=None, alias="filesizebytes")


class ContentPolicy(BaseModel):
    """Content adjacency restrictions for a creative."""

    allowed_categories: Optional[list[str]] = Field(default=None, alias="allowedcategories")
    blocked_categories: Optional[list[str]] = Field(default=None, alias="blockedcategories")


class Creative(BaseModel):
    """Creative metadata for advertising assets.

    Creatives contain metadata only, not executable
    markup. This enables platform-agnostic creative management.
    """

    model_config = ConfigDict(populate_by_name=True)

    creative_id: str = Field(alias="creativeid")
    ad_profile: AdProfile = Field(alias="adprofile")
    creative_manifest: CreativeManifest = Field(alias="creativemanifest")
    ad_product_taxonomy: Optional[dict[str, Any]] = Field(default=None, alias="adproducttaxonomy")
    audience_taxonomy: Optional[dict[str, Any]] = Field(default=None, alias="audiencetaxonomy")
    content_policy: Optional[ContentPolicy] = Field(default=None, alias="contentpolicy")
    review_status: ReviewStatus = Field(alias="reviewstatus")
    is_placeholder: bool = Field(alias="isplaceholder")
    placeholder_type: Optional[str] = Field(default=None, alias="placeholdertype")


class Assignment(BaseModel):
    """Links creative to placement with rotation rules."""

    model_config = ConfigDict(populate_by_name=True)

    assignment_id: str = Field(alias="assignmentid")
    placement_id: str = Field(alias="placementid")
    creative_id: str = Field(alias="creativeid")
    rotation_mode: RotationMode = Field(alias="rotationmode")
    effective_start_date: str = Field(alias="effectivestartdate")
    effective_end_date: str = Field(alias="effectiveenddate")
    sov: Optional[float] = None  # Share of voice (0-1)


# =============================================================================
# Ad Server Integration Models
# =============================================================================


class EntityMapping(BaseModel):
    """Maps OpenDirect entities to ad server entities."""

    model_config = ConfigDict(populate_by_name=True)

    mapping_id: str = Field(alias="id")
    config_id: str = Field(alias="config_id")
    opendirect_type: str = Field(alias="opendirect_type")
    opendirect_id: str = Field(alias="opendirect_id")
    adserver_type: str = Field(alias="adserver_type")
    adserver_id: str = Field(alias="adserver_id")
    sync_status: str = Field(alias="sync_status")
    last_synced: Optional[datetime] = Field(default=None, alias="last_synced")
