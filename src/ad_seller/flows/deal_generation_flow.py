# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal ID Generation Flow - Create Deal IDs from accepted proposals.

This flow handles:
- Creating Deal IDs (PG, PD, PA) from accepted proposals
- Generating deal parameters compatible with OpenRTB
- Output: Deal ID + pricing (floor or fixed) - NO budget in deal
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Optional

from crewai.flow.flow import Flow, listen, start

from ..clients import UnifiedClient
from ..config import get_settings
from ..events.helpers import emit_event
from ..events.models import EventType
from ..models.core import DealType, PricingModel
from ..models.flow_state import (
    DealOutput,
    ExecutionStatus,
    SellerFlowState,
)


class DealGenerationState(SellerFlowState):
    """State for deal generation flow."""

    # Input
    proposal_id: str = ""
    proposal_data: dict[str, Any] = {}

    # Deal output
    deal_output: Optional[DealOutput] = None

    # External references
    openrtb_deal_params: dict[str, Any] = {}


class DealGenerationFlow(Flow[DealGenerationState]):
    """Flow for generating Deal IDs from accepted proposals.

    Creates deals compatible with:
    - Agentic buyers (ad_buyer_system via MCP)
    - Traditional DSPs (TTD, Amazon DSP, DV360 via deal ID)

    Deal Types:
    - PG (Programmatic Guaranteed): Fixed price, guaranteed delivery
    - PD (Preferred Deal): Fixed price, non-guaranteed
    - PA (Private Auction): Floor price, auction-based

    Important: Deal IDs contain pricing only, NOT budget.
    Budget lives in the DSP, not the ad server/deal.
    """

    def __init__(self) -> None:
        """Initialize the deal generation flow."""
        super().__init__()
        self._settings = get_settings()

    @start()
    async def validate_proposal(self) -> None:
        """Validate the proposal is accepted and ready for deal creation."""
        self.state.flow_id = str(uuid.uuid4())
        self.state.flow_type = "deal_generation"
        self.state.started_at = datetime.utcnow()
        self.state.status = ExecutionStatus.EVALUATING

        # Check proposal is accepted
        if self.state.proposal_id not in self.state.accepted_proposals:
            # Allow if proposal_data indicates acceptance
            status = self.state.proposal_data.get("status", "")
            if status != "accepted":
                self.state.errors.append(f"Proposal {self.state.proposal_id} is not accepted")
                self.state.status = ExecutionStatus.FAILED

    @listen(validate_proposal)
    async def determine_deal_type(self) -> None:
        """Determine the deal type from proposal."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        deal_type_str = self.state.proposal_data.get("deal_type", "preferred_deal")

        # Map string to enum
        deal_type_map = {
            "programmatic_guaranteed": DealType.PROGRAMMATIC_GUARANTEED,
            "programmaticguaranteed": DealType.PROGRAMMATIC_GUARANTEED,
            "pg": DealType.PROGRAMMATIC_GUARANTEED,
            "preferred_deal": DealType.PREFERRED_DEAL,
            "preferreddeal": DealType.PREFERRED_DEAL,
            "pd": DealType.PREFERRED_DEAL,
            "private_auction": DealType.PRIVATE_AUCTION,
            "privateauction": DealType.PRIVATE_AUCTION,
            "pa": DealType.PRIVATE_AUCTION,
        }

        deal_type = deal_type_map.get(deal_type_str.lower(), DealType.PREFERRED_DEAL)
        self.state.proposal_data["deal_type_enum"] = deal_type

    @listen(determine_deal_type)
    async def generate_deal_id(self) -> None:
        """Generate a unique Deal ID."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        # Generate Deal ID with seller prefix
        seller_prefix = (
            self.state.seller_organization_id[:4] if self.state.seller_organization_id else "SELL"
        )
        deal_id = f"{seller_prefix}-{uuid.uuid4().hex[:12].upper()}"

        self.state.proposal_data["generated_deal_id"] = deal_id

    @listen(generate_deal_id)
    async def create_deal_output(self) -> None:
        """Create the deal output structure."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        deal_type = self.state.proposal_data.get("deal_type_enum", DealType.PREFERRED_DEAL)
        price = self.state.proposal_data.get("price", 0)

        # Create DealOutput
        self.state.deal_output = DealOutput(
            deal_id=self.state.proposal_data["generated_deal_id"],
            deal_type=deal_type,
            proposal_id=self.state.proposal_id,
            product_id=self.state.proposal_data.get("product_id", ""),
            price=price,
            pricing_model=PricingModel.CPM,
            currency=self._settings.default_currency,
            buyer_organization_id=self.state.proposal_data.get("buyer_id", ""),
            seller_organization_id=self.state.seller_organization_id,
            flight_start=self.state.proposal_data.get("start_date", ""),
            flight_end=self.state.proposal_data.get("end_date", ""),
            activation_type="traditional_dsp",  # Default, can be "agentic"
            dsp_compatible=True,
        )

        # Set deal-type specific fields
        if deal_type == DealType.PROGRAMMATIC_GUARANTEED:
            self.state.deal_output.guaranteed_impressions = self.state.proposal_data.get(
                "impressions"
            )
            # Note: Budget is NOT set on the deal - it lives in the DSP
        elif deal_type in [DealType.PREFERRED_DEAL, DealType.PRIVATE_AUCTION]:
            self.state.deal_output.floor_price = price

    @listen(create_deal_output)
    async def generate_openrtb_params(self) -> None:
        """Generate OpenRTB-compatible deal parameters.

        These parameters can be used by traditional DSPs to activate the deal.
        """
        if self.state.status == ExecutionStatus.FAILED or not self.state.deal_output:
            return

        deal = self.state.deal_output

        # OpenRTB 2.5 compatible deal parameters
        self.state.openrtb_deal_params = {
            "id": deal.deal_id,
            "bidfloor": deal.floor_price or deal.price,
            "bidfloorcur": deal.currency,
            "at": 1 if deal.deal_type == DealType.PRIVATE_AUCTION else 3,  # 1=first-price, 3=fixed
            "wseat": [],  # Allowed buyer seats (empty = all)
            "wadomain": [],  # Allowed advertiser domains
        }

        # Add PG-specific fields
        if deal.deal_type == DealType.PROGRAMMATIC_GUARANTEED:
            self.state.openrtb_deal_params["ext"] = {
                "guaranteed": True,
                "impressions": deal.guaranteed_impressions,
            }

    @listen(generate_openrtb_params)
    async def register_deal(self) -> None:
        """Register the deal in the OpenDirect system."""
        if self.state.status == ExecutionStatus.FAILED or not self.state.deal_output:
            return

        self.state.status = ExecutionStatus.DEAL_CREATED

        # Store deal in state
        self.state.deals[self.state.deal_output.deal_id] = self.state.deal_output

        # Register with OpenDirect server (if available).
        # Isolated via create_task to prevent httpcore/anyio CancelScope
        # from leaking into the parent flow listener task.
        try:
            await asyncio.create_task(self._od_register_deal())
        except Exception as e:
            self.state.warnings.append(f"Failed to register deal with server: {e}")

        await emit_event(
            event_type=EventType.DEAL_REGISTERED,
            flow_id=self.state.flow_id,
            flow_type=self.state.flow_type,
            proposal_id=self.state.proposal_id,
            deal_id=self.state.deal_output.deal_id,
            payload={
                "deal_type": self.state.deal_output.deal_type.value,
                "price": self.state.deal_output.price,
            },
        )

    async def _od_register_deal(self) -> None:
        """Register deal with OpenDirect — runs in an isolated task."""
        async with UnifiedClient() as client:
            result = await client.create_execution_order(
                proposal_id=self.state.proposal_id,
                execution_order_id=self.state.deal_output.deal_id,
                status="booked",
                external_ids={
                    "openrtb_deal_id": self.state.deal_output.deal_id,
                    "deal_type": self.state.deal_output.deal_type.value,
                },
            )
            if result.success:
                self.state.deal_output.ad_server_deal_id = result.data.get("executionorderid")

    @listen(register_deal)
    async def finalize(self) -> None:
        """Finalize the deal generation flow."""
        self.state.status = ExecutionStatus.COMPLETED
        self.state.completed_at = datetime.utcnow()

    def generate_deal(
        self,
        proposal_id: str,
        proposal_data: dict[str, Any],
        seller_organization_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Generate a deal from an accepted proposal.

        Args:
            proposal_id: The accepted proposal ID
            proposal_data: Proposal details including pricing and terms
            seller_organization_id: Seller organization ID

        Returns:
            Deal generation result with Deal ID and OpenRTB params
        """
        self.state.proposal_id = proposal_id
        self.state.proposal_data = proposal_data
        self.state.seller_organization_id = (
            seller_organization_id or self._settings.seller_organization_id or ""
        )
        self.state.accepted_proposals.append(proposal_id)  # Assume accepted

        # Run the flow
        self.kickoff()

        return {
            "deal_id": self.state.deal_output.deal_id if self.state.deal_output else None,
            "deal_type": self.state.deal_output.deal_type.value if self.state.deal_output else None,
            "price": self.state.deal_output.price if self.state.deal_output else None,
            "pricing_model": "CPM",
            "openrtb_params": self.state.openrtb_deal_params,
            "activation_instructions": {
                "agentic": "Use ad_buyer_system to activate via MCP/A2A",
                "traditional_dsp": f"Enter Deal ID '{self.state.deal_output.deal_id if self.state.deal_output else ''}' in your DSP",
            },
            "status": self.state.status.value,
            "errors": self.state.errors,
        }
