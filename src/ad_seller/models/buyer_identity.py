# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer identity models for tiered access and pricing.

The seller agent supports tiered access based on buyer identity:
- Public/Unauthenticated: General catalog, price ranges
- Agency/Seat Level: Agency-specific pricing, premium inventory
- Advertiser Level: Best rates, cross-agency consistency
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AccessTier(str, Enum):
    """Access tier based on buyer authentication level."""

    PUBLIC = "public"  # Unauthenticated, general catalog
    SEAT = "seat"  # Authenticated DSP seat
    AGENCY = "agency"  # Agency-level identity revealed
    ADVERTISER = "advertiser"  # Advertiser identity revealed


class IdentityLevel(str, Enum):
    """Level of identity revealed by buyer."""

    ANONYMOUS = "anonymous"
    SEAT_ONLY = "seat_only"
    AGENCY_ONLY = "agency_only"
    AGENCY_AND_ADVERTISER = "agency_and_advertiser"


class BuyerIdentity(BaseModel):
    """Buyer identity information for tiered access.

    Identity can be revealed progressively:
    1. Authenticate DSP/Agent seat → unlock seat-level access
    2. Provide agency ID → unlock agency-specific pricing
    3. Provide advertiser ID → unlock advertiser-specific pricing
    """

    # DSP/Seat level
    seat_id: Optional[str] = None
    seat_name: Optional[str] = None
    dsp_platform: Optional[str] = None  # ttd, amazon, dv360, etc.
    # Further DSP seat IDs allowed to bid on this buyer's deals (OpenRTB wseat)
    seat_ids: list[str] = Field(default_factory=list)

    # Agency level
    agency_id: Optional[str] = None
    agency_name: Optional[str] = None
    agency_holding_company: Optional[str] = None  # WPP, Omnicom, Publicis, etc.

    # Advertiser level
    advertiser_id: Optional[str] = None
    advertiser_name: Optional[str] = None
    advertiser_industry: Optional[str] = None
    # Advertiser landing domains for this buyer's deals (OpenRTB wadomain)
    advertiser_domains: list[str] = Field(default_factory=list)

    # Campaign level (optional, for campaign-specific deals)
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None

    @property
    def identity_level(self) -> IdentityLevel:
        """Determine the current identity level."""
        if self.advertiser_id and self.agency_id:
            return IdentityLevel.AGENCY_AND_ADVERTISER
        if self.agency_id:
            return IdentityLevel.AGENCY_ONLY
        if self.seat_id:
            return IdentityLevel.SEAT_ONLY
        return IdentityLevel.ANONYMOUS

    @property
    def access_tier(self) -> AccessTier:
        """Determine the access tier based on identity level."""
        level = self.identity_level
        if level == IdentityLevel.AGENCY_AND_ADVERTISER:
            return AccessTier.ADVERTISER
        if level == IdentityLevel.AGENCY_ONLY:
            return AccessTier.AGENCY
        if level == IdentityLevel.SEAT_ONLY:
            return AccessTier.SEAT
        return AccessTier.PUBLIC


class BuyerRelationship(BaseModel):
    """Historical relationship data for a buyer."""

    buyer_id: str  # Can be seat_id, agency_id, or advertiser_id
    buyer_type: str  # seat, agency, advertiser

    # Spend history
    total_historical_spend: float = 0.0
    ytd_spend: float = 0.0
    last_12_month_spend: float = 0.0

    # Deal history
    total_deals: int = 0
    active_deals: int = 0
    completed_deals: int = 0

    # Relationship tier
    relationship_tier: str = "standard"  # standard, preferred, strategic

    # Performance metrics
    average_fill_rate: float = 0.0
    average_cpm_paid: float = 0.0
    payment_history: str = "unknown"  # excellent, good, fair, poor

    # Preferences
    preferred_inventory_types: list[str] = Field(default_factory=list)
    blocked_content_categories: list[str] = Field(default_factory=list)


class BuyerContext(BaseModel):
    """Complete buyer context for pricing and access decisions.

    Combines identity, relationship history, and request context
    for comprehensive access and pricing evaluation.
    """

    identity: BuyerIdentity
    relationship: Optional[BuyerRelationship] = None

    # Request context
    request_timestamp: Optional[str] = None
    request_type: str = "discovery"  # discovery, proposal, deal

    # Authentication context
    is_authenticated: bool = False
    authentication_method: Optional[str] = None  # oauth, api_key, a2a

    # Agent registry context (set by registry resolution)
    agent_url: Optional[str] = None
    agent_trust_status: Optional[str] = None  # TrustStatus value
    max_access_tier: Optional[AccessTier] = None  # Ceiling from trust status

    # Ordered tier list for ceiling comparison
    _TIER_ORDER: list[AccessTier] = [
        AccessTier.PUBLIC,
        AccessTier.SEAT,
        AccessTier.AGENCY,
        AccessTier.ADVERTISER,
    ]

    # Derived properties
    @property
    def effective_tier(self) -> AccessTier:
        """Get effective access tier considering authentication and trust ceiling.

        If max_access_tier is set (from agent registry), the claimed tier
        is capped at that ceiling. This ensures e.g. an AAMP-registered agent
        (SEAT ceiling) claiming ADVERTISER identity only gets SEAT access.
        """
        if not self.is_authenticated:
            return AccessTier.PUBLIC
        claimed = self.identity.access_tier
        if self.max_access_tier is not None:
            claimed_idx = self._TIER_ORDER.index(claimed)
            ceiling_idx = self._TIER_ORDER.index(self.max_access_tier)
            return self._TIER_ORDER[min(claimed_idx, ceiling_idx)]
        return claimed

    @property
    def eligible_for_negotiation(self) -> bool:
        """Check if buyer is eligible for price negotiation."""
        return self.effective_tier in [AccessTier.AGENCY, AccessTier.ADVERTISER]

    @property
    def eligible_for_premium_inventory(self) -> bool:
        """Check if buyer has access to premium inventory."""
        return self.is_authenticated and self.effective_tier != AccessTier.PUBLIC

    def get_pricing_key(self) -> str:
        """Get the key for pricing lookup.

        Returns the most specific identifier available for consistent
        cross-agency pricing.
        """
        if self.identity.advertiser_id:
            return f"advertiser:{self.identity.advertiser_id}"
        if self.identity.agency_id:
            return f"agency:{self.identity.agency_id}"
        if self.identity.seat_id:
            return f"seat:{self.identity.seat_id}"
        return "public"
