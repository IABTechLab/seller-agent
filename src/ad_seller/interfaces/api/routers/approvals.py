# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Human-in-the-loop approval workflow endpoints.

These are privileged control-plane endpoints: submitting a decision here
finalizes (or rejects/counters) a gated deal. They therefore REQUIRE a
valid API-key credential (EP-4.5). The approver identity written to the
audit trail is the VERIFIED principal derived from that credential —
``body.decided_by`` is retained only as a free-text display label.
"""

from fastapi import APIRouter, Depends

from ....auth.dependencies import (
    principal_from_api_key,
    require_api_key_record,
)
from ....models.api_key import ApiKeyRecord
from ....services import approval_service
from ..schemas import ApprovalDecisionRequest

router = APIRouter()


@router.get("/approvals", tags=["Approvals"])
async def list_pending_approvals(
    _principal: ApiKeyRecord = Depends(require_api_key_record),
):
    """List all pending approval requests. Requires authentication."""
    return await approval_service.list_pending_approvals()


@router.get("/approvals/{approval_id}", tags=["Approvals"])
async def get_approval(
    approval_id: str,
    _principal: ApiKeyRecord = Depends(require_api_key_record),
):
    """Get a specific approval request and its response (if any).

    Requires authentication.
    """
    return await approval_service.get_approval(approval_id)


@router.post("/approvals/{approval_id}/decide", tags=["Approvals"])
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    principal: ApiKeyRecord = Depends(require_api_key_record),
):
    """Submit a human decision for a pending approval. Requires authentication.

    The verified principal from the authenticated API key is stamped into
    the audit record; ``body.decided_by`` is kept as a display label only.
    """
    return await approval_service.decide_approval(
        approval_id=approval_id,
        decision=body.decision,
        decided_by=body.decided_by,
        reason=body.reason,
        modifications=body.modifications,
        decided_by_principal=principal_from_api_key(principal),
    )


@router.post("/approvals/{approval_id}/resume", tags=["Approvals"])
async def resume_flow(
    approval_id: str,
    _principal: ApiKeyRecord = Depends(require_api_key_record),
):
    """Resume a flow after an approval decision has been submitted.

    Requires authentication. Loads the flow state snapshot, applies the
    decision, and returns the final result without re-running expensive
    crew evaluations.
    """
    return await approval_service.resume_flow(approval_id)
