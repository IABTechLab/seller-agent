# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Request/response models for the REST API.

Extracted verbatim from ``interfaces/api/main.py`` (EP-3.1). Wire shapes
are unchanged — these are the same Pydantic models the endpoints have
always used.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


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
    pricing_verified: bool = False
    pricing_verification_reason: str = ""
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


class AvailsRequest(BaseModel):
    """OpenDirect availability check request (POST /products/avails).

    Spec-named fields use the OpenDirect 2.1 all-lowercase wire names
    (``productid``/``startdate``/``enddate``), matching the buyer agent's
    OpenDirect client (``AvailsRequest`` in the buyer's models; coordinated
    Tier-1 rename). Non-spec extension fields
    (``requestedImpressions``/``budget``/``targeting``) are unchanged
    pending the Tier-2 restructure. ``targeting`` is accepted for wire
    compatibility but not used for filtering in this reference
    implementation — the static catalog has no per-slice availability data.
    """

    product_id: str = Field(..., alias="productid")
    start_date: datetime = Field(..., alias="startdate")
    end_date: datetime = Field(..., alias="enddate")
    requested_impressions: Optional[int] = Field(
        default=None, alias="requestedImpressions", ge=0
    )
    budget: Optional[float] = None
    targeting: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _end_after_start(self) -> "AvailsRequest":
        start, end = self.start_date, self.end_date
        # Normalize mixed naive/aware datetimes (treat naive as UTC) so the
        # comparison never raises.
        if (start.tzinfo is None) != (end.tzinfo is None):
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
        if end <= start:
            raise ValueError("enddate must be after startdate")
        return self


class AvailsResponse(BaseModel):
    """OpenDirect availability check response.

    ``productid`` follows the OpenDirect 2.1 spec-lowercase wire name
    (coordinated Tier-1 rename); the non-spec extension
    fields keep their camelCase names pending the Tier-2 restructure.
    ``deliveryConfidence`` is always null: the seller has no delivery
    forecast data source, and the reference implementation does not
    fabricate one.
    """

    product_id: str = Field(..., alias="productid")
    available_impressions: int = Field(..., alias="availableImpressions")
    guaranteed_impressions: Optional[int] = Field(default=None, alias="guaranteedImpressions")
    estimated_cpm: float = Field(..., alias="estimatedCpm")
    total_cost: float = Field(..., alias="totalCost")
    delivery_confidence: Optional[float] = Field(
        default=None, alias="deliveryConfidence", ge=0, le=100
    )
    available_targeting: Optional[list[str]] = Field(default=None, alias="availableTargeting")

    model_config = {"populate_by_name": True}


class DiscoveryRequest(BaseModel):
    """Discovery query request."""

    query: str
    buyer_tier: str = "public"
    agency_id: Optional[str] = None
    agent_url: Optional[str] = None


class PackageCreateRequest(BaseModel):
    """Request to create a curated package.

    Accepts the new typed `audience_capabilities` shape (proposal §5.7).
    Legacy callers may still send `audience_segment_ids: list[str]` as
    flat input -- the field is retained as deprecated and will be folded
    into `audience_capabilities.standard_segment_ids` (with implicit
    AT 1.1) by `create_package`.
    """

    name: str
    description: Optional[str] = None
    product_ids: list[str] = []
    cat: list[str] = []
    cattax: int = 2
    # New typed shape. Optional so legacy callers that only send
    # audience_segment_ids do not break. When None and audience_segment_ids
    # is present, create_package builds the capabilities dict from the
    # legacy field.
    audience_capabilities: Optional[dict] = None
    # Deprecated; retained for backward compat. Folded into
    # audience_capabilities.standard_segment_ids when audience_capabilities
    # is None.
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


class AudienceFilterModel(BaseModel):
    """Optional audience filter sub-object on `POST /media-kit/search`.

    Mirrors the query-param triple on `GET /packages`: type + id + version.
    When present, search results are restricted to packages whose
    `audience_capabilities` match. See proposal §5.7.
    """

    audience_type: Optional[str] = None
    audience_id: Optional[str] = None
    taxonomy_version: Optional[str] = None


class MediaKitSearchRequest(BaseModel):
    """Request to search packages."""

    query: str
    buyer_tier: str = "public"
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    audience_filter: Optional[AudienceFilterModel] = None


class CounterOfferRequest(BaseModel):
    """Request to submit a counter-offer in a negotiation."""

    buyer_price: float
    buyer_tier: str = "public"
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    # EP-5.2: buyer agent URL for registry trust verification. Without it
    # (and without an API key) self-asserted tier claims floor to PUBLIC.
    agent_url: Optional[str] = None


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
    """API request model for POST /api/v1/deals.

    `audience_plan` is optional and follows the wire shape documented at
    `docs/api/audience_plan_wire_format.md`. When present the seller
    pre-flights it against its own `audience_capabilities` and rejects
    with a structured `audience_plan_unsupported` error if any part
    cannot be honored (proposal §5.7 layer 3).
    """

    quote_id: str
    buyer_identity: Optional[QuoteBuyerIdentityModel] = None
    notes: Optional[str] = None
    audience_plan: Optional[dict] = None


class AgenticAudienceMatchRequest(BaseModel):
    """API request model for POST /agentic-audience/match (proposal §5.7).

    Accepts a single `AudienceRef` (must be `type=agentic`) and an optional
    package_id scope. Returns a deterministic mock-quality match score and
    quality bucket. Real model is Epic 2 / E2-2.
    """

    audience_ref: dict
    package_id: Optional[str] = None


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


class DiscoverAgentRequest(BaseModel):
    """Request to discover an agent by URL."""

    agent_url: str


class UpdateTrustRequest(BaseModel):
    """Request to update an agent's trust status."""

    trust_status: str  # TrustStatus value
    notes: Optional[str] = None


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
    # EP-5.2: buyer agent URL for registry trust verification. When present,
    # the registry-verified ceiling caps even the API key's identity tier.
    agent_url: Optional[str] = None


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


class DealDeprecationRequest(BaseModel):
    """Request to deprecate a deal."""

    reason: str
    replacement_deal_id: Optional[str] = None  # If replaced by another deal
