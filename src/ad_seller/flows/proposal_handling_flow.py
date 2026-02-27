# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Proposal Handling Flow - Process incoming buyer proposals.

This flow handles:
- Receiving proposals from buyer agents
- Validating against product availability
- Validating audience targeting via UCP
- Evaluating pricing and terms
- Counter/accept/reject with revision tracking
- Triggering upsell opportunities
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from crewai.flow.flow import Flow, start, listen, or_

from ..models.flow_state import (
    ExecutionStatus,
    ProposalEvaluation,
    SellerFlowState,
)
from ..models.buyer_identity import BuyerContext
from ..models.ucp import AudienceCapability, SignalType
from ..clients.ucp_client import UCPClient
from ..crews import create_proposal_review_crew
from ..config import get_settings
from ..events.helpers import emit_event
from ..events.models import EventType


class ProposalState(SellerFlowState):
    """State for proposal handling flow."""

    # Incoming proposal
    proposal_id: str = ""
    proposal_data: dict[str, Any] = {}
    buyer_context: Optional[BuyerContext] = None

    # Evaluation results
    evaluation: Optional[ProposalEvaluation] = None
    recommendation: str = ""  # accept, counter, reject

    # Counter proposal
    counter_terms: Optional[dict[str, Any]] = None

    # Upsell opportunities
    upsell_suggestions: list[dict[str, Any]] = []


class ProposalHandlingFlow(Flow[ProposalState]):
    """Flow for handling incoming buyer proposals.

    Steps:
    1. Receive and validate proposal
    2. Check product compatibility
    3. Evaluate pricing
    4. Check availability
    5. Generate recommendation (accept/counter/reject)
    6. Identify upsell opportunities
    7. Execute decision
    """

    def __init__(self) -> None:
        """Initialize the proposal handling flow."""
        super().__init__()
        self._settings = get_settings()
        self._audience_validation: dict = {}  # Populated by validate_audience step

    @start()
    async def receive_proposal(self) -> None:
        """Receive and validate the incoming proposal."""
        self.state.flow_id = str(uuid.uuid4())
        self.state.flow_type = "proposal_handling"
        self.state.started_at = datetime.utcnow()
        self.state.status = ExecutionStatus.PROPOSAL_RECEIVED

        # Validate required fields
        required_fields = ["product_id", "impressions", "start_date", "end_date"]
        missing = [f for f in required_fields if f not in self.state.proposal_data]

        if missing:
            self.state.errors.append(f"Missing required fields: {missing}")
            self.state.status = ExecutionStatus.FAILED

    @listen(receive_proposal)
    async def validate_product(self) -> None:
        """Validate that requested product exists and is compatible."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        self.state.status = ExecutionStatus.EVALUATING

        product_id = self.state.proposal_data.get("product_id")
        product = self.state.products.get(product_id)

        if not product:
            self.state.errors.append(f"Product not found: {product_id}")
            self.state.status = ExecutionStatus.FAILED
            return

        # Check deal type compatibility
        requested_deal_type = self.state.proposal_data.get("deal_type", "preferred_deal")
        if requested_deal_type not in [dt.value for dt in product.supported_deal_types]:
            self.state.warnings.append(
                f"Requested deal type {requested_deal_type} not supported for product"
            )

    @listen(validate_product)
    async def validate_audience(self) -> None:
        """Validate buyer's audience targeting via UCP.

        This step validates whether the proposal's audience targeting can
        be fulfilled by the product's audience capabilities.
        """
        if self.state.status == ExecutionStatus.FAILED:
            return

        product_id = self.state.proposal_data.get("product_id")
        product = self.state.products.get(product_id)
        audience_targeting = self.state.proposal_data.get("audience_targeting", {})

        if not audience_targeting:
            # No audience targeting in proposal - skip validation
            return

        if not product:
            return

        try:
            # Get or create product capabilities
            capabilities = self._get_product_capabilities(product_id, product)

            # Create UCP client for validation
            ucp_client = UCPClient()

            # Create product embedding from characteristics
            product_characteristics = {
                "product_id": product_id,
                "inventory_type": product.inventory_type,
                "audience_targeting": product.audience_targeting,
                "content_targeting": product.content_targeting,
            }
            product_embedding = ucp_client.create_inventory_embedding(
                product_characteristics
            )

            # Create buyer query embedding
            buyer_embedding = ucp_client.create_embedding(
                vector=ucp_client._generate_synthetic_embedding(
                    audience_targeting, 512
                ),
                embedding_type=__import__(
                    "ad_seller.models.ucp", fromlist=["EmbeddingType"]
                ).EmbeddingType.QUERY,
                signal_type=SignalType.CONTEXTUAL,
            )

            # Validate
            validation = ucp_client.validate_buyer_audience(
                buyer_embedding=buyer_embedding,
                product_embedding=product_embedding,
                capabilities=capabilities,
                audience_requirements=audience_targeting,
            )

            # Store validation results (to be used when initializing evaluation)
            self._audience_validation = {
                "validated": True,
                "coverage": validation.overall_coverage_percentage,
                "gaps": validation.gaps,
                "similarity_score": validation.ucp_similarity_score,
                "targeting_compatible": validation.targeting_compatible,
            }

            if not validation.targeting_compatible:
                self.state.warnings.append(
                    f"Audience coverage below threshold: {validation.overall_coverage_percentage:.1f}%"
                )

        except Exception as e:
            self.state.warnings.append(f"Audience validation warning: {e}")
            self._audience_validation = {
                "validated": False,
                "coverage": 0.0,
                "gaps": ["validation_error"],
                "similarity_score": None,
                "targeting_compatible": True,  # Fallback to allow
            }

    def _get_product_capabilities(
        self,
        product_id: str,
        product: Any,
    ) -> list[AudienceCapability]:
        """Get audience capabilities for a product."""
        # If product has pre-defined capabilities, use them
        if hasattr(product, "audience_capabilities") and product.audience_capabilities:
            # Would load from capability store
            pass

        # Default capabilities based on inventory type
        capabilities = [
            AudienceCapability(
                capability_id=f"{product_id}_ctx",
                name="Contextual Targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=95.0,
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id=f"{product_id}_geo",
                name="Geographic Targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=98.0,
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id=f"{product_id}_demo",
                name="Demographic Targeting",
                signal_type=SignalType.IDENTITY,
                coverage_percentage=70.0,
                ucp_compatible=True,
                embedding_dimension=512,
            ),
        ]

        return capabilities

    @listen(validate_audience)
    async def evaluate_pricing(self) -> None:
        """Evaluate the proposed pricing against our rules."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        product_id = self.state.proposal_data.get("product_id")
        product = self.state.products.get(product_id)
        requested_price = self.state.proposal_data.get("price", 0)

        if not product:
            return

        # Check against floor
        price_acceptable = requested_price >= product.floor_cpm

        # Get audience validation results (from validate_audience step)
        audience_validation = getattr(self, "_audience_validation", {})

        # Initialize evaluation with audience fields
        self.state.evaluation = ProposalEvaluation(
            proposal_id=self.state.proposal_id,
            proposal_line_id=self.state.proposal_data.get("line_id", ""),
            product_id=product_id,
            requested_price=requested_price,
            minimum_acceptable_price=product.floor_cpm,
            recommended_price=product.base_cpm,
            price_acceptable=price_acceptable,
            requested_impressions=self.state.proposal_data.get("impressions", 0),
            available_impressions=1000000,  # Placeholder - would come from avails
            impressions_available=True,  # Simplified
            # Audience validation fields
            audience_validated=audience_validation.get("validated", False),
            audience_coverage=audience_validation.get("coverage", 0.0),
            audience_gaps=audience_validation.get("gaps", []),
            ucp_similarity_score=audience_validation.get("similarity_score"),
            targeting_compatible=audience_validation.get("targeting_compatible", True),
        )

    @listen(evaluate_pricing)
    async def check_availability(self) -> None:
        """Check inventory availability for the requested flight."""
        if self.state.status == ExecutionStatus.FAILED or not self.state.evaluation:
            return

        # Simplified availability check
        # In production, this would query the ad server or avails system
        requested = self.state.evaluation.requested_impressions
        available = self.state.evaluation.available_impressions

        self.state.evaluation.impressions_available = requested <= available

        if not self.state.evaluation.impressions_available:
            self.state.evaluation.validation_errors.append(
                f"Requested {requested:,} impressions but only {available:,} available"
            )

    @listen(check_availability)
    async def run_crew_evaluation(self) -> None:
        """Run the proposal review crew for detailed evaluation."""
        if self.state.status == ExecutionStatus.FAILED:
            return

        # Create and run the proposal review crew
        crew = create_proposal_review_crew(self.state.proposal_data)

        try:
            result = crew.kickoff()

            # Parse crew recommendation
            result_text = str(result).lower()

            if "accept" in result_text:
                self.state.recommendation = "accept"
            elif "counter" in result_text:
                self.state.recommendation = "counter"
            else:
                self.state.recommendation = "reject"

            # Emit proposal.evaluated event
            await emit_event(
                event_type=EventType.PROPOSAL_EVALUATED,
                flow_id=self.state.flow_id,
                flow_type=self.state.flow_type,
                proposal_id=self.state.proposal_id,
                payload={
                    "recommendation": self.state.recommendation,
                    "evaluation": self.state.evaluation.model_dump() if self.state.evaluation else None,
                },
            )

        except Exception as e:
            self.state.warnings.append(f"Crew evaluation failed: {e}")
            # Fall back to rule-based evaluation
            self._fallback_evaluation()

    def _fallback_evaluation(self) -> None:
        """Fallback rule-based evaluation if crew fails."""
        if not self.state.evaluation:
            self.state.recommendation = "reject"
            return

        if (
            self.state.evaluation.price_acceptable
            and self.state.evaluation.impressions_available
            and self.state.evaluation.targeting_compatible
        ):
            self.state.recommendation = "accept"
        elif self.state.evaluation.impressions_available:
            self.state.recommendation = "counter"
        else:
            self.state.recommendation = "reject"

    @listen(run_crew_evaluation)
    async def generate_counter_terms(self) -> None:
        """Generate counter terms if recommending counter."""
        if self.state.recommendation != "counter":
            return

        if not self.state.evaluation:
            return

        product_id = self.state.proposal_data.get("product_id")
        product = self.state.products.get(product_id)

        if not product:
            return

        # Generate counter proposal
        self.state.counter_terms = {
            "proposed_price": product.base_cpm,
            "floor_price": product.floor_cpm,
            "max_impressions": self.state.evaluation.available_impressions,
            "reason": "Price below minimum acceptable threshold",
        }

        self.state.status = ExecutionStatus.COUNTER_PENDING

    @listen(run_crew_evaluation)
    async def identify_upsell(self) -> None:
        """Identify upsell opportunities."""
        if self.state.recommendation == "reject":
            # Even on reject, suggest alternatives
            self.state.upsell_suggestions.append({
                "type": "alternative_product",
                "message": "Consider our other inventory options",
            })
            return

        # Suggest volume upgrade
        if self.state.evaluation and self.state.evaluation.impressions_available:
            self.state.upsell_suggestions.append({
                "type": "volume_upgrade",
                "message": "Add 20% more impressions for a 10% volume discount",
            })

        # Suggest cross-sell
        self.state.upsell_suggestions.append({
            "type": "cross_sell",
            "message": "Extend your campaign to CTV for full-funnel coverage",
        })

    @listen(or_(generate_counter_terms, identify_upsell))
    async def execute_decision(self) -> None:
        """Execute the proposal decision, with optional approval gate."""
        settings = get_settings()

        # Check if approval gate is enabled for proposal decisions
        approval_enabled = (
            getattr(settings, "approval_gate_enabled", False)
            and "proposal_decision"
            in getattr(settings, "approval_required_flows", "").split(",")
        )

        if approval_enabled and self.state.recommendation in ("accept", "counter"):
            # Gate: mark as pending approval and return
            self.state.status = ExecutionStatus.PENDING_APPROVAL
            self.state.completed_at = datetime.utcnow()
            return

        # No gate — execute immediately (original behavior)
        self._finalize_decision()

    def _finalize_decision(self) -> None:
        """Apply the recommendation."""
        if self.state.recommendation == "accept":
            self.state.accepted_proposals.append(self.state.proposal_id)
            self.state.status = ExecutionStatus.ACCEPTED
        elif self.state.recommendation == "reject":
            self.state.rejected_proposals.append(self.state.proposal_id)
            self.state.status = ExecutionStatus.REJECTED
        # Counter status already set

        self.state.completed_at = datetime.utcnow()

    def handle_proposal(
        self,
        proposal_id: str,
        proposal_data: dict[str, Any],
        buyer_context: Optional[BuyerContext] = None,
        products: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Handle an incoming proposal.

        Args:
            proposal_id: Unique proposal identifier
            proposal_data: Proposal details
            buyer_context: Buyer identity context
            products: Product catalog

        Returns:
            Handling result with recommendation
        """
        self.state.proposal_id = proposal_id
        self.state.proposal_data = proposal_data
        self.state.buyer_context = buyer_context
        if products:
            self.state.products = products

        # Run the flow
        self.kickoff()

        result = {
            "proposal_id": proposal_id,
            "recommendation": self.state.recommendation,
            "status": self.state.status.value,
            "evaluation": self.state.evaluation.model_dump() if self.state.evaluation else None,
            "counter_terms": self.state.counter_terms,
            "upsell_suggestions": self.state.upsell_suggestions,
            "errors": self.state.errors,
            "warnings": self.state.warnings,
        }

        # If pending approval, include state snapshot for the API to create
        # an ApprovalRequest with (handle_proposal is sync, storage is async)
        if self.state.status == ExecutionStatus.PENDING_APPROVAL:
            result["pending_approval"] = True
            result["flow_id"] = self.state.flow_id
            result["_flow_state_snapshot"] = self.state.model_dump(mode="json")

        return result
