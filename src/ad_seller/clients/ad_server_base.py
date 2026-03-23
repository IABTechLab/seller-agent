# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Abstract ad server client interface.

Defines a common interface that all ad server integrations (GAM, FreeWheel, etc.)
must implement. Enables polymorphic dispatch in flows so the execution layer
doesn't need to know which ad server is in use.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Ad-server-agnostic result models
# =============================================================================


class AdServerType(str, Enum):
    """Supported ad server types."""

    GOOGLE_AD_MANAGER = "google_ad_manager"
    FREEWHEEL = "freewheel"


class OrderStatus(str, Enum):
    """Normalized order status across ad servers."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAUSED = "paused"
    CANCELED = "canceled"
    COMPLETED = "completed"


class LineItemStatus(str, Enum):
    """Normalized line item status."""

    DRAFT = "draft"
    READY = "ready"
    DELIVERING = "delivering"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"


class DealStatus(str, Enum):
    """Normalized programmatic deal status."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AdServerOrder(BaseModel):
    """Ad-server-agnostic order/IO representation."""

    id: str
    name: str
    advertiser_id: str
    advertiser_name: Optional[str] = None
    status: OrderStatus = OrderStatus.DRAFT
    external_id: Optional[str] = None
    notes: Optional[str] = None
    ad_server_type: AdServerType = AdServerType.GOOGLE_AD_MANAGER
    raw: Optional[dict[str, Any]] = Field(default=None, exclude=True)


class AdServerLineItem(BaseModel):
    """Ad-server-agnostic line item/campaign+placement representation."""

    id: str
    order_id: str
    name: str
    status: LineItemStatus = LineItemStatus.DRAFT
    cost_type: str = "CPM"  # CPM, CPC, CPD, CPCV, etc.
    cost_micros: int = 0  # Cost in microcurrency units
    currency: str = "USD"
    impressions_goal: int = -1  # -1 = unlimited
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    external_id: Optional[str] = None
    ad_server_type: AdServerType = AdServerType.GOOGLE_AD_MANAGER
    raw: Optional[dict[str, Any]] = Field(default=None, exclude=True)


class AdServerDeal(BaseModel):
    """Ad-server-agnostic programmatic deal representation."""

    id: str
    deal_id: str  # The external deal ID used in bid requests
    name: Optional[str] = None
    deal_type: str = "private_auction"  # private_auction, preferred_deal, programmatic_guaranteed
    floor_price_micros: int = 0
    fixed_price_micros: int = 0
    currency: str = "USD"
    buyer_seat_ids: list[str] = Field(default_factory=list)
    status: DealStatus = DealStatus.ACTIVE
    external_id: Optional[str] = None
    ad_server_type: AdServerType = AdServerType.GOOGLE_AD_MANAGER
    raw: Optional[dict[str, Any]] = Field(default=None, exclude=True)


class AdServerInventoryItem(BaseModel):
    """Ad-server-agnostic inventory unit (ad unit / ad slot)."""

    id: str
    name: str
    parent_id: Optional[str] = None
    status: str = "ACTIVE"
    sizes: list[tuple[int, int]] = Field(default_factory=list)
    ad_server_type: AdServerType = AdServerType.GOOGLE_AD_MANAGER


class AdServerAudienceSegment(BaseModel):
    """Ad-server-agnostic audience segment."""

    id: str
    name: str
    description: Optional[str] = None
    size: Optional[int] = None
    status: str = "ACTIVE"
    ad_server_type: AdServerType = AdServerType.GOOGLE_AD_MANAGER


class BookingResult(BaseModel):
    """Result of a full deal booking (order + line items + deal)."""

    order: Optional[AdServerOrder] = None
    line_items: list[AdServerLineItem] = Field(default_factory=list)
    deal: Optional[AdServerDeal] = None
    ad_server_type: AdServerType = AdServerType.GOOGLE_AD_MANAGER
    success: bool = True
    error: Optional[str] = None


# =============================================================================
# Abstract ad server client
# =============================================================================


class AdServerClient(ABC):
    """Abstract interface for ad server integrations.

    All ad server clients (GAM, FreeWheel, etc.) must implement this interface.
    The execution flow uses this interface for polymorphic dispatch.
    """

    ad_server_type: AdServerType

    # -- Lifecycle --

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the ad server."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the ad server."""

    async def __aenter__(self) -> "AdServerClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    # -- Order / IO Operations --

    @abstractmethod
    async def create_order(
        self,
        name: str,
        advertiser_id: str,
        *,
        advertiser_name: Optional[str] = None,
        agency_id: Optional[str] = None,
        notes: Optional[str] = None,
        external_id: Optional[str] = None,
    ) -> AdServerOrder:
        """Create an order (GAM) or insertion order (FreeWheel)."""

    @abstractmethod
    async def get_order(self, order_id: str) -> AdServerOrder:
        """Get an order by ID."""

    @abstractmethod
    async def approve_order(self, order_id: str) -> AdServerOrder:
        """Approve/activate an order for delivery."""

    # -- Line Item / Campaign+Placement Operations --

    @abstractmethod
    async def create_line_item(
        self,
        order_id: str,
        name: str,
        *,
        cost_micros: int,
        currency: str = "USD",
        cost_type: str = "CPM",
        impressions_goal: int = -1,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        targeting: Optional[dict[str, Any]] = None,
        creative_sizes: Optional[list[tuple[int, int]]] = None,
        external_id: Optional[str] = None,
    ) -> AdServerLineItem:
        """Create a line item (GAM) or campaign+placement (FreeWheel)."""

    @abstractmethod
    async def update_line_item(
        self,
        line_item_id: str,
        updates: dict[str, Any],
    ) -> AdServerLineItem:
        """Update an existing line item."""

    # -- Programmatic Deal Operations --

    @abstractmethod
    async def create_deal(
        self,
        deal_id: str,
        *,
        name: Optional[str] = None,
        deal_type: str = "private_auction",
        floor_price_micros: int = 0,
        fixed_price_micros: int = 0,
        currency: str = "USD",
        buyer_seat_ids: Optional[list[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        targeting: Optional[dict[str, Any]] = None,
    ) -> AdServerDeal:
        """Create a programmatic deal (PG/PD/PA)."""

    @abstractmethod
    async def update_deal(
        self,
        deal_id: str,
        updates: dict[str, Any],
    ) -> AdServerDeal:
        """Update an existing deal."""

    # -- Inventory Operations --

    @abstractmethod
    async def list_inventory(
        self,
        *,
        limit: int = 100,
        filter_str: Optional[str] = None,
    ) -> list[AdServerInventoryItem]:
        """List ad units (GAM) or ad slots (FreeWheel)."""

    # -- Audience Operations --

    @abstractmethod
    async def list_audience_segments(
        self,
        *,
        limit: int = 500,
        filter_str: Optional[str] = None,
    ) -> list[AdServerAudienceSegment]:
        """List audience segments available for targeting."""

    # -- High-Level Booking --

    @abstractmethod
    async def book_deal(
        self,
        deal_id: str,
        advertiser_name: str,
        *,
        deal_type: str = "private_auction",
        floor_price_micros: int = 0,
        fixed_price_micros: int = 0,
        currency: str = "USD",
        impressions_goal: int = -1,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        targeting: Optional[dict[str, Any]] = None,
        creative_sizes: Optional[list[tuple[int, int]]] = None,
    ) -> BookingResult:
        """Full deal booking: create order + line items + deal in one call."""


# =============================================================================
# Factory
# =============================================================================


def get_ad_server_client(ad_server_type: Optional[str] = None) -> AdServerClient:
    """Factory to get the appropriate ad server client based on config.

    Args:
        ad_server_type: Override ad server type. If None, reads from settings.

    Returns:
        An AdServerClient instance (not yet connected — call connect() or use as async context manager).
    """
    if ad_server_type is None:
        from ..config import get_settings
        ad_server_type = get_settings().ad_server_type

    if ad_server_type == "google_ad_manager":
        from .gam_adapter import GAMAdServerClient
        return GAMAdServerClient()
    elif ad_server_type == "freewheel":
        from .freewheel_adapter import FreeWheelAdServerClient
        return FreeWheelAdServerClient()
    else:
        raise ValueError(f"Unsupported ad server type: {ad_server_type}")
