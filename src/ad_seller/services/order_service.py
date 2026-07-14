# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Order lifecycle service.

Extracted from ``interfaces/api/main.py`` (EP-3.1): order creation and
state transitions (both routed through the formal
``models.order_state_machine.OrderStateMachine``), audit/reporting, and
change-request orchestration.

Behavior-preserving — error semantics are expressed as ``HTTPException``
exactly as the endpoints raised them, including the
409-with-``allowed_transitions`` contract on invalid transitions.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# =============================================================================
# Orders
# =============================================================================


async def create_order(
    deal_id: Optional[str] = None,
    quote_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    """Create a new order and persist its state machine."""
    from ..models.order_state_machine import OrderStateMachine
    from ..storage.factory import get_storage

    storage = await get_storage()

    order_id = f"ORD-{uuid.uuid4().hex[:12].upper()}"
    machine = OrderStateMachine(order_id=order_id)

    order_data = machine.to_dict()
    order_data["deal_id"] = deal_id
    order_data["quote_id"] = quote_id
    order_data["created_at"] = datetime.utcnow().isoformat() + "Z"
    order_data["metadata"] = metadata or {}

    await storage.set_order(order_id, order_data)

    return order_data


async def list_orders(status: Optional[str] = None) -> dict[str, Any]:
    """List orders, optionally filtered by status."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    filters = {}
    if status:
        filters["status"] = status
    orders = await storage.list_orders(filters if filters else None)
    return {"orders": orders, "count": len(orders)}


async def get_order(order_id: str) -> dict[str, Any]:
    """Get order current status and audit trail."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    order = await storage.get_order(order_id)

    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    return order


async def get_order_history(order_id: str) -> dict[str, Any]:
    """Get the full transition history for an order."""
    order = await get_order(order_id)

    audit_log = order.get("audit_log", {})
    transitions = audit_log.get("transitions", [])

    return {
        "order_id": order_id,
        "current_status": order.get("status"),
        "transitions": transitions,
        "transition_count": len(transitions),
    }


async def transition_order(
    order_id: str,
    to_status: str,
    actor: str = "system",
    reason: str = "",
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    """Transition an order to a new state via the formal state machine.

    Raises 400 for an unknown status, 404 for a missing order, and 409
    (with ``allowed_transitions``) when the state machine rejects the move.
    """
    from ..models.order_state_machine import (
        InvalidTransitionError,
        OrderStateMachine,
        OrderStatus,
    )
    from ..storage.factory import get_storage

    storage = await get_storage()
    order = await storage.get_order(order_id)

    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    # Validate target status
    try:
        to_status_enum = OrderStatus(to_status)
    except ValueError:
        valid = [s.value for s in OrderStatus]
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_status",
                "message": f"'{to_status}' is not a valid order status.",
                "valid_statuses": valid,
            },
        )

    # Restore state machine from stored data
    machine = OrderStateMachine.from_dict(order)

    try:
        record = machine.transition(
            to_status_enum,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )
    except InvalidTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "invalid_transition",
                "message": str(e),
                "current_status": machine.status.value,
                "allowed_transitions": [s.value for s in machine.allowed_transitions()],
            },
        )

    # Persist updated state
    updated = machine.to_dict()
    # Preserve extra fields not managed by the state machine
    for key in ("deal_id", "quote_id", "created_at", "metadata"):
        if key in order:
            updated[key] = order[key]

    await storage.set_order(order_id, updated)

    return {
        "order_id": order_id,
        "status": machine.status.value,
        "transition": record.model_dump(mode="json"),
        "allowed_next": [s.value for s in machine.allowed_transitions()],
    }


async def get_orders_report(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict[str, Any]:
    """Summary report across all orders."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    all_orders = await storage.list_orders()

    # Filter by date range if specified
    if from_date or to_date:
        filtered_orders = []
        for o in all_orders:
            created = o.get("created_at", "")
            if from_date and created < from_date:
                continue
            if to_date and created > to_date + "T23:59:59":
                continue
            filtered_orders.append(o)
        all_orders = filtered_orders

    # Counts by status
    status_counts: dict[str, int] = {}
    for o in all_orders:
        s = o.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Transition frequency by actor type
    actor_counts: dict[str, int] = {}
    total_transitions = 0
    for o in all_orders:
        transitions = o.get("audit_log", {}).get("transitions", [])
        total_transitions += len(transitions)
        for t in transitions:
            actor_type = t.get("actor", "system").split(":")[0]
            actor_counts[actor_type] = actor_counts.get(actor_type, 0) + 1

    # Average transitions per order
    order_count = len(all_orders)
    avg_transitions = round(total_transitions / order_count, 1) if order_count else 0

    # Change request summary
    all_crs = await storage.list_change_requests()
    cr_status_counts: dict[str, int] = {}
    for cr in all_crs:
        s = cr.get("status", "unknown")
        cr_status_counts[s] = cr_status_counts.get(s, 0) + 1

    return {
        "total_orders": order_count,
        "status_counts": status_counts,
        "total_transitions": total_transitions,
        "avg_transitions_per_order": avg_transitions,
        "actor_type_counts": actor_counts,
        "change_requests": {
            "total": len(all_crs),
            "by_status": cr_status_counts,
        },
    }


async def get_order_audit(
    order_id: str,
    actor: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict[str, Any]:
    """Detailed audit log for an order with optional filters."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    order = await storage.get_order(order_id)

    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    transitions = order.get("audit_log", {}).get("transitions", [])

    # Also include change requests for this order
    change_requests = await storage.list_change_requests({"order_id": order_id})

    # Filter transitions
    filtered = []
    for t in transitions:
        if actor and not t.get("actor", "").startswith(actor):
            continue
        ts = t.get("timestamp", "")
        if from_date and ts < from_date:
            continue
        if to_date and ts > to_date + "T23:59:59":
            continue
        filtered.append(t)

    return {
        "order_id": order_id,
        "current_status": order.get("status"),
        "created_at": order.get("created_at"),
        "transitions": filtered,
        "transition_count": len(filtered),
        "change_requests": change_requests,
        "change_request_count": len(change_requests),
    }


# =============================================================================
# Change requests
# =============================================================================


async def create_change_request(request: Any) -> dict[str, Any]:
    """Submit a change request for an existing order.

    Validates the change against the current order state, classifies
    severity, and routes to approval if needed.
    """
    from ..models.change_request import (
        ChangeRequest,
        ChangeRequestStatus,
        ChangeSeverity,
        ChangeType,
        FieldDiff,
        classify_severity,
        validate_change_request,
    )
    from ..storage.factory import get_storage

    storage = await get_storage()

    # Validate change_type
    try:
        change_type = ChangeType(request.change_type)
    except ValueError:
        valid = [t.value for t in ChangeType]
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_change_type",
                "message": f"'{request.change_type}' is not a valid change type.",
                "valid_types": valid,
            },
        )

    # Verify order exists
    order = await storage.get_order(request.order_id)
    if not order:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "order_not_found",
                "message": f"Order '{request.order_id}' not found.",
            },
        )

    # Build diffs
    diffs = [
        FieldDiff(field=d.field, old_value=d.old_value, new_value=d.new_value)
        for d in request.diffs
    ]

    # Classify severity
    severity = classify_severity(change_type, diffs)

    # Create the change request
    cr = ChangeRequest(
        order_id=request.order_id,
        deal_id=order.get("deal_id", ""),
        change_type=change_type,
        severity=severity,
        requested_by=request.requested_by,
        reason=request.reason,
        diffs=diffs,
        proposed_values=request.proposed_values or {},
        rollback_snapshot=order.copy(),
    )

    # Validate against order state
    errors = validate_change_request(cr, order)
    if errors:
        cr.status = ChangeRequestStatus.FAILED
        cr.validation_errors = errors
        cr_data = cr.model_dump(mode="json")
        await storage.set_change_request(cr.change_request_id, cr_data)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_failed",
                "change_request_id": cr.change_request_id,
                "validation_errors": errors,
            },
        )

    # Auto-approve minor changes, route material/critical to approval
    if severity == ChangeSeverity.MINOR:
        cr.status = ChangeRequestStatus.APPROVED
        cr.approved_by = "system:auto-approve"
        cr.approved_at = datetime.utcnow()
    else:
        cr.status = ChangeRequestStatus.PENDING_APPROVAL

    cr_data = cr.model_dump(mode="json")
    await storage.set_change_request(cr.change_request_id, cr_data)

    return cr_data


async def list_change_requests(
    order_id: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    """List change requests, optionally filtered by order or status."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    filters = {}
    if order_id:
        filters["order_id"] = order_id
    if status:
        filters["status"] = status
    results = await storage.list_change_requests(filters if filters else None)
    return {"change_requests": results, "count": len(results)}


async def get_change_request(cr_id: str) -> dict[str, Any]:
    """Get a change request by ID."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    cr = await storage.get_change_request(cr_id)
    if not cr:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "change_request_not_found",
                "message": f"Change request '{cr_id}' not found.",
            },
        )
    return cr


async def review_change_request(
    cr_id: str,
    decision: str,
    decided_by: str = "system",
    reason: str = "",
) -> dict[str, Any]:
    """Approve or reject a pending change request."""
    from ..models.change_request import ChangeRequestStatus
    from ..storage.factory import get_storage

    storage = await get_storage()
    cr = await storage.get_change_request(cr_id)

    if not cr:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "change_request_not_found",
                "message": f"Change request '{cr_id}' not found.",
            },
        )

    if cr.get("status") != ChangeRequestStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_pending_approval",
                "message": f"Change request is in '{cr.get('status')}' status, not 'pending_approval'.",
            },
        )

    if decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_decision",
                "message": "Decision must be 'approve' or 'reject'.",
            },
        )

    now = datetime.utcnow().isoformat() + "Z"

    if decision == "approve":
        cr["status"] = ChangeRequestStatus.APPROVED.value
        cr["approved_by"] = decided_by
        cr["approved_at"] = now
    else:
        cr["status"] = ChangeRequestStatus.REJECTED.value
        cr["rejection_reason"] = reason
        cr["approved_by"] = decided_by
        cr["approved_at"] = now

    await storage.set_change_request(cr_id, cr)
    return cr


async def apply_change_request(cr_id: str) -> dict[str, Any]:
    """Apply an approved change request to the order."""
    from ..models.change_request import ChangeRequestStatus
    from ..storage.factory import get_storage

    storage = await get_storage()
    cr = await storage.get_change_request(cr_id)

    if not cr:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "change_request_not_found",
                "message": f"Change request '{cr_id}' not found.",
            },
        )

    if cr.get("status") != ChangeRequestStatus.APPROVED.value:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_approved",
                "message": f"Change request is in '{cr.get('status')}' status, not 'approved'.",
            },
        )

    # Load the order
    order_id = cr.get("order_id")
    order = await storage.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": "order_not_found", "message": f"Order '{order_id}' not found."},
        )

    # Apply proposed values to order metadata
    proposed = cr.get("proposed_values", {})
    order_meta = order.get("metadata", {})
    order_meta.update(proposed)
    order["metadata"] = order_meta

    # Apply diffs directly to order where applicable
    for diff in cr.get("diffs", []):
        field = diff.get("field", "")
        new_val = diff.get("new_value")
        if field and new_val is not None:
            order_meta[f"_changed_{field}"] = new_val

    await storage.set_order(order_id, order)

    # Mark change request as applied
    cr["status"] = ChangeRequestStatus.APPLIED.value
    cr["applied_at"] = datetime.utcnow().isoformat() + "Z"
    cr["applied_by"] = "system"
    await storage.set_change_request(cr_id, cr)

    return {
        "change_request_id": cr_id,
        "status": "applied",
        "order_id": order_id,
    }
