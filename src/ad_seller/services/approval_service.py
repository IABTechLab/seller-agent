# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Human-in-the-loop approval service.

Extracted from ``interfaces/api/main.py`` (EP-3.1): approval listing,
decision submission, and flow resumption after a decision.
"""

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def list_pending_approvals() -> dict[str, Any]:
    """List all pending approval requests."""
    from ..events.approval import ApprovalGate
    from ..storage.factory import get_storage

    storage = await get_storage()
    gate = ApprovalGate(storage)
    pending = await gate.list_pending()
    return {"approvals": [r.model_dump(mode="json") for r in pending]}


async def get_approval(approval_id: str) -> dict[str, Any]:
    """Get a specific approval request and its response (if any)."""
    from ..events.approval import ApprovalGate
    from ..storage.factory import get_storage

    storage = await get_storage()
    gate = ApprovalGate(storage)
    request = await gate.get_request(approval_id)
    if not request:
        raise HTTPException(status_code=404, detail="Approval not found")
    response = await gate.get_response(approval_id)
    return {
        "request": request.model_dump(mode="json"),
        "response": response.model_dump(mode="json") if response else None,
    }


async def decide_approval(
    approval_id: str,
    decision: str,
    decided_by: str = "anonymous",
    reason: str = "",
    modifications: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit a human decision for a pending approval."""
    from ..events.approval import ApprovalGate
    from ..storage.factory import get_storage

    storage = await get_storage()
    gate = ApprovalGate(storage)
    try:
        response = await gate.submit_decision(
            approval_id=approval_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            modifications=modifications if modifications is not None else {},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return response.model_dump(mode="json")


async def resume_flow(approval_id: str) -> dict[str, Any]:
    """Resume a flow after an approval decision has been submitted.

    Loads the flow state snapshot, applies the decision, and returns
    the final result without re-running expensive crew evaluations.
    """
    from ..events.approval import ApprovalGate
    from ..storage.factory import get_storage

    storage = await get_storage()
    gate = ApprovalGate(storage)

    request = await gate.get_request(approval_id)
    if not request:
        raise HTTPException(status_code=404, detail="Approval not found")

    if request.status.value == "pending":
        raise HTTPException(
            status_code=400,
            detail="Approval has not been decided yet. Call /decide first.",
        )

    response = await gate.get_response(approval_id)
    if not response:
        raise HTTPException(status_code=400, detail="No decision found")

    # Route based on flow_type and gate_name
    if request.flow_type == "proposal_handling" and request.gate_name == "proposal_decision":
        return await _resume_proposal_flow(request, response)

    raise HTTPException(
        status_code=400,
        detail=f"Unknown flow_type/gate_name: {request.flow_type}/{request.gate_name}",
    )


async def _resume_proposal_flow(request, response):
    """Resume a proposal handling flow after approval decision."""
    from datetime import datetime

    from ..events.helpers import emit_event
    from ..events.models import EventType
    from ..flows.proposal_handling_flow import ProposalHandlingFlow, ProposalState
    from ..models.flow_state import ExecutionStatus

    snapshot = request.flow_state_snapshot

    # Re-hydrate state from snapshot
    flow = ProposalHandlingFlow()
    flow.state = ProposalState(**snapshot)

    # Apply the human decision
    if response.decision == "approve":
        flow.state.accepted_proposals.append(flow.state.proposal_id)
        flow.state.status = ExecutionStatus.ACCEPTED
    elif response.decision == "reject":
        flow.state.rejected_proposals.append(flow.state.proposal_id)
        flow.state.status = ExecutionStatus.REJECTED
    elif response.decision == "counter":
        if response.modifications:
            flow.state.counter_terms = response.modifications
        flow.state.status = ExecutionStatus.COUNTER_PENDING

    flow.state.completed_at = datetime.utcnow()

    # Emit event for the decision
    event_map = {
        "approve": EventType.PROPOSAL_ACCEPTED,
        "reject": EventType.PROPOSAL_REJECTED,
        "counter": EventType.PROPOSAL_COUNTERED,
    }
    await emit_event(
        event_type=event_map.get(response.decision, EventType.PROPOSAL_REJECTED),
        flow_id=flow.state.flow_id,
        flow_type="proposal_handling",
        proposal_id=flow.state.proposal_id,
        payload={
            "decision": response.decision,
            "decided_by": response.decided_by,
            "reason": response.reason,
        },
    )

    return {
        "proposal_id": flow.state.proposal_id,
        "status": flow.state.status.value,
        "recommendation": response.decision,
        "counter_terms": flow.state.counter_terms,
        "resumed_from_approval": request.approval_id,
    }
