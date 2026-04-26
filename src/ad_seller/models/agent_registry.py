# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Agent Registry models for AAMP integration and agent discovery.

Provides models for:
- Agent Cards (A2A-protocol-compliant agent identity)
- Trust status with access tier mapping
- Local agent registry entries

Trust status determines the maximum access tier an agent can claim,
which flows through the existing PricingTier/MediaKit/Negotiation systems:

    UNKNOWN    → PUBLIC      (price ranges only, no placements)
    REGISTERED → SEAT        (exact prices, no negotiation)
    APPROVED   → ADVERTISER  (full negotiation, volume discounts)
    PREFERRED  → ADVERTISER  (best rates, premium inventory, custom rules)
    BLOCKED    → None        (hard reject, zero data access)
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .audience_capabilities import CapabilityAudienceBlock
from .buyer_identity import AccessTier

# =============================================================================
# Agent Card Components (A2A Protocol)
# =============================================================================


class AgentSkill(BaseModel):
    """A declared capability of an agent."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)


class AgentProvider(BaseModel):
    """Organization operating the agent."""

    name: str
    url: Optional[str] = None
    description: Optional[str] = None


class AgentAuthentication(BaseModel):
    """Authentication requirements for interacting with this agent."""

    schemes: list[str] = Field(default_factory=lambda: ["api_key"])
    credentials_url: Optional[str] = None


class AgentCapabilities(BaseModel):
    """Protocol and feature capabilities."""

    protocols: list[str] = Field(default_factory=lambda: ["a2a"])
    streaming: bool = False
    push_notifications: bool = False


class AgentCard(BaseModel):
    """A2A-protocol-compliant agent card.

    Served at /.well-known/agent.json for agent discovery.
    Contains identity, capabilities, skills, and connection info.
    """

    name: str
    description: str
    url: str  # A2A service endpoint
    version: str = "1.0.0"
    provider: AgentProvider
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    skills: list[AgentSkill] = Field(default_factory=list)
    authentication: AgentAuthentication = Field(default_factory=AgentAuthentication)
    inventory_types: list[str] = Field(default_factory=list)
    supported_deal_types: list[str] = Field(default_factory=list)
    contact: Optional[str] = None
    tos_url: Optional[str] = None
    # Per proposal §5.7 layer 1: audience capability advertisement. Optional
    # for backward compatibility -- a missing block means "treat as legacy"
    # (standard segments only, no constraints/extensions/exclusions, no
    # agentic). Buyers pre-flight an AudiencePlan against these flags before
    # sending a DealBookingRequest.
    audience_capabilities: Optional[CapabilityAudienceBlock] = Field(
        default=None,
        description=(
            "Audience capability discovery block (proposal §5.7 layer 1). "
            "Schema version, supports_* flags, max_refs_per_role, and "
            "taxonomy_lock_hashes for cross-repo schema-drift detection."
        ),
    )


# =============================================================================
# Trust Status & Access Tier Mapping
# =============================================================================


class TrustStatus(str, Enum):
    """Agent trust status in the local registry.

    Determines the maximum AccessTier an agent can claim,
    capping whatever identity-based tier they self-declare.
    """

    UNKNOWN = "unknown"  # Never seen, not in AAMP registry
    REGISTERED = "registered"  # Found in IAB AAMP registry
    APPROVED = "approved"  # Manually approved by seller operator
    PREFERRED = "preferred"  # Strategic partner with premium access
    BLOCKED = "blocked"  # Explicitly blocked — zero data access


TRUST_TO_TIER_MAP: dict[TrustStatus, Optional[AccessTier]] = {
    TrustStatus.UNKNOWN: AccessTier.PUBLIC,
    TrustStatus.REGISTERED: AccessTier.SEAT,
    TrustStatus.APPROVED: AccessTier.ADVERTISER,
    TrustStatus.PREFERRED: AccessTier.ADVERTISER,
    TrustStatus.BLOCKED: None,  # No access — hard reject
}
"""Maps trust status to the maximum AccessTier an agent can achieve.

None means the agent is blocked and should be rejected entirely.
The agent's effective tier = min(trust_ceiling, claimed_identity_tier).
"""


# =============================================================================
# Registered Agent (Local Registry Entry)
# =============================================================================


class AgentType(str, Enum):
    """Type of agent in the registry.

    Extensible beyond buyer/seller to support tool providers,
    measurement vendors, verification services, and other
    ecosystem participants.
    """

    BUYER = "buyer"
    SELLER = "seller"
    TOOL_PROVIDER = "tool_provider"  # MCP tool servers, measurement, verification
    DATA_PROVIDER = "data_provider"  # Audience data, contextual, identity
    OTHER = "other"


class RegistrySource(BaseModel):
    """Tracks which registry an agent was discovered through.

    The IAB Tech Lab AAMP registry is the primary source, but the
    architecture supports additional registries (vendor-specific,
    private enterprise registries, etc.).
    """

    registry_id: str  # e.g. "iab_aamp", "custom_enterprise", "vendor_xyz"
    registry_name: str  # Human-readable: "IAB Tech Lab AAMP"
    registry_url: str  # Base URL of the registry
    external_agent_id: Optional[str] = None  # ID assigned by this registry
    verified_at: Optional[datetime] = None


class RegisteredAgent(BaseModel):
    """A known agent in the local registry.

    Persisted via StorageBackend at key agent:{agent_id}.
    Links trust status to access tier ceiling for the existing
    pricing/media-kit/negotiation infrastructure.

    Supports discovery from multiple registries — an agent may be
    registered in IAB AAMP and also in a vendor-specific registry.
    """

    agent_id: str
    agent_card: AgentCard
    agent_type: AgentType = AgentType.BUYER
    trust_status: TrustStatus = TrustStatus.UNKNOWN
    registry_sources: list[RegistrySource] = Field(default_factory=list)
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen: Optional[datetime] = None
    interaction_count: int = 0
    notes: Optional[str] = None

    @property
    def effective_access_ceiling(self) -> Optional[AccessTier]:
        """Maximum AccessTier this agent can claim.

        Returns None if agent is blocked (caller should reject).
        """
        return TRUST_TO_TIER_MAP.get(self.trust_status)

    @property
    def is_blocked(self) -> bool:
        """Whether this agent is explicitly blocked."""
        return self.trust_status == TrustStatus.BLOCKED
