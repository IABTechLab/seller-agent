# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Human-in-the-loop approval service.

Extracted from ``interfaces/api/main.py`` (EP-3.1): approval listing,
decision submission, and flow resumption after a decision.
"""

import logging
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

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

    # Reject a bad dsp_resolution decision while the approval is still pending, so it
    # can be corrected instead of surfacing as a 400 on resume.
    pending_request = await gate.get_request(approval_id)
    if (
        pending_request
        and pending_request.status.value == "pending"
        and decision == "approve"
        and pending_request.flow_type == "ssp_distribution"
        and pending_request.gate_name == "dsp_resolution"
    ):
        _build_dsp_resolution_create_request(pending_request, modifications or {})

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


async def _set_approval_status(request, status) -> None:
    from ..storage.factory import get_storage

    request.status = status
    storage = await get_storage()
    await storage.set(f"approval:{request.approval_id}", request.model_dump(mode="json"))


_DECISION_TIME_FIELDS = frozenset(
    {
        "name",
        "cpm",
        "account_id",
        "advertiser",
        "targeting",
        "start_date",
        "end_date",
        "impressions_goal",
    }
)


def _build_dsp_resolution_create_request(request, modifications: dict[str, Any]):
    """Validate a dsp_resolution decision's modifications and build the create request.

    Raises HTTPException(400) for a missing/unknown selected_dsp_id, unknown or
    disallowed keys, or values that fail SSPDealCreateRequest validation.
    """
    from ..clients.ssp_base import SSPDealCreateRequest

    selected_dsp_id = modifications.get("selected_dsp_id")
    if selected_dsp_id is None:
        raise HTTPException(
            status_code=400,
            detail='Approving a dsp_resolution gate requires modifications={"selected_dsp_id": <id>}',
        )

    unknown_keys = sorted(set(modifications) - _DECISION_TIME_FIELDS - {"selected_dsp_id"})
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported modifications {unknown_keys}; allowed: "
                f"{sorted(_DECISION_TIME_FIELDS | {'selected_dsp_id'})}"
            ),
        )

    candidate_dsp_ids = {str(c.get("dsp_id")) for c in request.context.get("candidates", [])}
    if str(selected_dsp_id) not in candidate_dsp_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"selected_dsp_id {selected_dsp_id} is not one of the candidate DSPs "
                f"for this approval: {sorted(candidate_dsp_ids)}"
            ),
        )

    supplied = {k: v for k, v in modifications.items() if k in _DECISION_TIME_FIELDS}
    try:
        return SSPDealCreateRequest.model_validate(
            {
                **request.flow_state_snapshot["create_request"],
                **supplied,
                "dsp_id": selected_dsp_id,
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid modifications: {exc.errors(include_url=False)}"
        ) from exc


async def _resume_ssp_distribution(request, response):
    """Resume an SSP distribution after a dsp_resolution approval decision.

    Unlike _resume_proposal_flow, there's no CrewAI flow/state machine to
    re-hydrate here — distribute_deal_via_ssp is a plain service call, so the
    snapshot is just the SSP name and the SSPDealCreateRequest that was
    waiting on a dsp_id. On approval, the human's selected_dsp_id
    (response.modifications) is applied and create_deal() finally runs.
    """
    from ..clients.ssp_base import SSPDealOutcomeUnknownError
    from ..clients.ssp_factory import build_ssp_registry
    from ..events.models import ApprovalStatus

    if request.status == ApprovalStatus.CONSUMED:
        raise HTTPException(
            status_code=409,
            detail=f"Approval {request.approval_id} has already been resumed",
        )

    if response.decision != "approve":
        return {
            "deal_id": request.deal_id,
            "status": response.decision,
            "resumed_from_approval": request.approval_id,
        }

    create_request = _build_dsp_resolution_create_request(request, response.modifications)

    registry = build_ssp_registry()
    ssp = registry.get_client(request.flow_state_snapshot["ssp_name"])

    def _success(result) -> dict[str, Any]:
        return {
            "deal_id": result.deal_id,
            "external_deal_id": result.external_deal_id,
            "ssp": result.ssp_name,
            "ssp_type": result.ssp_type.value,
            "status": result.status.value,
            "deal": result.model_dump(exclude={"raw"}),
            "resumed_from_approval": request.approval_id,
        }

    if request.status == ApprovalStatus.OUTCOME_UNKNOWN:
        # A previous create may or may not have landed. Only look the deal up here; never
        # POST, so this cannot create a second deal.
        try:
            async with ssp:
                existing = await ssp.find_deal_by_external_id(
                    create_request.external_deal_id, create_request.account_id
                )
        except SSPDealOutcomeUnknownError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if existing is not None:
            await _set_approval_status(request, ApprovalStatus.CONSUMED)
            return _success(existing)
        await _set_approval_status(request, ApprovalStatus.APPROVED)
        raise HTTPException(
            status_code=409,
            detail=(
                f"No deal with externalDealID {create_request.external_deal_id} exists in the "
                "SSP; the approval was restored to 'approved'. Resume again to create it."
            ),
        )

    # Mark the approval consumed before creating the deal so a repeated resume
    # cannot create it twice; restore it if creation fails so the call can be retried.
    # Storage has no compare-and-set, so two truly simultaneous resumes can still race.
    await _set_approval_status(request, ApprovalStatus.CONSUMED)
    try:
        async with ssp:
            result = await ssp.create_deal(create_request)
    except SSPDealOutcomeUnknownError as exc:
        # The deal may exist in the SSP; resuming again re-checks it instead of re-creating.
        await _set_approval_status(request, ApprovalStatus.OUTCOME_UNKNOWN)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        await _set_approval_status(request, ApprovalStatus.APPROVED)
        raise

    return _success(result)


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
