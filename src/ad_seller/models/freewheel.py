# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""FreeWheel-specific data models.

Models for FreeWheel Streaming Hub and Buyer Cloud entities
before normalization to the ad-server-agnostic types.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class FWDealStatus(str, Enum):
    """FreeWheel-native deal statuses."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class FWSellerType(str, Enum):
    """FreeWheel seller types."""

    PUBLISHER = "PUBLISHER"
    INTERMEDIARY = "INTERMEDIARY"
    BOTH = "BOTH"


class FWInventoryPackage(BaseModel):
    """Raw FreeWheel inventory package (template deal) before normalization.

    Philippe's list_inventory() returns these as "template deals"
    representing packages on Streaming Hub.
    """

    id: str
    name: str
    description: Optional[str] = None
    inventory_type: Optional[str] = None  # display, video, ctv, etc.
    ad_formats: list[str] = []
    device_types: list[int] = []
    content_categories: list[str] = []
    geo_targets: list[str] = []
    status: str = "ACTIVE"
    floor_price: Optional[float] = None
    currency: str = "USD"
    raw: Optional[dict[str, Any]] = None


class FWAudienceSegment(BaseModel):
    """Raw FreeWheel audience segment before normalization."""

    id: str
    name: str
    description: Optional[str] = None
    size: Optional[int] = None
    segment_type: Optional[str] = None  # 1P, 3P, ACR
    status: str = "ACTIVE"
    raw: Optional[dict[str, Any]] = None


class FWDeal(BaseModel):
    """Raw FreeWheel deal response before normalization."""

    id: str
    deal_id: str  # External deal ID (OpenRTB)
    name: Optional[str] = None
    deal_type: Optional[str] = None  # PG, PD, PA
    floor_price: Optional[float] = None
    fixed_price: Optional[float] = None
    currency: str = "USD"
    buyer_seat_ids: list[str] = []
    status: str = "DRAFT"
    sh_deal_id: Optional[str] = None  # Streaming Hub internal ID
    bc_deal_id: Optional[str] = None  # Buyer Cloud internal ID
    raw: Optional[dict[str, Any]] = None


class FWCrossMCPBinding(BaseModel):
    """Cross-MCP binding record tracking entities across SH and BC.

    The external deal_id is the shared key that binds entities
    across both FreeWheel systems.
    """

    deal_id: str  # External deal ID (OpenRTB)
    sh_deal_id: Optional[str] = None
    bc_campaign_id: Optional[str] = None
    bc_line_item_ids: list[str] = []
    bc_creative_ids: list[str] = []
    binding_status: str = "pending"  # pending, complete, partial, failed
    created_at: Optional[str] = None
    error: Optional[str] = None
