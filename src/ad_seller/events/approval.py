# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Approval gate for human-in-the-loop flow control.

Since CrewAI flows run to completion and cannot truly pause, the approval
pattern works as follows:

1. Flow reaches an approval point and the API calls ApprovalGate.request_approval().
2. This persists an ApprovalRequest to storage with status="pending".
3. The API returns "pending_approval" to the caller.
4. A human calls POST /approvals/{id}/decide to submit their decision.
5. The resume endpoint loads the flow state snapshot, applies the decision,
   and returns the final result — no expensive re-computation needed.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from .models import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalStatus,
    Event,
    EventType,
)

logger = logging.getLogger(__name__)


class ApprovalGate:
    """Manages approval requests and responses using the storage backend."""

    def __init__(self, storage_backend: Any) -> None:
        self._storage = storage_backend

    async def request_approval(
        self,
        flow_id: str,
        flow_type: str,
        gate_name: str,
        context: dict[str, Any],
        flow_state_snapshot: dict[str, Any],
        proposal_id: str = "",
        deal_id: str = "",
        timeout_hours: int = 24,
    ) -> ApprovalRequest:
        """Create a pending approval request.

        Args:
            flow_id: The ID of the flow requesting approval.
            flow_type: Type of the flow (proposal_handling, deal_generation, etc.).
            gate_name: Name of the approval gate (e.g. "proposal_decision").
            context: Human-readable context for the decision maker.
            flow_state_snapshot: Serialized flow state for resumption.
            proposal_id: Optional proposal ID.
            deal_id: Optional deal ID.
            timeout_hours: Hours until approval expires.

        Returns:
            The created ApprovalRequest.
        """
        # Publish the approval-requested event
        from .bus import get_event_bus
        bus = await get_event_bus()

        event = Event(
            event_type=EventType.APPROVAL_REQUESTED,
            flow_id=flow_id,
            flow_type=flow_type,
            proposal_id=proposal_id,
            deal_id=deal_id,
            payload=context,
        )
        await bus.publish(event)

        # Create the approval request
        request = ApprovalRequest(
            event_id=event.event_id,
            flow_id=flow_id,
            flow_type=flow_type,
            gate_name=gate_name,
            proposal_id=proposal_id,
            deal_id=deal_id,
            status=ApprovalStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(hours=timeout_hours),
            context=context,
            flow_state_snapshot=flow_state_snapshot,
        )

        # Persist
        await self._storage.set(
            f"approval:{request.approval_id}",
            request.model_dump(mode="json"),
        )

        # Add to pending index
        pending = await self._storage.get("approval_index:pending") or []
        pending.append(request.approval_id)
        await self._storage.set("approval_index:pending", pending)

        # Index by flow_id
        flow_key = f"approval_index:flow:{flow_id}"
        flow_approvals = await self._storage.get(flow_key) or []
        flow_approvals.append(request.approval_id)
        await self._storage.set(flow_key, flow_approvals)

        logger.info(
            "Approval requested: %s for flow %s gate=%s",
            request.approval_id,
            flow_id,
            gate_name,
        )

        return request

    async def submit_decision(
        self,
        approval_id: str,
        decision: str,
        decided_by: str = "unknown",
        reason: str = "",
        modifications: Optional[dict[str, Any]] = None,
    ) -> ApprovalResponse:
        """Submit a human decision for an approval request.

        Args:
            approval_id: The approval request ID.
            decision: "approve", "reject", or "counter".
            decided_by: Identifier of the human making the decision.
            reason: Optional reason for the decision.
            modifications: Optional modifications (e.g. counter-terms).

        Returns:
            The approval response.

        Raises:
            ValueError: If approval not found or already decided.
        """
        data = await self._storage.get(f"approval:{approval_id}")
        if not data:
            raise ValueError(f"Approval request not found: {approval_id}")

        request = ApprovalRequest(**data)

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Approval {approval_id} already resolved: {request.status}"
            )

        # Check expiration
        if request.expires_at and datetime.utcnow() > request.expires_at:
            request.status = ApprovalStatus.TIMED_OUT
            await self._storage.set(
                f"approval:{approval_id}",
                request.model_dump(mode="json"),
            )
            raise ValueError(f"Approval {approval_id} has expired")

        # Create response
        response = ApprovalResponse(
            approval_id=approval_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            modifications=modifications or {},
        )

        # Update request status
        if decision == "approve":
            request.status = ApprovalStatus.APPROVED
        else:
            request.status = ApprovalStatus.REJECTED

        # Persist
        await self._storage.set(
            f"approval:{approval_id}",
            request.model_dump(mode="json"),
        )
        await self._storage.set(
            f"approval_response:{approval_id}",
            response.model_dump(mode="json"),
        )

        # Remove from pending index
        pending = await self._storage.get("approval_index:pending") or []
        if approval_id in pending:
            pending.remove(approval_id)
            await self._storage.set("approval_index:pending", pending)

        # Publish decision event
        from .bus import get_event_bus
        bus = await get_event_bus()
        event_type = (
            EventType.APPROVAL_GRANTED
            if decision == "approve"
            else EventType.APPROVAL_DENIED
        )
        await bus.publish(
            Event(
                event_type=event_type,
                flow_id=request.flow_id,
                flow_type=request.flow_type,
                proposal_id=request.proposal_id,
                deal_id=request.deal_id,
                payload={
                    "approval_id": approval_id,
                    "decision": decision,
                    "decided_by": decided_by,
                    "reason": reason,
                },
            )
        )

        logger.info(
            "Approval %s decided: %s by %s",
            approval_id,
            decision,
            decided_by,
        )

        return response

    async def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Get an approval request by ID."""
        data = await self._storage.get(f"approval:{approval_id}")
        if data:
            return ApprovalRequest(**data)
        return None

    async def get_response(self, approval_id: str) -> Optional[ApprovalResponse]:
        """Get the response for an approval request."""
        data = await self._storage.get(f"approval_response:{approval_id}")
        if data:
            return ApprovalResponse(**data)
        return None

    async def list_pending(self) -> list[ApprovalRequest]:
        """List all pending approval requests."""
        ids = await self._storage.get("approval_index:pending") or []
        requests = []
        for aid in ids:
            req = await self.get_request(aid)
            if req and req.status == ApprovalStatus.PENDING:
                if req.expires_at and datetime.utcnow() > req.expires_at:
                    req.status = ApprovalStatus.TIMED_OUT
                    await self._storage.set(
                        f"approval:{req.approval_id}",
                        req.model_dump(mode="json"),
                    )
                    continue
                requests.append(req)
        return requests

    async def get_flow_approvals(self, flow_id: str) -> list[ApprovalRequest]:
        """Get all approval requests for a given flow."""
        ids = await self._storage.get(f"approval_index:flow:{flow_id}") or []
        requests = []
        for aid in ids:
            req = await self.get_request(aid)
            if req:
                requests.append(req)
        return requests
