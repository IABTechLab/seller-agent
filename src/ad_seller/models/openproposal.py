# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenProposal 3.0 (draft 1) objects and the GAM payloads mapped into them.

Field names follow ``spec/openproposal-3.0.md`` in IABTechLab/OpenProposal.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProposalType(str, Enum):
    STANDARD = "standard"
    CUSTOM = "custom"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UNDER_REVIEW = "under_review"
    AGREED = "agreed"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class LineItemStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UNDER_REVIEW = "under_review"
    AGREED = "agreed"
    WITHDRAWN = "withdrawn"
    SOLD_OUT = "sold_out"
    EXPIRED = "expired"


class Channel(str, Enum):
    DISPLAY = "display"
    VIDEO = "video"
    AUDIO = "audio"
    CTV = "ctv"
    DOOH = "dooh"
    NATIVE = "native"


class TransactionMechanism(str, Enum):
    DIRECT_IO = "DIRECT_IO"
    PROGRAMMATIC_GUARANTEED = "PROGRAMMATIC_GUARANTEED"
    PREFERRED_DEAL = "PREFERRED_DEAL"
    PRIVATE_AUCTION = "PRIVATE_AUCTION"
    OPEN_AUCTION = "OPEN_AUCTION"


class HoldState(str, Enum):
    NONE = "none"
    REQUESTED = "requested"
    HELD = "held"
    EXPIRED = "expired"
    RELEASED = "released"
    CONVERTED = "converted"


class Source(str, Enum):
    """Where a mapped field's value came from."""

    AD_SERVER = "ad_server"
    SELLER_CATALOG = "seller_catalog"
    SELLER_CONFIG = "seller_config"
    QUOTE = "quote"
    BOOKED_DEAL = "booked_deal"
    INFERRED = "inferred"
    NOT_AVAILABLE = "not_available"


class Selectable(BaseModel):
    """Buyer picks from ``available``; ``included`` is the default selection."""

    available: list[Any]
    included: list[Any] = Field(default_factory=list)
    pricing: Optional[dict[str, dict[str, float]]] = None
    max_select: Optional[int] = None


class Settable(BaseModel):
    """Buyer sets a value within ``allowed`` or ``min``/``max``."""

    allowed: Optional[list[Any]] = None
    min: Optional[float] = None
    max: Optional[float] = None


class Property(BaseModel):
    id: str
    name: Optional[str] = None
    domains: Optional[list[str]] = None
    apps: Optional[list[dict[str, Any]]] = None
    supported_formats: Optional[list[str]] = None


class Audience(BaseModel):
    id: str
    name: Optional[str] = None
    taxonomy_ref: Optional[str] = None
    provenance: Optional[str] = None
    addressable_scale: Optional[int] = None


class PricingEntry(BaseModel):
    cost_method: str
    gross_rate: Optional[float] = None
    agreed_rate: Optional[float] = None
    seller_rate: Optional[float] = None
    floor: Optional[float] = None
    price_valid_until: Optional[str] = None
    currency: str = "USD"


class Availability(BaseModel):
    unit: str = "impressions"
    basis: Optional[str] = None
    as_of: Optional[str] = None
    granularity: Optional[str] = None
    type: str = "forecast"
    quantity: Optional[int] = None


class CommitmentBounds(BaseModel):
    min_impressions: Optional[int] = None
    max_impressions: Optional[int] = None
    min_budget: Optional[float] = None
    max_budget: Optional[float] = None


class Commitment(BaseModel):
    basis: str
    amount: float
    currency: Optional[str] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    derived_units: Optional[int] = None
    idempotency_key: Optional[str] = None


class HoldStatus(BaseModel):
    hold_available: bool = False
    hold_duration: Optional[str] = None
    hold_scope: Optional[str] = None
    state: HoldState = HoldState.NONE
    hold_expires_at: Optional[str] = None


class FrequencyCap(BaseModel):
    max: Settable
    period: str
    basis: Optional[str] = None


class CommittedMetric(BaseModel):
    metric: str
    value: float
    basis: Optional[str] = None


class ExternalReference(BaseModel):
    system: str
    namespace: Optional[str] = None
    id: str
    role: str


class RequiredCredential(BaseModel):
    type: str
    scheme: str
    issuer: Optional[str] = None
    id_required: bool = True


class LineItem(BaseModel):
    """OpenProposal line item (spec section 5)."""

    model_config = ConfigDict(use_enum_values=True)

    line_item_id: str
    status: LineItemStatus
    channel: Channel

    properties: Optional[Selectable] = None
    content_detail: Optional[str] = None
    environments: Optional[Selectable] = None

    audiences: Optional[Selectable] = None
    addressable_scale: Optional[int] = None
    activation_route: Optional[Selectable] = None
    buyer_supplied: Optional[dict[str, Any]] = None

    formats: Optional[Selectable] = None
    specs: Optional[dict[str, Any]] = None
    third_party_tags: Optional[dict[str, Any]] = None
    creative_delivery: Optional[dict[str, Any]] = None
    approval: Optional[dict[str, Any]] = None
    assets: Optional[list[dict[str, Any]]] = None
    materials_due: Optional[str] = None
    production: Optional[Selectable] = None

    geo: Optional[Settable] = None
    device: Optional[Settable] = None
    daypart: Optional[Settable] = None
    frequency_cap: Optional[FrequencyCap] = None

    pricing: list[PricingEntry] = Field(default_factory=list)
    availability: Optional[Availability] = None
    commitment_bounds: Optional[CommitmentBounds] = None
    commitment: Optional[Commitment] = None
    hold_status: Optional[HoldStatus] = None
    min_spend: Optional[float] = None
    cancellation_policy: Optional[Selectable] = None
    exclusivity: Optional[Selectable] = None
    creative_policy: Optional[dict[str, Any]] = None

    committed_metrics: Optional[list[CommittedMetric]] = None
    measurement_source: Optional[Selectable] = None
    shortfall_remedy: Optional[Selectable] = None
    reporting: Optional[Selectable] = None

    visibility: Optional[str] = None
    required_credentials: Optional[list[RequiredCredential]] = None
    eligible_bidders: Optional[list[str]] = None
    terms_acceptance_required: Optional[bool] = None

    buying_route: Optional[str] = None
    transaction_mechanism: Optional[Selectable] = None
    serving_mode: Optional[str] = None
    deal_id_issuance: Optional[str] = None
    buyer_requirements: Optional[dict[str, Any]] = None
    serving_platform: Optional[str] = None

    external_references: list[ExternalReference] = Field(default_factory=list)
    correlation_id: Optional[str] = None
    billing: Optional[dict[str, Any]] = None
    supported_order_systems: Optional[list[str]] = None

    ext: Optional[dict[str, Any]] = Field(
        default=None, description="Namespaced extensions (proposed in AI-36)."
    )


LINE_ITEM_FIELDS: tuple[str, ...] = tuple(f for f in LineItem.model_fields if f != "ext")


class Proposal(BaseModel):
    """OpenProposal proposal (spec section 4) holding its line items."""

    model_config = ConfigDict(use_enum_values=True)

    proposal_id: str
    version: int = 1
    seller_id: str
    seller_name: str
    type: ProposalType = ProposalType.STANDARD
    status: ProposalStatus = ProposalStatus.PUBLISHED
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    brief_ref: Optional[str] = None
    description: str
    specifications: Optional[str] = None
    best_for: list[str] = Field(default_factory=list)
    not_suitable_for: list[str] = Field(default_factory=list)
    seasonality_notes: Optional[str] = None
    negotiation_history: list[dict[str, Any]] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)


class MappedLineItem(BaseModel):
    """A mapped line item plus where each field came from and what could not map."""

    line_item: LineItem
    provenance: dict[str, Source] = Field(
        description="Source of every OpenProposal line-item field."
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="OpenProposal fields that could not be filled, and why.",
    )
    unmapped_source_fields: list[str] = Field(
        default_factory=list,
        description="Source fields with no OpenProposal home.",
    )


class MappedProposal(BaseModel):
    proposal: Proposal
    line_items: list[MappedLineItem]


class GAMSizeIn(BaseModel):
    width: int
    height: int
    isAspectRatio: bool = False


class GAMAdUnitSizeIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    size: Optional[GAMSizeIn] = None
    environmentType: str = "BROWSER"
    isAudio: bool = False


class GAMAdUnitIn(BaseModel):
    """A GAM ``AdUnit`` as returned by ``InventoryService`` (camelCase, API v202608)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    parentId: Optional[str] = None
    parentPath: list[dict[str, Any]] = Field(default_factory=list)
    description: Optional[str] = None
    adUnitCode: Optional[str] = None
    status: str = "ACTIVE"
    adUnitSizes: list[GAMAdUnitSizeIn] = Field(default_factory=list)
    isNative: bool = False
    isFluid: bool = False
    isInterstitial: bool = False
    applicationId: Optional[int] = None


class GAMMapAdUnitRequest(BaseModel):
    ad_unit: GAMAdUnitIn
    network_code: Optional[str] = None


class GAMLineItemIn(BaseModel):
    """A GAM ``LineItem`` as returned by ``LineItemService`` (camelCase, API v202608)."""

    model_config = ConfigDict(extra="allow")

    id: int | str
    orderId: int | str
    name: str
    externalId: Optional[str] = None
    lineItemType: str
    status: str
    reservationStatus: Optional[str] = None
    costType: str = "CPM"
    costPerUnit: Optional[dict[str, Any]] = None
    budget: Optional[dict[str, Any]] = None
    primaryGoal: Optional[dict[str, Any]] = None
    startDateTime: Optional[dict[str, Any]] = None
    endDateTime: Optional[dict[str, Any]] = None
    unlimitedEndDateTime: bool = False
    environmentType: str = "BROWSER"
    creativePlaceholders: list[dict[str, Any]] = Field(default_factory=list)
    targeting: dict[str, Any] = Field(default_factory=dict)
    frequencyCaps: list[dict[str, Any]] = Field(default_factory=list)
    dealInfo: Optional[dict[str, Any]] = None
    thirdPartyMeasurementSettings: Optional[dict[str, Any]] = None


class GAMOrderIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | str
    name: Optional[str] = None
    currencyCode: Optional[str] = None
    isProgrammatic: bool = False
    poNumber: Optional[str] = None


class GAMMapLineItemRequest(BaseModel):
    line_item: GAMLineItemIn
    order: Optional[GAMOrderIn] = None
    network_code: Optional[str] = None


class OPBuyerIdentity(BaseModel):
    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None


class OPComposeCommitment(BaseModel):
    basis: str = Field(default="units", pattern="^units$", description="Only units is supported.")
    amount: int = Field(gt=0, description="Impressions.")
    flight_start: Optional[str] = Field(default=None, description="ISO date, e.g. 2026-11-01.")
    flight_end: Optional[str] = Field(default=None, description="ISO date, e.g. 2026-11-30.")


class OPQuoteRequest(BaseModel):
    """Compose a line item: pick the mechanism, size the commitment, get a price."""

    idempotency_key: str = Field(min_length=1)
    line_item_id: str = Field(description="A line_item_id from GET /op-products.")
    transaction_mechanism: TransactionMechanism = TransactionMechanism.PREFERRED_DEAL
    commitment: OPComposeCommitment
    requested_rate: Optional[float] = Field(
        default=None, gt=0, description="Optional target CPM in the line item's currency."
    )
    buyer_identity: Optional[OPBuyerIdentity] = None
    agent_url: Optional[str] = None


class OPQuote(BaseModel):
    quote_id: str
    line_item: LineItem


class OPDealRequest(BaseModel):
    """Commit to a composed line item (books the quote)."""

    idempotency_key: str = Field(min_length=1)
    quote_id: str = Field(description="The quote_id returned by POST /api/v1/op-quotes.")
    buyer_identity: Optional[OPBuyerIdentity] = None
    notes: Optional[str] = None


class OPDeal(BaseModel):
    deal_id: str
    line_item: LineItem
