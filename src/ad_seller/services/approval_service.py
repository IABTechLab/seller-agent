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
    decided_by_principal: str = "",
) -> dict[str, Any]:
    """Submit a human decision for a pending approval.

    ``decided_by`` is a free-text display label. ``decided_by_principal`` is
    the VERIFIED approver identity derived from the authenticated credential
    by the caller (the REST router); it is stamped into the audit record so
    the trail cannot be forged via the request body.
    """
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
            decided_by_principal=decided_by_principal,
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

    if request.flow_type == "ssp_distribution" and request.gate_name == "dsp_resolution":
        return await _resume_ssp_distribution(request, response)

    raise HTTPException(
        status_code=400,
        detail=f"Unknown flow_type/gate_name: {request.flow_type}/{request.gate_name}",
    )


async def _resume_ssp_distribution(request, response):
    """Resume an SSP distribution after a dsp_resolution approval decision.

    Unlike _resume_proposal_flow, there's no CrewAI flow/state machine to
    re-hydrate here — distribute_deal_via_ssp is a plain service call, so the
    snapshot is just the SSP name and the SSPDealCreateRequest that was
    waiting on a dsp_id. On approval, the human's selected_dsp_id
    (response.modifications) is applied and create_deal() finally runs.
    """
    from ..clients.ssp_base import SSPDealCreateRequest
    from ..clients.ssp_factory import build_ssp_registry

    if response.decision != "approve":
        return {
            "deal_id": request.deal_id,
            "status": response.decision,
            "resumed_from_approval": request.approval_id,
        }

    selected_dsp_id = response.modifications.get("selected_dsp_id")
    if selected_dsp_id is None:
        raise HTTPException(
            status_code=400,
            detail='Approving a dsp_resolution gate requires modifications={"selected_dsp_id": <id>}',
        )

    snapshot = request.flow_state_snapshot
    create_request = SSPDealCreateRequest(**snapshot["create_request"])
    create_request.dsp_id = selected_dsp_id

    # Any other create_request field the human supplies at decision time
    # (e.g. account_id, name, targeting) — fields the SSP needs that
    # distribute_deal_via_ssp never had a value for when the gate fired.
    for field, value in response.modifications.items():
        if field != "selected_dsp_id" and hasattr(create_request, field):
            setattr(create_request, field, value)

    registry = build_ssp_registry()
    ssp = registry.get_client(snapshot["ssp_name"])

    async with ssp:
        result = await ssp.create_deal(create_request)

    return {
        "deal_id": result.deal_id,
        "ssp": result.ssp_name,
        "ssp_type": result.ssp_type.value,
        "status": result.status.value,
        "deal": result.model_dump(exclude={"raw"}),
        "resumed_from_approval": request.approval_id,
    }


async def _resume_proposal_flow(request, response):
    """Resume a proposal handling flow after approval decision."""
    from datetime import datetime

    from ..events.helpers import emit_event
    from ..events.models import EventType
    from ..flows.proposal_handling_flow import ProposalHandlingFlow, ProposalState
    from ..models.flow_state import ExecutionStatus

    snapshot = request.flow_state_snapshot

    # Re-hydrate state from snapshot. CrewAI 1.15 made ``Flow.state`` a
    # read-only property; assigning it raises AttributeError (HTTP 500).
    # ``_state`` is the PrivateAttr the property reads.
    flow = ProposalHandlingFlow()
    flow._state = ProposalState(**(snapshot or {}))

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
