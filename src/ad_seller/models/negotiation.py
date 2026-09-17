# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Negotiation models for multi-round buyer-seller negotiation.

Supports strategy-per-buyer-tier, concession tracking, walk-away logic,
and convergence detection. All state is externalized in NegotiationHistory
so the engine remains stateless.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .buyer_identity import AccessTier


class NegotiationAction(str, Enum):
    """Actions the seller can take in a negotiation round."""

    ACCEPT = "accept"
    COUNTER = "counter"
    REJECT = "reject"  # Walk-away
    FINAL_OFFER = "final_offer"  # Last round before walk-away


class NegotiationStrategy(str, Enum):
    """Negotiation strategy, mapped from buyer AccessTier."""

    AGGRESSIVE = "aggressive"  # PUBLIC tier
    STANDARD = "standard"  # SEAT tier
    COLLABORATIVE = "collaborative"  # AGENCY tier
    PREMIUM = "premium"  # ADVERTISER tier


# Deterministic mapping from AccessTier to NegotiationStrategy
TIER_STRATEGY_MAP: dict[AccessTier, NegotiationStrategy] = {
    AccessTier.PUBLIC: NegotiationStrategy.AGGRESSIVE,
    AccessTier.SEAT: NegotiationStrategy.STANDARD,
    AccessTier.AGENCY: NegotiationStrategy.COLLABORATIVE,
    AccessTier.ADVERTISER: NegotiationStrategy.PREMIUM,
}


class NegotiationLimits(BaseModel):
    """Concession limits for a negotiation strategy."""

    max_rounds: int
    per_round_concession_cap: float  # Max % concession per round (0-1)
    total_concession_cap: float  # Max cumulative % concession (0-1)
    gap_split_buyer_share: float  # Buyer's share of gap (0-1)


# Default limits per strategy
STRATEGY_LIMITS: dict[NegotiationStrategy, NegotiationLimits] = {
    NegotiationStrategy.AGGRESSIVE: NegotiationLimits(
        max_rounds=3,
        per_round_concession_cap=0.03,
        total_concession_cap=0.08,
        gap_split_buyer_share=0.30,
    ),
    NegotiationStrategy.STANDARD: NegotiationLimits(
        max_rounds=4,
        per_round_concession_cap=0.04,
        total_concession_cap=0.12,
        gap_split_buyer_share=0.40,
    ),
    NegotiationStrategy.COLLABORATIVE: NegotiationLimits(
        max_rounds=5,
        per_round_concession_cap=0.05,
        total_concession_cap=0.15,
        gap_split_buyer_share=0.50,
    ),
    NegotiationStrategy.PREMIUM: NegotiationLimits(
        max_rounds=6,
        per_round_concession_cap=0.06,
        total_concession_cap=0.20,
        gap_split_buyer_share=0.65,
    ),
}


class NegotiationRound(BaseModel):
    """A single round in a negotiation."""

    round_number: int
    buyer_price: float  # What buyer offered
    seller_price: float  # What seller countered (or accepted at)
    action: NegotiationAction
    concession_pct: float = 0.0  # How much seller conceded this round (0-1)
    cumulative_concession_pct: float = 0.0  # Total concession so far (0-1)
    rationale: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class NegotiationHistory(BaseModel):
    """Full negotiation state, passed in to the stateless engine."""

    negotiation_id: str = Field(default_factory=lambda: f"neg-{uuid.uuid4().hex[:8]}")
    proposal_id: str
    # The quote this negotiation concerns, when one was named. It arrives on
    # the wire (``NegotiationMessage.quote_id``) and used to be dropped here,
    # so nothing could correlate a negotiation to the quote a buyer books —
    # booking then struck the stale quoted price and logged success.
    quote_id: Optional[str] = None
    product_id: str
    buyer_tier: AccessTier
    strategy: NegotiationStrategy
    limits: NegotiationLimits
    base_price: float  # Seller's starting price (tier-adjusted)
    floor_price: float  # Absolute floor (product floor)
    rounds: list[NegotiationRound] = Field(default_factory=list)
    status: str = "active"  # active, accepted, rejected, expired
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    package_id: Optional[str] = None  # If negotiating on a package
