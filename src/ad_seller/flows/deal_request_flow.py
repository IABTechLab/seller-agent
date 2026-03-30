# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal Request Flow - Support deal creation for any buyer workflow.

This flow enables the seller agent to create deals via direct request:
- Human buyer or agency requests deal via chat/API
- Seller agent validates, prices, creates Deal ID
- Deal ID shared with buyer for DSP activation
- Full seller agent value for both agentic and traditional buyers

Compatible with traditional DSPs (TTD, Amazon DSP, DV360) and
agentic buyer workflows (Deal Library sub-agent).
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from crewai.flow.flow import Flow, listen, start

from ..config import get_settings
from ..models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity
from ..models.core import DealType, PricingModel
from ..models.flow_state import (
    DealOutput,
    ExecutionStatus,
    PricingDecision,
    SellerFlowState,
)


class DealRequestState(SellerFlowState):
    """State for deal request flow."""

    # Request details (from human/chat)
    request_type: str = "deal_request"  # deal_request, inquiry, negotiation
    request_text: str = ""
    buyer_context: Optional[BuyerContext] = None

    # Parsed request
    parsed_request: dict[str, Any] = {}

    # Response
    response_text: str = ""
    deal_output: Optional[DealOutput] = None


class DealRequestFlow(Flow[DealRequestState]):
    """Flow for processing deal requests from buyers.

    Workflow:
    1. Receive request from buyer (via chat, API, agent)
    2. Parse and understand the request
    3. Validate buyer identity and access tier
    4. Apply tiered pricing
    5. Check availability
    6. Generate Deal ID
    7. Provide activation instructions for DSP

    The seller agent provides full value for any buyer workflow:
    - Automated pricing and validation
    - Deal ID generation
    - Clear activation instructions
    """

    def __init__(self) -> None:
        """Initialize the deal request flow."""
        super().__init__()
        self._settings = get_settings()

    @start()
    async def receive_request(self) -> None:
        """Receive and categorize the buyer's request."""
        self.state.flow_id = str(uuid.uuid4())
        self.state.flow_type = "deal_request"
        self.state.started_at = datetime.utcnow()
        self.state.status = ExecutionStatus.PROPOSAL_RECEIVED

        # Parse the natural language request
        request_lower = self.state.request_text.lower()

        if any(word in request_lower for word in ["deal", "create", "book", "buy"]):
            self.state.request_type = "deal_request"
        elif any(word in request_lower for word in ["price", "cost", "rate", "cpm"]):
            self.state.request_type = "inquiry"
        elif any(word in request_lower for word in ["counter", "negotiate", "lower"]):
            self.state.request_type = "negotiation"
        else:
            self.state.request_type = "inquiry"

    @listen(receive_request)
    async def validate_buyer(self) -> None:
        """Validate buyer identity and determine access tier."""
        if not self.state.buyer_context:
            # Create anonymous buyer context
            self.state.buyer_context = BuyerContext(
                identity=BuyerIdentity(),
                is_authenticated=False,
            )

        tier = self.state.buyer_context.effective_tier
        self.state.parsed_request["access_tier"] = tier.value

        # Check if buyer can create deals
        if tier == AccessTier.PUBLIC and self.state.request_type == "deal_request":
            self.state.response_text = (
                "To create a deal, please authenticate with your agency or advertiser credentials. "
                "You can browse our catalog and pricing as a guest."
            )
            self.state.status = ExecutionStatus.FAILED
            return

    @listen(validate_buyer)
    async def parse_deal_requirements(self) -> None:
        """Parse deal requirements from the request."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        if self.state.request_type != "deal_request":
            return

        self.state.status = ExecutionStatus.EVALUATING

        # Extract deal parameters from request
        # In production, this would use NLP/LLM to parse
        request = self.state.request_text.lower()

        # Simple parsing for demo
        parsed = {
            "product_type": "display",  # Default
            "impressions": 1000000,  # Default 1M
            "deal_type": "preferred_deal",
        }

        # Detect product type
        if "ctv" in request or "streaming" in request or "tv" in request:
            parsed["product_type"] = "ctv"
        elif "video" in request:
            parsed["product_type"] = "video"
        elif "mobile" in request or "app" in request:
            parsed["product_type"] = "mobile_app"
        elif "native" in request:
            parsed["product_type"] = "native"

        # Detect deal type
        if "guaranteed" in request or "pg" in request:
            parsed["deal_type"] = "programmatic_guaranteed"
        elif "private auction" in request or "pa" in request:
            parsed["deal_type"] = "private_auction"

        self.state.parsed_request.update(parsed)

    @listen(parse_deal_requirements)
    async def apply_tiered_pricing(self) -> None:
        """Apply tiered pricing based on buyer identity."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        if self.state.request_type not in ["deal_request", "inquiry"]:
            return

        tier = AccessTier(self.state.parsed_request.get("access_tier", "public"))
        product_type = self.state.parsed_request.get("product_type", "display")

        # Base prices by product type
        base_prices = {
            "display": 12.0,
            "video": 25.0,
            "ctv": 35.0,
            "mobile_app": 18.0,
            "native": 10.0,
        }

        base_price = base_prices.get(product_type, 12.0)

        # Apply tier discounts
        tier_discounts = {
            AccessTier.PUBLIC: 0.0,
            AccessTier.SEAT: 0.05,
            AccessTier.AGENCY: 0.10,
            AccessTier.ADVERTISER: 0.15,
        }

        discount = tier_discounts.get(tier, 0.0)
        final_price = base_price * (1 - discount)

        self.state.pricing_decisions[product_type] = PricingDecision(
            product_id=product_type,
            deal_type=DealType(
                self.state.parsed_request.get("deal_type", "preferred_deal").replace("_", "")
            ),
            buyer_tier=tier.value,
            base_price=base_price,
            tier_discount=discount,
            final_price=round(final_price, 2),
            rationale=f"Base ${base_price} CPM with {discount * 100:.0f}% {tier.value} tier discount",
        )

    @listen(apply_tiered_pricing)
    async def create_deal_for_dsp(self) -> None:
        """Create a Deal ID for DSP activation."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        if self.state.request_type != "deal_request":
            return

        product_type = self.state.parsed_request.get("product_type", "display")
        pricing = self.state.pricing_decisions.get(product_type)

        if not pricing:
            self.state.errors.append("Pricing not available")
            return

        # Generate Deal ID
        seller_prefix = (self.state.seller_organization_id or "SELL")[:4].upper()
        deal_id = f"{seller_prefix}-{uuid.uuid4().hex[:12].upper()}"

        # Determine deal type enum
        deal_type_str = self.state.parsed_request.get("deal_type", "preferred_deal")
        deal_type_map = {
            "programmatic_guaranteed": DealType.PROGRAMMATIC_GUARANTEED,
            "preferred_deal": DealType.PREFERRED_DEAL,
            "private_auction": DealType.PRIVATE_AUCTION,
        }
        deal_type = deal_type_map.get(deal_type_str, DealType.PREFERRED_DEAL)

        # Create deal output
        self.state.deal_output = DealOutput(
            deal_id=deal_id,
            deal_type=deal_type,
            proposal_id=f"human-{self.state.flow_id}",
            product_id=product_type,
            price=pricing.final_price,
            pricing_model=PricingModel.CPM,
            buyer_organization_id=self.state.buyer_context.identity.agency_id or "human-buyer",
            seller_organization_id=self.state.seller_organization_id or "default-seller",
            flight_start=datetime.utcnow().strftime("%Y-%m-%d"),
            flight_end=(datetime.utcnow().replace(month=datetime.utcnow().month + 1)).strftime(
                "%Y-%m-%d"
            ),
            activation_type="traditional_dsp",
            dsp_compatible=True,
        )

        if deal_type == DealType.PRIVATE_AUCTION:
            self.state.deal_output.floor_price = pricing.final_price

        self.state.deals[deal_id] = self.state.deal_output
        self.state.status = ExecutionStatus.DEAL_CREATED

    @listen(create_deal_for_dsp)
    async def generate_response(self) -> None:
        """Generate human-readable response with activation instructions."""
        if self.state.request_type == "deal_request" and self.state.deal_output:
            deal = self.state.deal_output

            self.state.response_text = f"""
**Deal Created Successfully**

**Deal ID**: `{deal.deal_id}`
**Deal Type**: {deal.deal_type.value.replace("_", " ").title()}
**Price**: ${deal.price:.2f} CPM
**Inventory**: {deal.product_id.replace("_", " ").title()}

**Activation Instructions**:

1. **The Trade Desk**:
   - Go to Inventory > Private Marketplace
   - Enter Deal ID: `{deal.deal_id}`

2. **Amazon DSP**:
   - Navigate to Supply > Deals
   - Add Deal ID: `{deal.deal_id}`

3. **DV360**:
   - Go to Inventory > My Inventory > Deals
   - Enter Deal ID: `{deal.deal_id}`

**Important**: The budget is controlled in your DSP. This deal provides access and pricing only.

Need help? Contact your account manager or reply to this message.
"""
        elif self.state.request_type == "inquiry":
            product_type = self.state.parsed_request.get("product_type", "display")
            pricing = self.state.pricing_decisions.get(product_type)

            if pricing:
                self.state.response_text = f"""
**Pricing Information**

**Product**: {product_type.replace("_", " ").title()} Inventory
**Your Rate**: ${pricing.final_price:.2f} CPM
**Pricing Tier**: {pricing.buyer_tier.title()}

{pricing.rationale}

Ready to create a deal? Just let me know and I'll generate a Deal ID for you.
"""
            else:
                self.state.response_text = "Please specify what inventory you're interested in."

        self.state.completed_at = datetime.utcnow()
        if self.state.status != ExecutionStatus.FAILED:
            self.state.status = ExecutionStatus.COMPLETED

    def process_request(
        self,
        request_text: str,
        buyer_context: Optional[BuyerContext] = None,
        seller_organization_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Process a request from a buyer (synchronous - for CLI use).

        Args:
            request_text: Natural language request
            buyer_context: Buyer identity context
            seller_organization_id: Seller organization ID

        Returns:
            Response with deal info or inquiry response
        """
        self.state.request_text = request_text
        self.state.buyer_context = buyer_context
        self.state.seller_organization_id = (
            seller_organization_id or self._settings.seller_organization_id or ""
        )

        # Run the flow synchronously
        self.kickoff()

        return {
            "request_type": self.state.request_type,
            "response": self.state.response_text,
            "deal": self.state.deal_output.model_dump() if self.state.deal_output else None,
            "status": self.state.status.value,
            "errors": self.state.errors,
        }

    async def process_request_async(
        self,
        request_text: str,
        buyer_context: Optional[BuyerContext] = None,
        seller_organization_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Process a request from a buyer (async - for API use).

        Args:
            request_text: Natural language request
            buyer_context: Buyer identity context
            seller_organization_id: Seller organization ID

        Returns:
            Response with deal info or inquiry response
        """
        self.state.request_text = request_text
        self.state.buyer_context = buyer_context
        self.state.seller_organization_id = (
            seller_organization_id or self._settings.seller_organization_id or ""
        )

        # Run the flow asynchronously
        await self.kickoff_async()

        return {
            "request_type": self.state.request_type,
            "response": self.state.response_text,
            "deal": self.state.deal_output.model_dump() if self.state.deal_output else None,
            "status": self.state.status.value,
            "errors": self.state.errors,
        }


# Backward-compatibility aliases
NonAgenticState = DealRequestState
NonAgenticDSPFlow = DealRequestFlow
