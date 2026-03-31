# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Flow state models for seller workflow orchestration.

These models track the state of seller workflows including product setup,
proposal handling, deal generation, and execution activation.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .core import DealType, PricingModel
from .pricing_type import PricingType


class ExecutionStatus(str, Enum):
    """Status of seller workflow execution."""

    INITIALIZED = "initialized"
    PRODUCT_SETUP = "product_setup"
    AWAITING_PROPOSALS = "awaiting_proposals"
    PROPOSAL_RECEIVED = "proposal_received"
    EVALUATING = "evaluating"
    COUNTER_PENDING = "counter_pending"
    PENDING_APPROVAL = "pending_approval"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEAL_CREATED = "deal_created"
    SYNCING_TO_AD_SERVER = "syncing_to_ad_server"
    COMPLETED = "completed"
    FAILED = "failed"


class ProductDefinition(BaseModel):
    """Definition of a product for the inventory catalog."""

    product_id: str
    name: str
    description: Optional[str] = None
    inventory_type: str  # display, video, ctv, mobile_app, native
    inventory_segment_ids: list[str] = Field(default_factory=list)
    supported_deal_types: list[DealType] = Field(default_factory=list)
    supported_pricing_models: list[PricingModel] = Field(default_factory=list)
    pricing_type: PricingType = PricingType.FIXED
    base_cpm: Optional[float] = None
    floor_cpm: Optional[float] = None
    audience_targeting: Optional[dict[str, Any]] = None
    content_targeting: Optional[dict[str, Any]] = None
    ad_product_targeting: Optional[dict[str, Any]] = None
    minimum_impressions: int = 10000
    maximum_impressions: Optional[int] = None
    currency: str = "USD"

    # UCP audience capabilities (added for audience validation)
    audience_capabilities: list[str] = Field(
        default_factory=list,
        description="List of audience capability IDs available for this product",
    )
    ucp_embedding: Optional[dict[str, Any]] = Field(
        default=None,
        description="Pre-computed UCP embedding for this product's audience",
    )


class ProposalEvaluation(BaseModel):
    """Evaluation result for an incoming proposal."""

    proposal_id: str
    proposal_line_id: str
    product_id: str
    evaluation_timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Validation results
    is_valid: bool = True
    validation_errors: list[str] = Field(default_factory=list)

    # Pricing analysis
    requested_price: float
    minimum_acceptable_price: float
    recommended_price: float
    price_acceptable: bool = False

    # Availability analysis
    requested_impressions: int
    available_impressions: int
    impressions_available: bool = False

    # Targeting analysis
    targeting_compatible: bool = True
    targeting_notes: list[str] = Field(default_factory=list)

    # Audience validation (added for UCP integration)
    audience_validated: bool = Field(
        default=False,
        description="Whether audience targeting has been validated via UCP",
    )
    audience_coverage: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Audience coverage percentage (0-100)",
    )
    audience_gaps: list[str] = Field(
        default_factory=list,
        description="Audience requirements that cannot be fulfilled",
    )
    ucp_similarity_score: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="UCP embedding similarity score (0-1)",
    )

    # Overall recommendation
    recommendation: str  # accept, counter, reject
    counter_terms: Optional[dict[str, Any]] = None
    rejection_reason: Optional[str] = None

    # Yield optimization
    yield_score: float = 0.0  # 0-1 score of deal quality
    upsell_opportunities: list[str] = Field(default_factory=list)


class PricingDecision(BaseModel):
    """Pricing decision for a deal."""

    product_id: str
    deal_type: DealType
    buyer_tier: str  # public, agency, advertiser
    buyer_identity: Optional[dict[str, Any]] = None

    # Pricing output
    base_price: float
    tier_discount: float = 0.0
    volume_discount: float = 0.0
    final_price: float
    currency: str = "USD"
    pricing_model: PricingModel = PricingModel.CPM

    # Context
    rationale: str = ""
    applied_rules: list[str] = Field(default_factory=list)


class ChannelRecommendation(BaseModel):
    """Recommendation from an inventory channel specialist."""

    channel: str  # display, video, ctv, mobile_app, native
    product_ids: list[str] = Field(default_factory=list)
    recommended_pricing: dict[str, float] = Field(default_factory=dict)
    available_inventory: dict[str, int] = Field(default_factory=dict)
    targeting_suggestions: list[str] = Field(default_factory=list)
    yield_analysis: str = ""


class DealOutput(BaseModel):
    """Output of deal creation process."""

    deal_id: str
    deal_type: DealType
    proposal_id: str
    product_id: str

    # Deal terms
    price: float
    pricing_model: PricingModel
    currency: str = "USD"

    # For PG deals
    guaranteed_impressions: Optional[int] = None
    budget: Optional[float] = None

    # For PD/PA deals
    floor_price: Optional[float] = None

    # External references
    ad_server_deal_id: Optional[str] = None
    openrtb_deal_id: Optional[str] = None

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    buyer_organization_id: str
    seller_organization_id: str
    flight_start: str
    flight_end: str

    # Activation info
    activation_type: str  # agentic, traditional_dsp
    dsp_compatible: bool = True


class SellerFlowState(BaseModel):
    """Complete state for seller workflow execution."""

    # Workflow identity
    flow_id: str
    flow_type: str  # product_setup, proposal_handling, deal_generation, execution
    status: ExecutionStatus = ExecutionStatus.INITIALIZED
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Seller identity
    seller_organization_id: str
    seller_name: str

    # Product catalog state
    products: dict[str, ProductDefinition] = Field(default_factory=dict)
    inventory_segments: dict[str, Any] = Field(default_factory=dict)

    # Proposal handling state
    pending_proposals: list[str] = Field(default_factory=list)
    evaluations: dict[str, ProposalEvaluation] = Field(default_factory=dict)
    accepted_proposals: list[str] = Field(default_factory=list)
    rejected_proposals: list[str] = Field(default_factory=list)
    counter_proposals: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Deal state
    deals: dict[str, DealOutput] = Field(default_factory=dict)
    execution_orders: dict[str, Any] = Field(default_factory=dict)
    placements: dict[str, Any] = Field(default_factory=dict)

    # Channel specialist outputs
    channel_recommendations: dict[str, ChannelRecommendation] = Field(default_factory=dict)

    # Pricing decisions
    pricing_decisions: dict[str, PricingDecision] = Field(default_factory=dict)

    # Error tracking
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
