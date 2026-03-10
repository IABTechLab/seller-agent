# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Change Request Management models (seller-ju5).

Post-deal modification requests: flight date changes, impression adjustments,
price modifications, creative swaps. Each change request tracks what changed,
who requested it, and flows through a validation/approval pipeline.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChangeRequestStatus(str, Enum):
    """Lifecycle status of a change request."""

    PENDING = "pending"
    VALIDATING = "validating"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


class ChangeType(str, Enum):
    """Category of change being requested."""

    FLIGHT_DATES = "flight_dates"
    IMPRESSIONS = "impressions"
    PRICING = "pricing"
    CREATIVE = "creative"
    TARGETING = "targeting"
    CANCELLATION = "cancellation"
    OTHER = "other"


class ChangeSeverity(str, Enum):
    """How significant the change is — determines approval requirements."""

    MINOR = "minor"       # Auto-approvable (e.g. small date shift)
    MATERIAL = "material"  # Requires human approval (e.g. price change)
    CRITICAL = "critical"  # Requires senior approval (e.g. cancellation)


class FieldDiff(BaseModel):
    """A single field-level change."""

    field: str
    old_value: Any = None
    new_value: Any = None


class ChangeRequest(BaseModel):
    """A request to modify an existing order or deal post-booking."""

    change_request_id: str = Field(default_factory=lambda: f"CR-{uuid.uuid4().hex[:12].upper()}")
    order_id: str
    deal_id: str = ""
    status: ChangeRequestStatus = ChangeRequestStatus.PENDING
    change_type: ChangeType
    severity: ChangeSeverity = ChangeSeverity.MATERIAL
    requested_by: str = "system"
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    reason: str = ""

    # What changed
    diffs: list[FieldDiff] = Field(default_factory=list)
    proposed_values: dict[str, Any] = Field(default_factory=dict)

    # Validation results
    validation_errors: list[str] = Field(default_factory=list)
    pricing_impact: Optional[dict[str, Any]] = None
    availability_check: Optional[dict[str, Any]] = None

    # Approval
    approved_by: str = ""
    approved_at: Optional[datetime] = None
    rejection_reason: str = ""

    # Application
    applied_at: Optional[datetime] = None
    applied_by: str = ""
    rollback_snapshot: dict[str, Any] = Field(default_factory=dict)


# Severity classification rules
_SEVERITY_BY_CHANGE_TYPE: dict[ChangeType, ChangeSeverity] = {
    ChangeType.FLIGHT_DATES: ChangeSeverity.MATERIAL,
    ChangeType.IMPRESSIONS: ChangeSeverity.MATERIAL,
    ChangeType.PRICING: ChangeSeverity.CRITICAL,
    ChangeType.CREATIVE: ChangeSeverity.MINOR,
    ChangeType.TARGETING: ChangeSeverity.MATERIAL,
    ChangeType.CANCELLATION: ChangeSeverity.CRITICAL,
    ChangeType.OTHER: ChangeSeverity.MATERIAL,
}


def classify_severity(change_type: ChangeType, diffs: list[FieldDiff]) -> ChangeSeverity:
    """Determine severity based on change type and magnitude of diffs."""
    base = _SEVERITY_BY_CHANGE_TYPE.get(change_type, ChangeSeverity.MATERIAL)

    # Upgrade to critical if price change is >20%
    if change_type == ChangeType.PRICING:
        for diff in diffs:
            if diff.field in ("final_cpm", "base_cpm") and diff.old_value and diff.new_value:
                try:
                    pct = abs(float(diff.new_value) - float(diff.old_value)) / float(diff.old_value) * 100
                    if pct > 20:
                        return ChangeSeverity.CRITICAL
                except (ValueError, ZeroDivisionError):
                    pass

    # Downgrade flight_dates to minor if shift is ≤3 days
    if change_type == ChangeType.FLIGHT_DATES and base == ChangeSeverity.MATERIAL:
        for diff in diffs:
            if diff.field in ("flight_start", "flight_end") and diff.old_value and diff.new_value:
                try:
                    old = datetime.fromisoformat(str(diff.old_value))
                    new = datetime.fromisoformat(str(diff.new_value))
                    if abs((new - old).days) <= 3:
                        return ChangeSeverity.MINOR
                except (ValueError, TypeError):
                    pass

    return base


def validate_change_request(
    change_request: ChangeRequest,
    order: dict,
) -> list[str]:
    """Validate a change request against the current order state.

    Returns a list of validation error strings (empty = valid).
    """
    errors: list[str] = []

    # Can't modify completed/cancelled/failed orders
    status = order.get("status", "")
    terminal = {"completed", "cancelled", "failed"}
    if status in terminal:
        errors.append(f"Cannot modify order in '{status}' status.")

    # Cancellation only from active states
    if change_request.change_type == ChangeType.CANCELLATION:
        if status not in {"draft", "submitted", "pending_approval", "approved", "in_progress", "booked"}:
            errors.append(f"Cannot cancel order in '{status}' status.")

    # Impression changes must be positive
    if change_request.change_type == ChangeType.IMPRESSIONS:
        for diff in change_request.diffs:
            if diff.field == "impressions" and diff.new_value is not None:
                try:
                    if int(diff.new_value) <= 0:
                        errors.append("Impressions must be positive.")
                except (ValueError, TypeError):
                    errors.append("Impressions must be an integer.")

    return errors
