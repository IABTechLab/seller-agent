# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Execution Activation Flow - Sync deals to ad servers.

This flow handles:
- Creating execution orders from accepted proposals
- Syncing to ad server (GAM/FreeWheel)
- Two paths: IO/Order (budget committed) or Deal ID (access + pricing only)
- Managing creative assignments
- Tracking entity mappings
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Optional

from crewai.flow.flow import Flow, listen, or_, start

from ..clients import UnifiedClient, get_ad_server_client
from ..events.helpers import emit_event
from ..events.models import EventType
from ..models.core import DealType, ExecutionOrderStatus
from ..models.flow_state import (
    ExecutionStatus,
    SellerFlowState,
)


class ExecutionState(SellerFlowState):
    """State for execution activation flow."""

    # Input
    proposal_id: str = ""
    deal_id: str = ""
    execution_type: str = "deal_id"  # deal_id or io_order

    # Ad server config
    ad_server_config_id: Optional[str] = None

    # Execution tracking
    execution_order_id: str = ""
    ad_server_entity_id: str = ""
    sync_status: str = "pending"


class ExecutionActivationFlow(Flow[ExecutionState]):
    """Flow for activating deals in ad servers.

    Two Output Types:

    1. IO / Booked Order (Budget Committed)
       - GAM: Order + Line Items with budget, impressions, dates
       - FreeWheel: Insertion Order with committed spend
       - Budget lives in ad server, seller responsible for delivery

    2. Deal ID Only (Access + Pricing)
       - GAM: Programmatic Deal with price floor or fixed price
       - FreeWheel: Deal ID for programmatic activation
       - NO budget in deal - just "access pass" + pricing
       - Budget lives in DSP, buyer controls spend
    """

    def __init__(self) -> None:
        """Initialize the execution activation flow."""
        super().__init__()

    @start()
    async def initialize_execution(self) -> None:
        """Initialize the execution flow."""
        self.state.flow_id = str(uuid.uuid4())
        self.state.flow_type = "execution_activation"
        self.state.started_at = datetime.utcnow()
        self.state.status = ExecutionStatus.SYNCING_TO_AD_SERVER

        # Validate we have something to execute
        if not self.state.proposal_id and not self.state.deal_id:
            self.state.errors.append("Either proposal_id or deal_id required")
            self.state.status = ExecutionStatus.FAILED

    @listen(initialize_execution)
    async def create_execution_order(self) -> None:
        """Create execution order in OpenDirect."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        # Generate execution order ID
        self.state.execution_order_id = f"exec-{uuid.uuid4().hex[:8]}"

        # Isolated via create_task to prevent httpcore/anyio CancelScope
        # from leaking into the parent flow listener task.
        try:
            await asyncio.create_task(self._od_create_execution_order())
        except Exception as e:
            self.state.warnings.append(f"Execution order creation failed: {e}")

    async def _od_create_execution_order(self) -> None:
        """Create execution order in OpenDirect — runs in an isolated task."""
        async with UnifiedClient() as client:
            result = await client.create_execution_order(
                proposal_id=self.state.proposal_id or self.state.deal_id,
                execution_order_id=self.state.execution_order_id,
                status="draft",
                external_ids={
                    "execution_type": self.state.execution_type,
                    "deal_id": self.state.deal_id,
                },
            )
            if not result.success:
                self.state.warnings.append(f"Could not create execution order: {result.error}")

    @listen(create_execution_order)
    async def determine_sync_path(self) -> None:
        """Determine which sync path to use based on deal type."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        # Get deal info
        deal = self.state.deals.get(self.state.deal_id)

        if deal:
            # Deal ID path for PG/PD/PA
            if deal.deal_type in [
                DealType.PROGRAMMATIC_GUARANTEED,
                DealType.PREFERRED_DEAL,
                DealType.PRIVATE_AUCTION,
            ]:
                self.state.execution_type = "deal_id"
            else:
                self.state.execution_type = "io_order"
        else:
            # Default to deal_id path
            self.state.execution_type = "deal_id"

    @listen(determine_sync_path)
    async def sync_deal_id_to_ad_server(self) -> None:
        """Sync Deal ID to ad server (GAM/FreeWheel).

        Creates a programmatic deal with price floor or fixed price.
        NO budget commitment - just access + pricing terms.
        """
        if self.state.status == ExecutionStatus.FAILED:
            return

        if self.state.execution_type != "deal_id":
            return

        deal = self.state.deals.get(self.state.deal_id)
        if not deal:
            self.state.warnings.append("No deal found for sync")
            return

        try:
            ad_server = get_ad_server_client()
            async with ad_server:
                # Determine pricing
                is_fixed = deal.deal_type == DealType.PROGRAMMATIC_GUARANTEED
                floor_micros = 0 if is_fixed else int(deal.price * 1_000_000)
                fixed_micros = int(deal.price * 1_000_000) if is_fixed else 0

                result = await ad_server.create_deal(
                    deal_id=deal.deal_id,
                    name=f"Deal {deal.deal_id}",
                    deal_type=deal.deal_type.value
                    if hasattr(deal.deal_type, "value")
                    else str(deal.deal_type),
                    floor_price_micros=floor_micros,
                    fixed_price_micros=fixed_micros,
                )

                self.state.ad_server_entity_id = result.id
                self.state.sync_status = "synced"

                self.state.execution_orders[deal.deal_id] = {
                    "execution_order_id": self.state.execution_order_id,
                    "ad_server_type": ad_server.ad_server_type.value,
                    "ad_server_entity_id": result.id,
                    "entity_type": "programmatic_deal",
                    "price": deal.price,
                    "pricing_type": "fixed" if is_fixed else "floor",
                }
        except NotImplementedError as e:
            self.state.warnings.append(str(e))
        except Exception as e:
            self.state.errors.append(f"Deal sync failed: {e}")
            self.state.status = ExecutionStatus.FAILED

    @listen(determine_sync_path)
    async def sync_io_order_to_ad_server(self) -> None:
        """Sync IO/Order to ad server (GAM/FreeWheel).

        Creates Order + Line Items with budget commitment.
        Budget lives in ad server, seller responsible for delivery.
        """
        if self.state.status == ExecutionStatus.FAILED:
            return

        if self.state.execution_type != "io_order":
            return

        # Get proposal data for IO creation
        proposal_data = self.state.counter_proposals.get(self.state.proposal_id, {})

        try:
            ad_server = get_ad_server_client()
            async with ad_server:
                # Create order
                advertiser_name = proposal_data.get("advertiser_name", "Unknown Advertiser")
                order = await ad_server.create_order(
                    name=f"IO-{self.state.execution_order_id}",
                    advertiser_id=proposal_data.get("advertiser_id", ""),
                    advertiser_name=advertiser_name,
                    external_id=self.state.execution_order_id,
                )

                # Create line item
                line_item = await ad_server.create_line_item(
                    order_id=order.id,
                    name=f"Line-{self.state.execution_order_id}",
                    cost_micros=int(proposal_data.get("price", 0) * 1_000_000),
                    impressions_goal=proposal_data.get("impressions", -1),
                )

                self.state.ad_server_entity_id = order.id
                self.state.sync_status = "synced"

                self.state.execution_orders[self.state.proposal_id] = {
                    "execution_order_id": self.state.execution_order_id,
                    "ad_server_type": ad_server.ad_server_type.value,
                    "ad_server_order_id": order.id,
                    "ad_server_line_ids": [line_item.id],
                    "entity_type": "order",
                    "budget_committed": True,
                }
        except NotImplementedError as e:
            self.state.warnings.append(str(e))
        except Exception as e:
            self.state.errors.append(f"IO sync failed: {e}")
            self.state.status = ExecutionStatus.FAILED

    @listen(or_(sync_deal_id_to_ad_server, sync_io_order_to_ad_server))
    async def distribute_to_ssps(self) -> None:
        """Distribute deal to configured SSPs after ad server sync.

        If SSP connectors are configured, pushes the deal to the appropriate
        SSP(s) based on routing rules. The SSP handles DSP-side distribution.
        This runs in parallel with (not instead of) ad server sync.
        """
        if self.state.status == ExecutionStatus.FAILED:
            return

        deal = self.state.deals.get(self.state.deal_id)
        if not deal:
            return

        try:
            from ..config import get_settings

            settings = get_settings()

            if not settings.ssp_connectors:
                return  # No SSPs configured — skip

            from ..clients.ssp_base import SSPDealCreateRequest, SSPDealType
            from ..clients.ssp_factory import build_ssp_registry

            registry = build_ssp_registry(settings)
            if not registry.list_ssps():
                return

            # Map deal type
            deal_type_str = (
                deal.deal_type.value if hasattr(deal.deal_type, "value") else str(deal.deal_type)
            )
            ssp_deal_type = SSPDealType.PMP
            if "guaranteed" in deal_type_str:
                ssp_deal_type = SSPDealType.PG
            elif "preferred" in deal_type_str:
                ssp_deal_type = SSPDealType.PREFERRED

            create_req = SSPDealCreateRequest(
                deal_type=ssp_deal_type,
                name=f"Deal {deal.deal_id}",
                cpm=deal.price,
            )

            # Route to appropriate SSP
            ssp = registry.get_client_for(
                inventory_type=getattr(deal, "product_id", None),
                deal_type=deal_type_str,
            )

            async with ssp:
                ssp_result = await ssp.create_deal(create_req)

            self.state.execution_orders.setdefault(deal.deal_id, {})["ssp_deal"] = {
                "ssp_name": ssp_result.ssp_name,
                "ssp_deal_id": ssp_result.deal_id,
                "ssp_status": ssp_result.status.value,
            }

            await emit_event(
                event_type=EventType.DEAL_SYNCED,
                flow_id=self.state.flow_id,
                flow_type=self.state.flow_type,
                deal_id=self.state.deal_id,
                payload={"ssp_name": ssp_result.ssp_name, "ssp_deal_id": ssp_result.deal_id},
            )

        except Exception as e:
            # SSP distribution failure is non-fatal — deal still exists in ad server
            self.state.warnings.append(f"SSP distribution failed (non-fatal): {e}")

    @listen(distribute_to_ssps)
    async def update_execution_status(self) -> None:
        """Update execution order status after sync."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        try:
            await asyncio.create_task(self._od_update_execution_status())
        except Exception as e:
            self.state.warnings.append(f"Status update failed: {e}")

    async def _od_update_execution_status(self) -> None:
        """Update execution order status in OpenDirect — runs in an isolated task."""
        async with UnifiedClient() as client:
            await client.update_proposal(
                proposal_id=self.state.execution_order_id,
                status=ExecutionOrderStatus.BOOKED.value,
            )

    @listen(update_execution_status)
    async def finalize(self) -> None:
        """Finalize the execution flow."""
        self.state.status = ExecutionStatus.COMPLETED
        self.state.completed_at = datetime.utcnow()

        # Emit deal.synced event
        await emit_event(
            event_type=EventType.DEAL_SYNCED,
            flow_id=self.state.flow_id,
            flow_type=self.state.flow_type,
            deal_id=self.state.deal_id,
            payload={
                "sync_status": self.state.sync_status,
                "ad_server_entity_id": self.state.ad_server_entity_id,
                "execution_type": self.state.execution_type,
            },
        )

    def activate(
        self,
        deal_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        execution_type: str = "deal_id",
        deals: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Activate a deal or proposal in the ad server.

        Args:
            deal_id: Deal ID to activate
            proposal_id: Proposal ID to activate
            execution_type: Type of execution (deal_id or io_order)
            deals: Deal dictionary

        Returns:
            Activation result with ad server entity IDs
        """
        self.state.deal_id = deal_id or ""
        self.state.proposal_id = proposal_id or deal_id or ""
        self.state.execution_type = execution_type
        if deals:
            self.state.deals = deals

        # Run the flow
        self.kickoff()

        return {
            "execution_order_id": self.state.execution_order_id,
            "ad_server_entity_id": self.state.ad_server_entity_id,
            "execution_type": self.state.execution_type,
            "sync_status": self.state.sync_status,
            "execution_orders": self.state.execution_orders,
            "status": self.state.status.value,
            "errors": self.state.errors,
            "warnings": self.state.warnings,
        }
