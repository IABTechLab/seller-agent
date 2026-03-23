# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Normalization layer for FreeWheel MCP responses.

Converts FreeWheel-native field names, status values, and pricing formats
to the ad-server-agnostic types defined in ad_server_base.py.

Philippe's responses are not yet OpenDirect compliant but data maps to
IAB taxonomy. This module handles the translation.
"""

from typing import Any

from .ad_server_base import (
    AdServerAudienceSegment,
    AdServerDeal,
    AdServerInventoryItem,
    AdServerType,
    BookingResult,
    DealStatus,
)


# =============================================================================
# Price conversion
# =============================================================================


def dollars_to_micros(dollars: float) -> int:
    """Convert decimal dollars to microcurrency (1 USD = 1,000,000 micro-USD)."""
    return int(dollars * 1_000_000)


def micros_to_dollars(micros: int) -> float:
    """Convert microcurrency to decimal dollars."""
    return micros / 1_000_000


# =============================================================================
# Status mappings (FreeWheel → normalized)
# =============================================================================

_DEAL_STATUS_MAP: dict[str, DealStatus] = {
    "DRAFT": DealStatus.DRAFT,
    "ACTIVE": DealStatus.ACTIVE,
    "PAUSED": DealStatus.PAUSED,
    "ARCHIVED": DealStatus.ARCHIVED,
    # Lowercase variants
    "draft": DealStatus.DRAFT,
    "active": DealStatus.ACTIVE,
    "paused": DealStatus.PAUSED,
    "archived": DealStatus.ARCHIVED,
}

_DEAL_TYPE_MAP: dict[str, str] = {
    "PG": "programmaticguaranteed",
    "PD": "preferreddeal",
    "PA": "privateauction",
    "programmatic_guaranteed": "programmaticguaranteed",
    "preferred_deal": "preferreddeal",
    "private_auction": "privateauction",
}


# =============================================================================
# Inventory normalization
# =============================================================================


def normalize_inventory(raw_items: list[dict[str, Any]]) -> list[AdServerInventoryItem]:
    """Normalize FreeWheel inventory packages to AdServerInventoryItem.

    Philippe's list_inventory() returns "template deals" from SH
    representing packages. Each becomes an inventory item.
    """
    items = []
    for raw in raw_items:
        item = AdServerInventoryItem(
            id=str(raw.get("id", "")),
            name=raw.get("name", "Unknown"),
            parent_id=raw.get("parent_id") or raw.get("network_id"),
            status=raw.get("status", "ACTIVE"),
            sizes=_parse_sizes(raw.get("sizes") or raw.get("ad_formats", [])),
            ad_server_type=AdServerType.FREEWHEEL,
        )
        items.append(item)
    return items


def _parse_sizes(raw_sizes: list) -> list[tuple[int, int]]:
    """Parse size data from FreeWheel responses.

    Handles both (width, height) tuples and string formats like "300x250".
    """
    sizes = []
    for s in raw_sizes:
        if isinstance(s, (list, tuple)) and len(s) == 2:
            sizes.append((int(s[0]), int(s[1])))
        elif isinstance(s, str) and "x" in s:
            parts = s.split("x")
            if len(parts) == 2:
                try:
                    sizes.append((int(parts[0]), int(parts[1])))
                except ValueError:
                    pass
    return sizes


# =============================================================================
# Audience segment normalization
# =============================================================================


def normalize_audience_segments(raw_segments: list[dict[str, Any]]) -> list[AdServerAudienceSegment]:
    """Normalize FreeWheel audience segments."""
    segments = []
    for raw in raw_segments:
        seg = AdServerAudienceSegment(
            id=str(raw.get("id", "")),
            name=raw.get("name", "Unknown"),
            description=raw.get("description"),
            size=raw.get("size"),
            status=raw.get("status", "ACTIVE"),
            ad_server_type=AdServerType.FREEWHEEL,
        )
        segments.append(seg)
    return segments


# =============================================================================
# Deal normalization
# =============================================================================


def normalize_deal(raw: dict[str, Any]) -> AdServerDeal:
    """Normalize a FreeWheel deal response to AdServerDeal."""
    deal_type_raw = raw.get("deal_type", "private_auction")
    deal_type = _DEAL_TYPE_MAP.get(deal_type_raw, deal_type_raw)

    status_raw = raw.get("status", "DRAFT")
    status = _DEAL_STATUS_MAP.get(status_raw, DealStatus.DRAFT)

    floor_price = raw.get("floor_price") or raw.get("floorPrice") or 0
    fixed_price = raw.get("fixed_price") or raw.get("fixedPrice") or 0

    return AdServerDeal(
        id=str(raw.get("id", raw.get("deal_id", ""))),
        deal_id=str(raw.get("deal_id", raw.get("id", ""))),
        name=raw.get("name"),
        deal_type=deal_type,
        floor_price_micros=dollars_to_micros(floor_price),
        fixed_price_micros=dollars_to_micros(fixed_price),
        currency=raw.get("currency", "USD"),
        buyer_seat_ids=raw.get("buyer_seat_ids", []),
        status=status,
        ad_server_type=AdServerType.FREEWHEEL,
        raw=raw,
    )


def normalize_booking_result(raw: dict[str, Any]) -> BookingResult:
    """Normalize a FreeWheel book_deal response to BookingResult.

    Philippe's book_deal() creates deals on both SH and BC but does NOT
    create campaign/line item/creative on BC. So the result has a deal
    but no order or line items.
    """
    deal = normalize_deal(raw) if raw else None

    return BookingResult(
        order=None,  # SH programmatic doesn't use orders
        line_items=[],  # No line items for SH programmatic
        deal=deal,
        ad_server_type=AdServerType.FREEWHEEL,
        success=deal is not None,
        error=raw.get("error") if raw else "Empty response",
    )
