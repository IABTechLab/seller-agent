# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""IAB Tech Lab Deal Sync API v1.0 — seller-side implementation.

Standardized one-way push from seller (SSP/publisher) to buyer (DSP)
for deal term transmission. Three deal distribution paths:

1. Direct push to buyer URL (IAB Deals API HTTP POST)
2. Through ad server (FreeWheel/GAM book_deal)
3. Through SSP MCP/REST (PubMatic, Index Exchange, etc.)

Deal lifecycle:
  seller creates deal → pushes to buyer → buyer accepts/rejects
  → seller queries status → deal goes active → delivery starts

Buyer seat status: pending → approved → ready_to_serve → active → complete
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# =============================================================================
# OpenRTB deal parameters
# =============================================================================

_DEAL_TYPE_CODES = {
    "PG": "PG",
    "PROGRAMMATICGUARANTEED": "PG",
    "PD": "PD",
    "PREFERREDDEAL": "PD",
    "PA": "PA",
    "PRIVATEAUCTION": "PA",
}


def build_openrtb_deal_params(
    deal_id: str,
    deal_type: Any,
    price: float,
    *,
    currency: str = "USD",
    seat_ids: Optional[list[str]] = None,
    advertiser_domains: Optional[list[str]] = None,
    guaranteed_impressions: Optional[int] = None,
) -> dict[str, Any]:
    """OpenRTB 2.x ``imp.pmp.deals[]`` parameters for a seller deal.

    Single source for every deal path (quote booking, proposal deal
    generation, Deals API sync). ``at`` follows OpenRTB: PG and PD are
    fixed-price deals (``at=3``, ``bidfloor`` is the deal price); PA is an
    auction (``at=1`` first price, ``bidfloor`` is the floor). ``wseat`` /
    ``wadomain`` are allow-lists; empty means unrestricted.
    """
    raw = str(getattr(deal_type, "value", deal_type) or "").upper().replace("_", "")
    code = _DEAL_TYPE_CODES.get(raw, "PD")
    params: dict[str, Any] = {
        "id": deal_id,
        "bidfloor": price,
        "bidfloorcur": currency,
        "at": 1 if code == "PA" else 3,
        "wseat": list(seat_ids or []),
        "wadomain": list(advertiser_domains or []),
    }
    if code == "PG" and guaranteed_impressions:
        params["ext"] = {"guaranteed": True, "impressions": guaranteed_impressions}
    return params


# =============================================================================
# IAB Deals API v1.0 models
# =============================================================================


class DealSyncStatus(str, Enum):
    """Seller-side deal status per IAB Deals API v1.0."""

    ACTIVE = "active"
    PAUSED = "paused"
    PENDING = "pending"
    ARCHIVED = "archived"


class BuyerSeatStatus(str, Enum):
    """Buyer-side deal acceptance status per IAB Deals API v1.0."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    READY_TO_SERVE = "ready_to_serve"
    ACTIVE = "active"
    COMPLETE = "complete"


class IABDealObject(BaseModel):
    """IAB Deals API v1.0 Deal object for push transmission.

    Maps from our DealOutput model to the IAB standard format.
    """

    deal_id: str
    name: Optional[str] = None
    deal_type: str  # programmaticguaranteed, preferreddeal, privateauction
    status: DealSyncStatus = DealSyncStatus.PENDING

    # Pricing
    floor_price_cpm: Optional[float] = None  # PA deals
    fixed_price_cpm: Optional[float] = None  # PG/PD deals
    currency: str = "USD"

    # Inventory
    inventory: Optional[dict[str, Any]] = None  # product/package references
    curation: Optional[dict[str, Any]] = None  # curator overlay if applicable

    # Terms
    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None

    # Buyer targeting
    buyer_seat_ids: list[str] = []  # DSP seat IDs (wseat in OpenRTB)

    # OpenRTB params for DSP activation
    openrtb_params: Optional[dict[str, Any]] = None

    # Seller identity
    seller_id: Optional[str] = None
    seller_domain: Optional[str] = None

    # Timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DealPushResult(BaseModel):
    """Result of pushing a deal to a buyer endpoint."""

    deal_id: str
    buyer_url: str
    success: bool
    buyer_status: Optional[BuyerSeatStatus] = None
    response_code: Optional[int] = None
    error: Optional[str] = None
    pushed_at: str = ""


class DealStatusQuery(BaseModel):
    """Result of querying a buyer for deal acceptance status."""

    deal_id: str
    buyer_url: str
    buyer_status: BuyerSeatStatus = BuyerSeatStatus.PENDING
    last_checked: str = ""
    error: Optional[str] = None


# =============================================================================
# Deal push service
# =============================================================================


class DealsAPIService:
    """IAB Deals API v1.0 push service.

    Pushes deals to configured buyer endpoints and queries for acceptance status.
    """

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def build_deal_object(
        self,
        deal_id: str,
        deal_type: str,
        price: float,
        *,
        name: Optional[str] = None,
        impressions: Optional[int] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        buyer_seat_ids: Optional[list[str]] = None,
        inventory: Optional[dict[str, Any]] = None,
        seller_id: Optional[str] = None,
        seller_domain: Optional[str] = None,
    ) -> IABDealObject:
        """Build an IAB Deal object from seller deal data.

        Maps our internal DealOutput format to IAB Deals API v1.0 spec.
        """
        # Normalize deal type
        deal_type_map = {
            "programmatic_guaranteed": "programmaticguaranteed",
            "preferred_deal": "preferreddeal",
            "private_auction": "privateauction",
            "PG": "programmaticguaranteed",
            "PD": "preferreddeal",
            "PA": "privateauction",
        }
        normalized_type = deal_type_map.get(deal_type, deal_type)

        # Pricing based on deal type
        is_fixed = normalized_type in ("programmaticguaranteed", "preferreddeal")
        floor_price = None if is_fixed else price
        fixed_price = price if is_fixed else None

        openrtb = build_openrtb_deal_params(
            deal_id, normalized_type, price, seat_ids=buyer_seat_ids
        )

        now = datetime.now(timezone.utc).isoformat()

        return IABDealObject(
            deal_id=deal_id,
            name=name or f"Deal {deal_id}",
            deal_type=normalized_type,
            status=DealSyncStatus.PENDING,
            floor_price_cpm=floor_price,
            fixed_price_cpm=fixed_price,
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            buyer_seat_ids=buyer_seat_ids or [],
            inventory=inventory,
            openrtb_params=openrtb,
            seller_id=seller_id,
            seller_domain=seller_domain,
            created_at=now,
            updated_at=now,
        )

    async def push_deal(
        self,
        deal: IABDealObject,
        buyer_url: str,
        *,
        api_key: Optional[str] = None,
    ) -> DealPushResult:
        """Push a deal to a buyer endpoint via HTTP POST.

        Args:
            deal: IAB Deal object to push
            buyer_url: Buyer's deal receiving endpoint (e.g., https://buyer.example.com/api/v1/deals/push)
            api_key: Optional API key for buyer authentication
        """
        now = datetime.now(timezone.utc).isoformat()

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    buyer_url,
                    json=deal.model_dump(exclude_none=True),
                    headers=headers,
                )

                if response.status_code in (200, 201, 202):
                    body = response.json() if response.content else {}
                    buyer_status = body.get("status", "pending")
                    return DealPushResult(
                        deal_id=deal.deal_id,
                        buyer_url=buyer_url,
                        success=True,
                        buyer_status=BuyerSeatStatus(buyer_status)
                        if buyer_status in BuyerSeatStatus.__members__.values()
                        else BuyerSeatStatus.PENDING,
                        response_code=response.status_code,
                        pushed_at=now,
                    )
                else:
                    return DealPushResult(
                        deal_id=deal.deal_id,
                        buyer_url=buyer_url,
                        success=False,
                        response_code=response.status_code,
                        error=f"HTTP {response.status_code}: {response.text[:200]}",
                        pushed_at=now,
                    )

        except Exception as e:
            return DealPushResult(
                deal_id=deal.deal_id,
                buyer_url=buyer_url,
                success=False,
                error=str(e),
                pushed_at=now,
            )

    async def query_deal_status(
        self,
        deal_id: str,
        buyer_url: str,
        *,
        api_key: Optional[str] = None,
    ) -> DealStatusQuery:
        """Query a buyer for the acceptance status of a deal.

        Polls the buyer's deal status endpoint.
        """
        now = datetime.now(timezone.utc).isoformat()

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{buyer_url}/{deal_id}/status",
                    headers=headers,
                )
                response.raise_for_status()
                body = response.json()

                status_str = body.get("status", "pending")
                return DealStatusQuery(
                    deal_id=deal_id,
                    buyer_url=buyer_url,
                    buyer_status=BuyerSeatStatus(status_str)
                    if status_str in BuyerSeatStatus.__members__.values()
                    else BuyerSeatStatus.PENDING,
                    last_checked=now,
                )

        except Exception as e:
            return DealStatusQuery(
                deal_id=deal_id,
                buyer_url=buyer_url,
                error=str(e),
                last_checked=now,
            )

    async def push_deal_to_multiple_buyers(
        self,
        deal: IABDealObject,
        buyer_configs: list[dict[str, str]],
    ) -> list[DealPushResult]:
        """Push a deal to multiple buyer endpoints.

        Args:
            deal: IAB Deal object
            buyer_configs: List of {"url": "...", "api_key": "..."} dicts
        """
        results = []
        for config in buyer_configs:
            result = await self.push_deal(
                deal=deal,
                buyer_url=config["url"],
                api_key=config.get("api_key"),
            )
            results.append(result)
        return results
