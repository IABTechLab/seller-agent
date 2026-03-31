# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quote and deal booking models for the IAB Deals API v1.0.

Implements the two-phase quote-then-book flow:
- Phase 1: Buyer requests non-binding pricing quotes from sellers
- Phase 2: Buyer books a deal by referencing an accepted quote

See: docs/api/deal-creation-api-contract.md (bead: ar-mkj)
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .pricing_type import PricingType


class QuoteStatus(str, Enum):
    """Status of a price quote."""

    AVAILABLE = "available"
    BOOKED = "booked"
    EXPIRED = "expired"
    DECLINED = "declined"


class DealBookingStatus(str, Enum):
    """Status of a booked deal."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# =============================================================================
# Request Models
# =============================================================================


class QuoteBuyerIdentity(BaseModel):
    """Buyer identity included in a quote request."""

    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    dsp_platform: Optional[str] = None


class QuoteRequest(BaseModel):
    """Request for a non-binding price quote."""

    product_id: str
    deal_type: str  # "PG", "PD", or "PA"
    impressions: Optional[int] = None
    flight_start: Optional[str] = None  # YYYY-MM-DD
    flight_end: Optional[str] = None  # YYYY-MM-DD
    target_cpm: Optional[float] = None
    buyer_identity: Optional[QuoteBuyerIdentity] = None


class DealBookingRequest(BaseModel):
    """Request to book a deal from a quote."""

    quote_id: str
    buyer_identity: Optional[QuoteBuyerIdentity] = None
    notes: Optional[str] = None


# =============================================================================
# Response Models
# =============================================================================


class QuoteProductInfo(BaseModel):
    """Product summary in a quote response."""

    product_id: str
    name: str
    inventory_type: str


class QuotePricing(BaseModel):
    """Pricing breakdown in a quote response."""

    pricing_type: PricingType = PricingType.FIXED
    base_cpm: Optional[float] = None
    tier_discount_pct: float = 0.0
    volume_discount_pct: float = 0.0
    final_cpm: float
    currency: str = "USD"
    pricing_model: str = "cpm"
    rationale: str = ""


class QuoteTerms(BaseModel):
    """Deal terms in a quote response."""

    impressions: Optional[int] = None
    flight_start: str
    flight_end: str
    guaranteed: bool = False


class QuoteAvailability(BaseModel):
    """Availability info in a quote response."""

    inventory_available: bool = True
    estimated_fill_rate: float = 0.95
    competing_demand: str = "moderate"


class QuoteResponse(BaseModel):
    """Non-binding price quote from the seller."""

    quote_id: str
    status: QuoteStatus = QuoteStatus.AVAILABLE
    product: QuoteProductInfo
    pricing: QuotePricing
    terms: QuoteTerms
    availability: QuoteAvailability = Field(default_factory=QuoteAvailability)
    deal_type: str
    buyer_tier: str
    expires_at: str  # ISO 8601
    seller_id: str = "seller-premium-pub-001"
    created_at: str  # ISO 8601
    deal_id: Optional[str] = None  # Set when quote is booked


class DealBookingResponse(BaseModel):
    """Confirmed deal booked from a quote."""

    deal_id: str
    deal_type: str
    status: DealBookingStatus = DealBookingStatus.PROPOSED
    quote_id: str
    product: QuoteProductInfo
    pricing: QuotePricing
    terms: QuoteTerms
    buyer_tier: str
    expires_at: str  # ISO 8601 — acceptance window
    activation_instructions: dict[str, str] = Field(default_factory=dict)
    openrtb_params: dict[str, Any] = Field(default_factory=dict)
    created_at: str  # ISO 8601
