# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Google Ad Manager adapter implementing the AdServerClient interface.

Wraps the existing GAMSoapClient (writes) and GAMRestClient (reads) behind
the unified AdServerClient abstraction.
"""

from datetime import datetime
from typing import Any, Optional

from ..models.gam import (
    GAMAdUnitTargeting,
    GAMCostType,
    GAMGoal,
    GAMGoalType,
    GAMInventoryTargeting,
    GAMLineItemType,
    GAMMoney,
    GAMTargeting,
    GAMUnitType,
)
from .ad_server_base import (
    AdServerAudienceSegment,
    AdServerClient,
    AdServerDeal,
    AdServerInventoryItem,
    AdServerLineItem,
    AdServerOrder,
    AdServerType,
    BookingResult,
    DealStatus,
    LineItemStatus,
    OrderStatus,
)
from .gam_rest_client import GAMRestClient
from .gam_soap_client import GAMSoapClient

# Status mapping from GAM-specific to normalized
_ORDER_STATUS_MAP: dict[str, OrderStatus] = {
    "DRAFT": OrderStatus.DRAFT,
    "PENDING_APPROVAL": OrderStatus.PENDING_APPROVAL,
    "APPROVED": OrderStatus.APPROVED,
    "DISAPPROVED": OrderStatus.REJECTED,
    "PAUSED": OrderStatus.PAUSED,
    "CANCELED": OrderStatus.CANCELED,
    "DELETED": OrderStatus.CANCELED,
}

_LINE_ITEM_STATUS_MAP: dict[str, LineItemStatus] = {
    "DRAFT": LineItemStatus.DRAFT,
    "READY": LineItemStatus.READY,
    "DELIVERING": LineItemStatus.DELIVERING,
    "DELIVERY_EXTENDED": LineItemStatus.DELIVERING,
    "PAUSED": LineItemStatus.PAUSED,
    "COMPLETED": LineItemStatus.COMPLETED,
    "CANCELED": LineItemStatus.CANCELED,
    "INACTIVE": LineItemStatus.DRAFT,
}


class GAMAdServerClient(AdServerClient):
    """GAM implementation of the AdServerClient interface.

    Delegates writes to GAMSoapClient and reads to GAMRestClient.
    """

    ad_server_type = AdServerType.GOOGLE_AD_MANAGER

    def __init__(self) -> None:
        self._soap = GAMSoapClient()
        self._rest = GAMRestClient()

    async def connect(self) -> None:
        self._soap.connect()
        await self._rest.connect()

    async def disconnect(self) -> None:
        self._soap.disconnect()
        await self._rest.disconnect()

    # -- Order / IO Operations --

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
        # If we have a name but no ID, look up or create the advertiser
        resolved_advertiser_id = advertiser_id
        if advertiser_name and not advertiser_id:
            resolved_advertiser_id = self._soap.get_or_create_advertiser(advertiser_name)

        gam_order = self._soap.create_order(
            name=name,
            advertiser_id=resolved_advertiser_id,
            agency_id=agency_id,
            notes=notes,
            external_order_id=external_id,
        )

        return AdServerOrder(
            id=gam_order.id,
            name=gam_order.name,
            advertiser_id=gam_order.advertiser_id,
            advertiser_name=advertiser_name,
            status=_ORDER_STATUS_MAP.get(gam_order.status.value, OrderStatus.DRAFT),
            external_id=gam_order.external_order_id,
            notes=gam_order.notes,
            ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
        )

    async def get_order(self, order_id: str) -> AdServerOrder:
        gam_order = await self._rest.get_order(order_id)
        return AdServerOrder(
            id=gam_order.id,
            name=gam_order.name,
            advertiser_id=gam_order.advertiser_id,
            status=_ORDER_STATUS_MAP.get(
                gam_order.status.value if hasattr(gam_order.status, "value") else gam_order.status,
                OrderStatus.DRAFT,
            ),
            external_id=gam_order.external_order_id,
            notes=gam_order.notes,
            ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
        )

    async def approve_order(self, order_id: str) -> AdServerOrder:
        gam_order = self._soap.approve_order(order_id)
        return AdServerOrder(
            id=gam_order.id,
            name=gam_order.name,
            advertiser_id=gam_order.advertiser_id,
            status=_ORDER_STATUS_MAP.get(gam_order.status.value, OrderStatus.APPROVED),
            external_id=gam_order.external_order_id,
            notes=gam_order.notes,
            ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
        )

    # -- Line Item Operations --

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
        # Build GAM-specific targeting
        gam_targeting = GAMTargeting()
        if targeting and "ad_unit_ids" in targeting:
            gam_targeting.inventory_targeting = GAMInventoryTargeting(
                targeted_ad_units=[
                    GAMAdUnitTargeting(ad_unit_id=uid, include_descendants=True)
                    for uid in targeting["ad_unit_ids"]
                ]
            )

        # Map cost type
        gam_cost_type = (
            GAMCostType(cost_type)
            if cost_type in [e.value for e in GAMCostType]
            else GAMCostType.CPM
        )

        # Determine line item type based on deal context
        line_item_type = GAMLineItemType.STANDARD

        gam_line_item = self._soap.create_line_item(
            order_id=order_id,
            name=name,
            line_item_type=line_item_type,
            targeting=gam_targeting,
            cost_per_unit=GAMMoney(currency_code=currency, micro_amount=cost_micros),
            goal=GAMGoal(
                goal_type=GAMGoalType.LIFETIME,
                unit_type=GAMUnitType.IMPRESSIONS,
                units=impressions_goal,
            ),
            start_time=start_time or datetime.utcnow(),
            end_time=end_time or datetime(2099, 12, 31),
            cost_type=gam_cost_type,
            creative_sizes=creative_sizes,
            external_id=external_id,
        )

        return AdServerLineItem(
            id=gam_line_item.id,
            order_id=gam_line_item.order_id,
            name=gam_line_item.name,
            status=_LINE_ITEM_STATUS_MAP.get(gam_line_item.status.value, LineItemStatus.DRAFT),
            cost_type=cost_type,
            cost_micros=cost_micros,
            currency=currency,
            impressions_goal=impressions_goal,
            start_time=start_time,
            end_time=end_time,
            external_id=gam_line_item.external_id,
            ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
        )

    async def update_line_item(
        self,
        line_item_id: str,
        updates: dict[str, Any],
    ) -> AdServerLineItem:
        gam_line_item = self._soap.update_line_item(line_item_id, updates)
        return AdServerLineItem(
            id=gam_line_item.id,
            order_id=gam_line_item.order_id,
            name=gam_line_item.name,
            status=_LINE_ITEM_STATUS_MAP.get(gam_line_item.status.value, LineItemStatus.DRAFT),
            cost_type=gam_line_item.cost_type.value,
            cost_micros=gam_line_item.cost_per_unit.micro_amount,
            currency=gam_line_item.cost_per_unit.currency_code,
            external_id=gam_line_item.external_id,
            ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
        )

    # -- Programmatic Deal Operations --

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
        # For GAM, private auctions are the primary deal mechanism
        # Create a private auction then add a deal to it
        auction = await self._rest.create_private_auction(
            display_name=name or f"Deal-{deal_id}",
            description=f"Programmatic deal {deal_id}",
        )

        # Determine floor price
        floor_cpm = floor_price_micros / 1_000_000 if floor_price_micros else 0
        if fixed_price_micros:
            floor_cpm = fixed_price_micros / 1_000_000

        # Build targeting if provided
        gam_targeting = None
        if targeting and "ad_unit_ids" in targeting:
            gam_targeting = GAMTargeting(
                inventory_targeting=GAMInventoryTargeting(
                    targeted_ad_units=[
                        GAMAdUnitTargeting(ad_unit_id=uid, include_descendants=True)
                        for uid in targeting["ad_unit_ids"]
                    ]
                )
            )

        # Create deal within the auction
        buyer_id = buyer_seat_ids[0] if buyer_seat_ids else ""
        gam_deal = await self._rest.create_private_auction_deal(
            private_auction_id=auction.id,
            buyer_account_id=buyer_id,
            floor_price=floor_cpm,
            currency=currency,
            targeting=gam_targeting,
            external_deal_id=deal_id,
        )

        return AdServerDeal(
            id=gam_deal.id,
            deal_id=deal_id,
            name=name,
            deal_type=deal_type,
            floor_price_micros=floor_price_micros,
            fixed_price_micros=fixed_price_micros,
            currency=currency,
            buyer_seat_ids=buyer_seat_ids or [],
            status=DealStatus.ACTIVE,
            ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
        )

    async def update_deal(
        self,
        deal_id: str,
        updates: dict[str, Any],
    ) -> AdServerDeal:
        floor_price = updates.get("floor_price_micros")
        floor_cpm = floor_price / 1_000_000 if floor_price else None

        gam_deal = await self._rest.update_private_auction_deal(
            deal_id=deal_id,
            floor_price=floor_cpm,
        )

        return AdServerDeal(
            id=gam_deal.id,
            deal_id=gam_deal.external_deal_id or deal_id,
            floor_price_micros=gam_deal.floor_price.micro_amount if gam_deal.floor_price else 0,
            currency=gam_deal.floor_price.currency_code if gam_deal.floor_price else "USD",
            status=DealStatus.ACTIVE,
            ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
        )

    # -- Inventory Operations --

    async def list_inventory(
        self,
        *,
        limit: int = 100,
        filter_str: Optional[str] = None,
    ) -> list[AdServerInventoryItem]:
        ad_units, _ = await self._rest.list_ad_units(
            page_size=limit,
            filter_str=filter_str,
        )

        return [
            AdServerInventoryItem(
                id=unit.id,
                name=unit.name,
                parent_id=unit.parent_id,
                status=unit.status,
                sizes=[(s.size.width, s.size.height) for s in (unit.ad_unit_sizes or []) if s.size],
                ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
            )
            for unit in ad_units
        ]

    # -- Audience Operations --

    async def list_audience_segments(
        self,
        *,
        limit: int = 500,
        filter_str: Optional[str] = None,
    ) -> list[AdServerAudienceSegment]:
        # Audience segments use the SOAP client
        gam_segments = self._soap.list_audience_segments(
            filter_statement=filter_str,
            limit=limit,
        )

        return [
            AdServerAudienceSegment(
                id=str(seg.id),
                name=seg.name,
                description=seg.description,
                size=seg.size,
                status=seg.status.value,
                ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
            )
            for seg in gam_segments
        ]

    # -- High-Level Booking --

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
        try:
            # 1. Get or create advertiser
            advertiser_id = self._soap.get_or_create_advertiser(advertiser_name)

            # 2. Create order
            order = await self.create_order(
                name=f"Order for {deal_id}",
                advertiser_id=advertiser_id,
                advertiser_name=advertiser_name,
                external_id=deal_id,
            )

            # 3. Create line item
            cost_micros = fixed_price_micros or floor_price_micros
            line_item = await self.create_line_item(
                order_id=order.id,
                name=f"Line Item for {deal_id}",
                cost_micros=cost_micros,
                currency=currency,
                impressions_goal=impressions_goal,
                start_time=start_time,
                end_time=end_time,
                targeting=targeting,
                creative_sizes=creative_sizes,
                external_id=deal_id,
            )

            # 4. Create programmatic deal
            deal = await self.create_deal(
                deal_id=deal_id,
                name=f"Deal {deal_id} - {advertiser_name}",
                deal_type=deal_type,
                floor_price_micros=floor_price_micros,
                fixed_price_micros=fixed_price_micros,
                currency=currency,
            )

            # 5. Write gam_order_id back to the stored deal record so the
            # performance endpoint can serve real delivery data for this deal
            # (main PR #12 parity — bead ar-j8hl). Best-effort: a storage
            # hiccup must never fail the booking itself.
            try:
                from ..storage.factory import get_storage

                storage = await get_storage()
                stored = await storage.get_deal(deal_id)
                if stored is not None:
                    stored["gam_order_id"] = order.id
                    await storage.set_deal(deal_id, stored)
            except Exception:  # noqa: BLE001 — best-effort linkage only
                pass

            return BookingResult(
                order=order,
                line_items=[line_item],
                deal=deal,
                ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
                success=True,
            )

        except Exception as e:
            return BookingResult(
                ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
                success=False,
                error=str(e),
            )
