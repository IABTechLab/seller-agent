# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Book Deal in GAM Tool - Orchestrate booking of accepted deals."""

from datetime import datetime
from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings
from ...models.gam import (
    GAMAdUnitTargeting,
    GAMBookingResult,
    GAMCostType,
    GAMGoal,
    GAMGoalType,
    GAMInventoryTargeting,
    GAMLineItemType,
    GAMMoney,
    GAMTargeting,
    GAMUnitType,
)


class BookDealInGAMInput(BaseModel):
    """Input schema for booking a deal in GAM."""

    deal_id: str = Field(
        description="The OpenDirect deal/proposal ID to book",
    )
    deal_type: str = Field(
        description="Deal type: programmatic_guaranteed, preferred_deal, or private_auction",
    )
    advertiser_name: str = Field(
        description="Advertiser company name",
    )
    agency_name: Optional[str] = Field(
        default=None,
        description="Agency name if applicable",
    )
    campaign_name: str = Field(
        description="Campaign/order name",
    )
    ad_unit_ids: list[str] = Field(
        description="List of GAM ad unit IDs to target",
    )
    impressions: int = Field(
        description="Goal number of impressions",
    )
    cpm_rate: float = Field(
        description="CPM rate in dollars",
    )
    pricing_model: str = Field(
        default="cpm",
        description="Pricing model: cpm, cpc, cpcv, or flat_fee",
    )
    start_date: str = Field(
        description="Start date in YYYY-MM-DD format",
    )
    end_date: str = Field(
        description="End date in YYYY-MM-DD format",
    )
    currency: str = Field(
        default="USD",
        description="Currency code",
    )
    audience_segment_ids: Optional[list[int]] = Field(
        default=None,
        description="GAM audience segment IDs for targeting",
    )


class BookDealInGAMTool(BaseTool):
    """Tool for booking accepted OpenDirect deals in Google Ad Manager.

    Orchestrates the full booking flow:
    1. Creates or finds the advertiser company
    2. Creates an order
    3. Creates line items with targeting
    4. Returns booking confirmation

    Handles different deal types appropriately:
    - Programmatic Guaranteed: SPONSORSHIP or STANDARD line items
    - Preferred Deal: PREFERRED_DEAL line items
    - Private Auction: Uses PrivateAuctionDeal API (separate flow)
    """

    name: str = "book_deal_in_gam"
    description: str = """Book an accepted OpenDirect deal as order and line items in GAM.
    Returns order ID, line item ID, and booking status."""
    args_schema: Type[BaseModel] = BookDealInGAMInput

    def _run(
        self,
        deal_id: str,
        deal_type: str,
        advertiser_name: str,
        campaign_name: str,
        ad_unit_ids: list[str],
        impressions: int,
        cpm_rate: float,
        start_date: str,
        end_date: str,
        agency_name: Optional[str] = None,
        pricing_model: str = "cpm",
        currency: str = "USD",
        audience_segment_ids: Optional[list[int]] = None,
    ) -> str:
        """Execute deal booking."""
        settings = get_settings()

        if not settings.gam_enabled:
            return "GAM integration is not enabled. Set GAM_ENABLED=true in your environment."

        if not settings.gam_network_code or not settings.gam_json_key_path:
            return (
                "GAM credentials not configured. Set GAM_NETWORK_CODE and "
                "GAM_JSON_KEY_PATH environment variables."
            )

        # Validate pricing model - reject CPV
        pricing_model_lower = pricing_model.lower()
        if pricing_model_lower == "cpv":
            return (
                "Error: GAM does not support CPV (cost per view) pricing model. "
                "Please use CPM, CPC, CPCV, or flat_fee pricing instead. "
                "Deal cannot be booked with CPV pricing."
            )

        # Map deal type
        deal_type_lower = deal_type.lower().replace("_", "")
        if deal_type_lower in ("privateauction", "pa"):
            return self._book_private_auction(
                deal_id=deal_id,
                ad_unit_ids=ad_unit_ids,
                floor_price=cpm_rate,
                currency=currency,
            )

        # Map pricing model to cost type
        cost_type_map = {
            "cpm": GAMCostType.CPM,
            "cpc": GAMCostType.CPC,
            "cpcv": GAMCostType.CPCV,
            "flat_fee": GAMCostType.CPD,
        }
        cost_type = cost_type_map.get(pricing_model_lower, GAMCostType.CPM)

        # Map deal type to line item type
        deal_type_map = {
            "programmaticguaranteed": GAMLineItemType.SPONSORSHIP,
            "pg": GAMLineItemType.SPONSORSHIP,
            "preferreddeal": GAMLineItemType.PREFERRED_DEAL,
            "pd": GAMLineItemType.PREFERRED_DEAL,
        }
        line_item_type = deal_type_map.get(deal_type_lower, GAMLineItemType.STANDARD)

        try:
            from ...clients import GAMSoapClient

            # Parse dates
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            client = GAMSoapClient(
                network_code=settings.gam_network_code,
                credentials_path=settings.gam_json_key_path,
            )
            client.connect()

            try:
                # Step 1: Get or create advertiser
                advertiser_id = client.get_or_create_advertiser(
                    name=advertiser_name,
                    external_id=deal_id,
                )

                # Step 2: Create order
                order = client.create_order(
                    name=f"{campaign_name} - {deal_id}",
                    advertiser_id=advertiser_id,
                    notes=f"OpenDirect Deal: {deal_id}",
                    external_order_id=deal_id,
                    is_programmatic=True,
                )

                # Step 3: Build targeting
                inventory_targeting = GAMInventoryTargeting(
                    targeted_ad_units=[
                        GAMAdUnitTargeting(ad_unit_id=uid, include_descendants=True)
                        for uid in ad_unit_ids
                    ]
                )
                targeting = GAMTargeting(inventory_targeting=inventory_targeting)

                # Add audience targeting if provided
                if audience_segment_ids:
                    from ...models.gam import GAMAudienceSegmentCriteria, GAMCustomCriteriaSet

                    audience_criteria = GAMAudienceSegmentCriteria(
                        operator="IS",
                        audience_segment_ids=audience_segment_ids,
                    )
                    targeting.custom_targeting = GAMCustomCriteriaSet(
                        logical_operator="AND",
                        children=[audience_criteria],
                    )

                # Step 4: Create line item
                cost_per_unit = GAMMoney(
                    currency_code=currency,
                    micro_amount=int(cpm_rate * 1_000_000),
                )
                goal = GAMGoal(
                    goal_type=GAMGoalType.LIFETIME,
                    unit_type=GAMUnitType.IMPRESSIONS,
                    units=impressions,
                )

                line_item = client.create_line_item(
                    order_id=order.id,
                    name=f"Line - {deal_id}",
                    line_item_type=line_item_type,
                    targeting=targeting,
                    cost_per_unit=cost_per_unit,
                    goal=goal,
                    start_time=start_dt,
                    end_time=end_dt,
                    cost_type=cost_type,
                    external_id=deal_id,
                )

                # Format booking result
                GAMBookingResult(
                    success=True,
                    order_id=order.id,
                    line_item_id=line_item.id,
                    deal_id=deal_id,
                    status="BOOKED",
                    message="Deal successfully booked in GAM",
                )

                # Write gam_order_id back to the deal record so the
                # performance endpoint can query real delivery data
                try:
                    import asyncio

                    from ...storage.factory import get_storage

                    async def _persist_gam_order_id() -> None:
                        storage = await get_storage()
                        deal = await storage.get_deal(deal_id)
                        if deal:
                            deal["gam_order_id"] = order.id
                            await storage.set_deal(deal_id, deal)

                    asyncio.run(_persist_gam_order_id())
                except Exception:
                    pass  # Best-effort — never block the booking result

                lines = [
                    "Deal booked successfully in GAM:\n",
                    f"- OpenDirect Deal ID: {deal_id}",
                    f"- Deal Type: {deal_type}",
                    f"- GAM Order ID: {order.id}",
                    f"- GAM Line Item ID: {line_item.id}",
                    f"- Order Status: {order.status.value}",
                    f"- Line Item Status: {line_item.status.value}",
                    f"- Advertiser: {advertiser_name} (ID: {advertiser_id})",
                    f"- Cost: ${cpm_rate:.2f} {currency} {cost_type.value}",
                    f"- Goal: {impressions:,} impressions",
                    f"- Flight: {start_date} to {end_date}",
                    f"- Targeted ad units: {len(ad_unit_ids)}",
                ]

                if audience_segment_ids:
                    lines.append(f"- Audience segments: {len(audience_segment_ids)}")

                lines.append(
                    "\nNote: Order is in DRAFT status. Approve the order in GAM to start delivery."
                )

                return "\n".join(lines)

            finally:
                client.disconnect()

        except ImportError as e:
            return f"GAM client dependencies not installed: {e}"
        except ValueError as e:
            return f"Configuration error: {e}"
        except Exception as e:
            return f"Error booking deal: {e}"

    def _book_private_auction(
        self,
        deal_id: str,
        ad_unit_ids: list[str],
        floor_price: float,
        currency: str,
    ) -> str:
        """Book a private auction deal using the REST API."""
        return (
            f"Private auction deal booking requires a different flow.\n"
            f"Use the create_private_auction_deal tool with:\n"
            f"- deal_id: {deal_id}\n"
            f"- floor_price: {floor_price}\n"
            f"- currency: {currency}\n"
            f"- ad_unit_ids: {ad_unit_ids}\n"
            f"\nPrivate auctions do not create Orders/LineItems. "
            f"They use the PrivateAuctionDeal API instead."
        )
