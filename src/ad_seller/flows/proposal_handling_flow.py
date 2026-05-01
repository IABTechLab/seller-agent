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

from crewai.flow.flow import Flow, listen, or_, start

from ..clients.ucp_client import UCPClient
from ..config import get_settings
from ..crews import create_proposal_review_crew
from ..events.helpers import emit_event
from ..events.models import EventType
from ..models.buyer_identity import BuyerContext
from ..models.flow_state import (
    ExecutionStatus,
    ProposalEvaluation,
    SellerFlowState,
)
from ..models.ucp import AudienceCapability, SignalType


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
        # Optional package list (Package objects or dicts) used by
        # _aggregate_seller_segments() for hard-reject overlap checks.
        # Tests / upstream code inject via attribute; default empty.
        self._packages_for_audience_validation: dict | list = {}

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

        Per proposal §5.7 layer 3 (bead ar-sn8f): when the proposal carries a
        structured `audience_plan`, the static-taxonomy paths (standard /
        contextual) are HARD-REJECTED on zero overlap with the seller's
        aggregated segment IDs. Agentic match scores remain a SOFT WARN
        because the score is opinion (mock-quality in Epic 1).
        """
        if self.state.status == ExecutionStatus.FAILED:
            return

        product_id = self.state.proposal_data.get("product_id")
        product = self.state.products.get(product_id)

        # ---- Hard-reject pass: structured audience_plan vs. seller segments
        # Runs whether or not legacy `audience_targeting` is also present.
        audience_plan = self.state.proposal_data.get("audience_plan")
        if audience_plan:
            hard_reject_reason = self._check_audience_plan_hard_rejects(
                audience_plan
            )
            if hard_reject_reason:
                self.state.errors.append(hard_reject_reason)
                self.state.status = ExecutionStatus.FAILED
                self._audience_validation = {
                    "validated": False,
                    "coverage": 0.0,
                    "gaps": ["audience_plan_no_overlap"],
                    "similarity_score": None,
                    "targeting_compatible": False,
                }
                return

        audience_targeting = self.state.proposal_data.get("audience_targeting", {})

        if not audience_targeting:
            # No audience targeting in proposal - skip soft-warn validation.
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
            product_embedding = ucp_client.create_inventory_embedding(product_characteristics)

            # Create buyer query embedding
            buyer_embedding = ucp_client.create_embedding(
                vector=ucp_client._generate_synthetic_embedding(audience_targeting, 512),
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

    def _aggregate_seller_segments(self) -> tuple[set[str], set[str]]:
        """Aggregate the seller's standard + contextual segment IDs across packages.

        Walks `self._packages_for_audience_validation` (instance attribute,
        injected by tests / upstream callers) and pulls each package's
        `audience_capabilities.standard_segment_ids` and
        `contextual_segment_ids`. Falls back to an empty set when no packages
        are wired in -- callers treat empty as 'seller has nothing in this
        dimension' and defer to the existing soft-warn UCP path.

        Per proposal §5.7 layer 3 (bead ar-sn8f).
        """

        std: set[str] = set()
        ctx: set[str] = set()
        packages = getattr(self, "_packages_for_audience_validation", None) or {}
        for pkg in packages.values() if isinstance(packages, dict) else packages:
            caps = getattr(pkg, "audience_capabilities", None)
            if caps is None and isinstance(pkg, dict):
                caps = pkg.get("audience_capabilities")
            if caps is None:
                continue
            std_ids = (
                getattr(caps, "standard_segment_ids", None)
                if not isinstance(caps, dict)
                else caps.get("standard_segment_ids", [])
            )
            ctx_ids = (
                getattr(caps, "contextual_segment_ids", None)
                if not isinstance(caps, dict)
                else caps.get("contextual_segment_ids", [])
            )
            if std_ids:
                std.update(std_ids)
            if ctx_ids:
                ctx.update(ctx_ids)
        return std, ctx

    def _check_audience_plan_hard_rejects(
        self, audience_plan: dict
    ) -> Optional[str]:
        """Hard-reject when buyer's standard/contextual refs have zero overlap.

        Returns a human-readable rejection reason when zero overlap exists on
        either dimension; returns None when the plan is acceptable (or when
        the seller has no packages registered, which falls back to the
        existing soft-warn UCP path).

        Per proposal §5.7 layer 3 (bead ar-sn8f). Agentic refs are NOT
        checked here -- low agentic match scores remain soft warnings since
        the score is opinion (mock-quality in Epic 1).
        """

        std_seller, ctx_seller = self._aggregate_seller_segments()

        # If seller has nothing registered in either dimension we can't
        # meaningfully hard-reject -- defer to the soft-warn path.
        if not std_seller and not ctx_seller:
            return None

        def _collect(role_refs: list, want_type: str) -> set[str]:
            ids: set[str] = set()
            for ref in role_refs or []:
                if isinstance(ref, dict) and ref.get("type") == want_type:
                    ident = ref.get("identifier")
                    if ident:
                        ids.add(ident)
            return ids

        # Walk all roles for standard / contextual refs the buyer asked for.
        all_refs: list = []
        primary = audience_plan.get("primary")
        if isinstance(primary, dict):
            all_refs.append(primary)
        for role in ("constraints", "extensions", "exclusions"):
            extra = audience_plan.get(role) or []
            if isinstance(extra, list):
                all_refs.extend(extra)

        std_buyer = _collect(all_refs, "standard")
        ctx_buyer = _collect(all_refs, "contextual")

        if std_buyer and not (std_buyer & std_seller):
            return (
                "audience_plan rejected: zero overlap between buyer's standard "
                f"refs {sorted(std_buyer)} and seller's standard segments "
                f"{sorted(std_seller)} (proposal §5.7 layer 3)"
            )

        if ctx_buyer and not (ctx_buyer & ctx_seller):
            return (
                "audience_plan rejected: zero overlap between buyer's contextual "
                f"refs {sorted(ctx_buyer)} and seller's contextual segments "
                f"{sorted(ctx_seller)} (proposal §5.7 layer 3)"
            )

        return None

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
                    "evaluation": self.state.evaluation.model_dump()
                    if self.state.evaluation
                    else None,
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
        """Generate counter terms using NegotiationEngine."""
        if self.state.recommendation != "counter":
            return

        if not self.state.evaluation:
            return

        product_id = self.state.proposal_data.get("product_id")
        product = self.state.products.get(product_id)

        if not product:
            return

        # Use NegotiationEngine for strategy-aware counter
        from ..engines.negotiation_engine import NegotiationEngine
        from ..engines.pricing_rules_engine import PricingRulesEngine
        from ..engines.yield_optimizer import YieldOptimizer
        from ..models.pricing_tiers import TieredPricingConfig

        pricing_config = TieredPricingConfig(seller_organization_id="default")
        pricing_engine = PricingRulesEngine(pricing_config)
        yield_opt = YieldOptimizer()
        neg_engine = NegotiationEngine(pricing_engine, yield_opt)

        history = neg_engine.start_negotiation(
            proposal_id=self.state.proposal_id,
            product_id=product.product_id,
            buyer_context=self.state.buyer_context,
            base_price=product.base_cpm,
            floor_price=product.floor_cpm,
        )

        buyer_price = self.state.proposal_data.get("price", 0)
        round_result = neg_engine.evaluate_buyer_offer(
            history, buyer_price, self.state.buyer_context
        )
        history = neg_engine.record_round(history, round_result)

        self.state.counter_terms = {
            "proposed_price": round_result.seller_price,
            "floor_price": product.floor_cpm,
            "max_impressions": self.state.evaluation.available_impressions,
            "reason": round_result.rationale,
            "negotiation_id": history.negotiation_id,
            "round_number": round_result.round_number,
            "action": round_result.action.value,
        }

        self.state.status = ExecutionStatus.COUNTER_PENDING

    @listen(run_crew_evaluation)
    async def identify_upsell(self) -> None:
        """Identify upsell opportunities."""
        if self.state.recommendation == "reject":
            # Even on reject, suggest alternatives
            self.state.upsell_suggestions.append(
                {
                    "type": "alternative_product",
                    "message": "Consider our other inventory options",
                }
            )
            return

        # Suggest volume upgrade
        if self.state.evaluation and self.state.evaluation.impressions_available:
            self.state.upsell_suggestions.append(
                {
                    "type": "volume_upgrade",
                    "message": "Add 20% more impressions for a 10% volume discount",
                }
            )

        # Suggest cross-sell
        self.state.upsell_suggestions.append(
            {
                "type": "cross_sell",
                "message": "Extend your campaign to CTV for full-funnel coverage",
            }
        )

    @listen(or_(generate_counter_terms, identify_upsell))
    async def execute_decision(self) -> None:
        """Execute the proposal decision, with optional approval gate."""
        settings = get_settings()

        # Check if approval gate is enabled for proposal decisions
        approval_enabled = getattr(
            settings, "approval_gate_enabled", False
        ) and "proposal_decision" in getattr(settings, "approval_required_flows", "").split(",")

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

    async def handle_proposal_async(
        self,
        proposal_id: str,
        proposal_data: dict[str, Any],
        buyer_context: Optional[BuyerContext] = None,
        products: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Async version of handle_proposal using kickoff_async().

        Use this from async FastAPI handlers to avoid blocking the event loop
        with the synchronous kickoff() call.
        """
        self.state.proposal_id = proposal_id
        self.state.proposal_data = proposal_data
        self.state.buyer_context = buyer_context
        if products:
            self.state.products = products

        await self.kickoff_async()

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

        if self.state.status == ExecutionStatus.PENDING_APPROVAL:
            result["pending_approval"] = True
            result["flow_id"] = self.state.flow_id
            result["_flow_state_snapshot"] = self.state.model_dump(mode="json")

        return result
