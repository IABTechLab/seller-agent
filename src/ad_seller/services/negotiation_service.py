# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Proposal & negotiation service.

Extracted from ``interfaces/api/main.py`` (EP-3.1): proposal submission
(evaluation + approval-gate routing + pricing verification) and the
multi-round counter-offer logic (counter/accept/decline via the
NegotiationEngine).
"""

import logging
import uuid
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def build_negotiation_engine():
    """Construct the seller's ``NegotiationEngine`` with its standard wiring.

    Single source of the engine assembly (default tiered-pricing config +
    pricing-rules engine + yield optimizer). Both the REST negotiation path
    (``counter_proposal``) and the chat adapter build the engine here so the
    negotiation behavior stays identical across surfaces (EP-3.2).
    """
    from ..engines.negotiation_engine import NegotiationEngine
    from ..engines.pricing_rules_engine import PricingRulesEngine
    from ..engines.yield_optimizer import YieldOptimizer
    from ..models.pricing_tiers import TieredPricingConfig

    config = TieredPricingConfig(seller_organization_id="default")
    pricing_engine = PricingRulesEngine(config)
    yield_opt = YieldOptimizer()
    return NegotiationEngine(pricing_engine, yield_opt)


async def submit_proposal(request: Any, buyer_context: Any, catalog: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a submitted proposal.

    Product data now comes from the single cached catalog source (EP-3.3)
    instead of running ProductSetupFlow per request. Returns the response
    payload dict (status may be ``pending_approval`` with an approval_id).
    """
    from ..flows import ProposalHandlingFlow

    # Process proposal
    proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
    proposal_data = {
        "product_id": request.product_id,
        "deal_type": request.deal_type,
        "price": request.price,
        "impressions": request.impressions,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "buyer_id": request.buyer_id,
    }

    flow = ProposalHandlingFlow()
    result = flow.handle_proposal(
        proposal_id=proposal_id,
        proposal_data=proposal_data,
        buyer_context=buyer_context,
        products=catalog["products"],
    )

    # Verify pricing against quote history (Layer 4 — CPM hallucination defense)
    from ..storage.factory import get_storage
    from ..storage.quote_history import QuoteHistoryStore

    storage = await get_storage()
    quote_history = QuoteHistoryStore(storage)
    verification = await quote_history.verify_pricing(
        buyer_id=buyer_context.get_pricing_key(),
        product_id=request.product_id,
        proposed_cpm=request.price,
    )
    pricing_verified = verification.pricing_verified
    pricing_verification_reason = verification.reason

    # If pending approval, create the approval request
    if result.get("pending_approval"):
        from ..events.approval import ApprovalGate

        gate = ApprovalGate(storage)
        approval_req = await gate.request_approval(
            flow_id=result["flow_id"],
            flow_type="proposal_handling",
            gate_name="proposal_decision",
            context={
                "proposal_id": proposal_id,
                "recommendation": result["recommendation"],
                "evaluation": result.get("evaluation"),
                "counter_terms": result.get("counter_terms"),
                "pricing_verified": pricing_verified,
                "pricing_verification_reason": pricing_verification_reason,
            },
            flow_state_snapshot=result.get("_flow_state_snapshot", {}),
            proposal_id=proposal_id,
        )
        return {
            "proposal_id": proposal_id,
            "recommendation": result["recommendation"],
            "status": "pending_approval",
            "counter_terms": result.get("counter_terms"),
            "approval_id": approval_req.approval_id,
            "pricing_verified": pricing_verified,
            "pricing_verification_reason": pricing_verification_reason,
            "errors": result.get("errors", []),
        }

    return {
        "proposal_id": proposal_id,
        "recommendation": result["recommendation"],
        "status": result["status"],
        "counter_terms": result.get("counter_terms"),
        "approval_id": None,
        "pricing_verified": pricing_verified,
        "pricing_verification_reason": pricing_verification_reason,
        "errors": result.get("errors", []),
    }


async def counter_proposal(
    proposal_id: str,
    buyer_price: float,
    buyer_context: Any,
) -> dict[str, Any]:
    """Submit a counter-offer in an ongoing negotiation.

    Loads or creates a NegotiationHistory, evaluates the buyer's offer,
    persists the updated history, and emits a NEGOTIATION_ROUND event.
    """
    from ..events.helpers import emit_event
    from ..events.models import EventType
    from ..models.negotiation import NegotiationHistory
    from ..storage.factory import get_storage

    storage = await get_storage()
    neg_engine = build_negotiation_engine()

    # Load existing negotiation or start new one
    existing = await storage.get_negotiation(proposal_id)
    if existing:
        history = NegotiationHistory(**existing)
        if history.status != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Negotiation is {history.status}, cannot counter",
            )
    else:
        # Look up proposal to get product info
        proposal_data = await storage.get_proposal(proposal_id)
        if not proposal_data:
            raise HTTPException(status_code=404, detail="Proposal not found")

        product_id = proposal_data.get("product_id", "")
        product_data = await storage.get_product(product_id)
        if not product_data:
            raise HTTPException(status_code=404, detail="Product not found")

        history = neg_engine.start_negotiation(
            proposal_id=proposal_id,
            product_id=product_id,
            buyer_context=buyer_context,
            base_price=product_data.get("base_cpm", 0),
            floor_price=product_data.get("floor_cpm", 0),
        )

        await emit_event(
            event_type=EventType.NEGOTIATION_STARTED,
            proposal_id=proposal_id,
            payload={
                "negotiation_id": history.negotiation_id,
                "strategy": history.strategy.value,
                "base_price": history.base_price,
            },
        )

    # Evaluate buyer's offer
    round_result = neg_engine.evaluate_buyer_offer(history, buyer_price, buyer_context)
    history = neg_engine.record_round(history, round_result)

    # Persist
    await storage.set_negotiation(proposal_id, history.model_dump(mode="json"))

    # Emit round event
    await emit_event(
        event_type=EventType.NEGOTIATION_ROUND,
        proposal_id=proposal_id,
        payload={
            "negotiation_id": history.negotiation_id,
            "round_number": round_result.round_number,
            "action": round_result.action.value,
            "buyer_price": round_result.buyer_price,
            "seller_price": round_result.seller_price,
        },
    )

    # Emit concluded event if terminal
    if history.status in ("accepted", "rejected"):
        await emit_event(
            event_type=EventType.NEGOTIATION_CONCLUDED,
            proposal_id=proposal_id,
            payload={
                "negotiation_id": history.negotiation_id,
                "status": history.status,
                "total_rounds": len(history.rounds),
                "final_price": round_result.seller_price,
            },
        )

    return {
        "negotiation_id": history.negotiation_id,
        "round_number": round_result.round_number,
        "action": round_result.action.value,
        "buyer_price": round_result.buyer_price,
        "seller_price": round_result.seller_price,
        "concession_pct": round_result.concession_pct,
        "cumulative_concession_pct": round_result.cumulative_concession_pct,
        "rationale": round_result.rationale,
        "status": history.status,
        "rounds_remaining": history.limits.max_rounds - round_result.round_number,
    }


async def get_negotiation_status(proposal_id: str) -> dict[str, Any]:
    """Get full negotiation history for a proposal."""
    from ..models.negotiation import NegotiationHistory
    from ..storage.factory import get_storage

    storage = await get_storage()
    data = await storage.get_negotiation(proposal_id)
    if not data:
        raise HTTPException(status_code=404, detail="No negotiation found for this proposal")

    history = NegotiationHistory(**data)
    return {
        "negotiation_id": history.negotiation_id,
        "proposal_id": history.proposal_id,
        "product_id": history.product_id,
        "buyer_tier": history.buyer_tier.value,
        "strategy": history.strategy.value,
        "base_price": history.base_price,
        "floor_price": history.floor_price,
        "status": history.status,
        "total_rounds": len(history.rounds),
        "max_rounds": history.limits.max_rounds,
        "rounds": [r.model_dump(mode="json") for r in history.rounds],
        "started_at": history.started_at.isoformat(),
        "completed_at": history.completed_at.isoformat() if history.completed_at else None,
        "package_id": history.package_id,
    }
