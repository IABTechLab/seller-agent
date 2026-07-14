# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Human-in-the-loop approval workflow endpoints."""

from fastapi import APIRouter

from ....services import approval_service
from ..schemas import ApprovalDecisionRequest

router = APIRouter()


@router.get("/approvals", tags=["Approvals"])
async def list_pending_approvals():
    """List all pending approval requests."""
    return await approval_service.list_pending_approvals()


@router.get("/approvals/{approval_id}", tags=["Approvals"])
async def get_approval(approval_id: str):
    """Get a specific approval request and its response (if any)."""
    return await approval_service.get_approval(approval_id)


@router.post("/approvals/{approval_id}/decide", tags=["Approvals"])
async def decide_approval(approval_id: str, body: ApprovalDecisionRequest):
    """Submit a human decision for a pending approval."""
    return await approval_service.decide_approval(
        approval_id=approval_id,
        decision=body.decision,
        decided_by=body.decided_by,
        reason=body.reason,
        modifications=body.modifications,
    )


@router.post("/approvals/{approval_id}/resume", tags=["Approvals"])
async def resume_flow(approval_id: str):
    """Resume a flow after an approval decision has been submitted.

    Loads the flow state snapshot, applies the decision, and returns
    the final result without re-running expensive crew evaluations.
    """
    return await approval_service.resume_flow(approval_id)
