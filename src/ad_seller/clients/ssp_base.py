# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Abstract SSP client interface.

Defines a common interface for SSP (Supply-Side Platform) integrations.
SSPs are intermediaries between publishers and DSPs — they manage deal
distribution, troubleshooting, and reporting across the exchange.

This is separate from AdServerClient (FreeWheel/GAM) which manages the
publisher's ad server. A publisher may use both:
  - AdServerClient: inventory sync, deal setup in their ad server
  - SSPClient: deal distribution through SSP exchanges to DSPs

Implementations:
  - MCPSSPClient: SSPs with MCP servers (PubMatic, etc.)
  - RESTSSPClient: SSPs with REST APIs (Magnite, Index Exchange, etc.)

Publishers can configure multiple SSPs simultaneously with routing rules.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# SSP result models
# =============================================================================


class SSPType(str, Enum):
    """Known SSP platforms (extensible via config)."""

    PUBMATIC = "pubmatic"
    MAGNITE = "magnite"
    INDEX_EXCHANGE = "index_exchange"
    OPENX = "openx"
    XANDR = "xandr"
    CUSTOM = "custom"


class SSPDealType(str, Enum):
    """Deal types supported across SSPs."""

    PMP = "pmp"  # Private Marketplace
    PG = "pg"  # Programmatic Guaranteed
    PREFERRED = "preferred"  # Preferred Deal
    AUCTION_PACKAGE = "auction_package"  # Curated auction package


class SSPDealStatus(str, Enum):
    """Normalized deal status across SSPs."""

    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    ERROR = "error"


class SSPDeal(BaseModel):
    """Normalized deal representation from an SSP."""

    deal_id: str
    name: Optional[str] = None
    deal_type: SSPDealType = SSPDealType.PMP
    status: SSPDealStatus = SSPDealStatus.CREATED
    advertiser: Optional[str] = None
    cpm: Optional[float] = None
    currency: str = "USD"
    priority: Optional[str] = None  # SSP-specific (e.g., PubMatic P11-P15)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    targeting: Optional[dict[str, Any]] = None
    impressions_goal: Optional[int] = None
    ssp_type: SSPType = SSPType.CUSTOM
    ssp_name: Optional[str] = None  # Human-readable SSP name
    raw: Optional[dict[str, Any]] = None  # Original SSP response


class SSPTroubleshootResult(BaseModel):
    """Troubleshooting result from an SSP."""

    deal_id: str
    health_score: Optional[int] = None  # 0-100
    status: str = "unknown"
    primary_issues: list[str] = []
    root_causes: list[dict[str, str]] = []
    recommendations: list[dict[str, str]] = []
    ssp_type: SSPType = SSPType.CUSTOM
    raw: Optional[dict[str, Any]] = None


class SSPDealCreateRequest(BaseModel):
    """Normalized request to create a deal on an SSP."""

    deal_type: SSPDealType = SSPDealType.PMP
    name: Optional[str] = None
    advertiser: Optional[str] = None
    cpm: Optional[float] = None
    currency: str = "USD"
    priority: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    targeting: Optional[dict[str, Any]] = None
    impressions_goal: Optional[int] = None
    buyer_seat_ids: list[str] = []
    # For clone operations
    source_deal_id: Optional[str] = None


# =============================================================================
# Abstract SSP Client
# =============================================================================


class SSPClient(ABC):
    """Abstract base class for SSP integrations.

    Each SSP implementation (MCP or REST) must provide these methods.
    The SSP registry manages multiple SSP clients and routing.
    """

    ssp_type: SSPType = SSPType.CUSTOM
    ssp_name: str = "Unknown SSP"

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the SSP."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the SSP."""
        ...

    async def __aenter__(self) -> "SSPClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    # --- Deal Operations ---

    @abstractmethod
    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Create a new deal on the SSP.

        The SSP handles distributing the deal to the appropriate DSP(s).
        """
        ...

    @abstractmethod
    async def clone_deal(
        self,
        source_deal_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SSPDeal:
        """Clone an existing deal with optional modifications."""
        ...

    @abstractmethod
    async def get_deal(self, deal_id: str) -> SSPDeal:
        """Get deal details by ID."""
        ...

    @abstractmethod
    async def list_deals(
        self,
        *,
        status: Optional[SSPDealStatus] = None,
        limit: int = 100,
    ) -> list[SSPDeal]:
        """List deals on this SSP."""
        ...

    @abstractmethod
    async def update_deal(
        self,
        deal_id: str,
        updates: dict[str, Any],
    ) -> SSPDeal:
        """Update deal attributes."""
        ...

    # --- Troubleshooting ---

    @abstractmethod
    async def troubleshoot_deal(self, deal_id: str) -> SSPTroubleshootResult:
        """Diagnose and get recommendations for an underperforming deal."""
        ...

    # --- Health Check ---

    async def health_check(self) -> bool:
        """Check if the SSP connection is healthy. Override for custom logic."""
        return True


# =============================================================================
# SSP Registry — manages multiple SSP clients with routing
# =============================================================================


class SSPRegistry:
    """Registry for multiple SSP clients with routing rules.

    Publishers configure which SSPs to use and optional routing rules
    to direct deals to specific SSPs based on inventory type, deal type, etc.

    Usage:
        registry = SSPRegistry()
        registry.register("pubmatic", pubmatic_client)
        registry.register("magnite", magnite_client)
        registry.set_routing_rules({"ctv": "pubmatic", "display": "magnite"})

        # Route a deal
        ssp = registry.get_client_for("ctv")  # returns pubmatic client
        ssp = registry.get_client("pubmatic")  # direct access
    """

    def __init__(self) -> None:
        self._clients: dict[str, SSPClient] = {}
        self._routing_rules: dict[str, str] = {}  # inventory_type → ssp_name
        self._default_ssp: Optional[str] = None

    def register(self, name: str, client: SSPClient) -> None:
        """Register an SSP client by name."""
        self._clients[name] = client
        if self._default_ssp is None:
            self._default_ssp = name

    def unregister(self, name: str) -> None:
        """Remove an SSP client."""
        self._clients.pop(name, None)
        if self._default_ssp == name:
            self._default_ssp = next(iter(self._clients), None)

    def get_client(self, name: str) -> SSPClient:
        """Get an SSP client by name."""
        if name not in self._clients:
            raise KeyError(f"SSP '{name}' not registered. Available: {list(self._clients.keys())}")
        return self._clients[name]

    def get_client_for(
        self,
        inventory_type: Optional[str] = None,
        deal_type: Optional[str] = None,
    ) -> SSPClient:
        """Get the appropriate SSP client based on routing rules.

        Falls back to default SSP if no rule matches.
        """
        # Check inventory type routing
        if inventory_type and inventory_type in self._routing_rules:
            ssp_name = self._routing_rules[inventory_type]
            if ssp_name in self._clients:
                return self._clients[ssp_name]

        # Check deal type routing
        if deal_type and deal_type in self._routing_rules:
            ssp_name = self._routing_rules[deal_type]
            if ssp_name in self._clients:
                return self._clients[ssp_name]

        # Fall back to default
        if self._default_ssp and self._default_ssp in self._clients:
            return self._clients[self._default_ssp]

        raise RuntimeError("No SSP clients registered")

    def set_routing_rules(self, rules: dict[str, str]) -> None:
        """Set routing rules (key → ssp_name mapping).

        Keys can be inventory types (ctv, display, video) or
        deal types (pmp, pg, preferred).
        """
        self._routing_rules = rules

    def set_default(self, name: str) -> None:
        """Set the default SSP for unrouted deals."""
        if name not in self._clients:
            raise KeyError(f"SSP '{name}' not registered")
        self._default_ssp = name

    def list_ssps(self) -> list[str]:
        """List registered SSP names."""
        return list(self._clients.keys())

    @property
    def default_ssp(self) -> Optional[str]:
        return self._default_ssp

    async def connect_all(self) -> None:
        """Connect all registered SSP clients."""
        for name, client in self._clients.items():
            await client.connect()

    async def disconnect_all(self) -> None:
        """Disconnect all registered SSP clients."""
        for name, client in self._clients.items():
            try:
                await client.disconnect()
            except Exception:
                pass
