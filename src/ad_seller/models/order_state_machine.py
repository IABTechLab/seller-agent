# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Order Workflow State Machine (seller-awh).

Replaces the linear ExecutionStatus enum with a formal state machine that
supports configurable transitions, guard conditions, and a full audit log
of every state change.

The unified OrderStatus enum consolidates:
  - ExecutionStatus  (flow_state.py)  — workflow-level states
  - ExecutionOrderStatus  (core.py)   — ad-server order states

Existing code continues to work: ExecutionStatus and ExecutionOrderStatus
are preserved and mapped into OrderStatus where flows need the new machine.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Unified Order Status
# ---------------------------------------------------------------------------


class OrderStatus(str, Enum):
    """Unified status for seller order lifecycle.

    Merges ExecutionStatus (workflow) and ExecutionOrderStatus (ad-server)
    into a single, formal state set with well-defined transitions.
    """

    # Entry states
    DRAFT = "draft"
    SUBMITTED = "submitted"

    # Approval gate
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"

    # Execution
    IN_PROGRESS = "in_progress"
    SYNCING = "syncing"

    # Terminal states
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    # Ad-server specific
    BOOKED = "booked"
    UNBOOKED = "unbooked"


# ---------------------------------------------------------------------------
# Audit models
# ---------------------------------------------------------------------------


class StateTransition(BaseModel):
    """Immutable record of a single state change."""

    transition_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    from_status: OrderStatus
    to_status: OrderStatus
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor: str = "system"  # "system", "human:<user_id>", "agent:<agent_id>"
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderAuditLog(BaseModel):
    """Append-only audit trail for an order's lifecycle."""

    order_id: str
    transitions: list[StateTransition] = Field(default_factory=list)

    @property
    def current_status(self) -> Optional[OrderStatus]:
        if self.transitions:
            return self.transitions[-1].to_status
        return None

    def append(self, transition: StateTransition) -> None:
        self.transitions.append(transition)


# ---------------------------------------------------------------------------
# Transition rules
# ---------------------------------------------------------------------------

# Type alias for guard functions: (order_id, from_status, to_status, context) -> bool
GuardFn = Callable[[str, OrderStatus, OrderStatus, dict[str, Any]], bool]


class TransitionRule(BaseModel):
    """Defines a permitted state transition with optional guard condition."""

    model_config = {"arbitrary_types_allowed": True}

    from_status: OrderStatus
    to_status: OrderStatus
    guard: Optional[GuardFn] = Field(default=None, exclude=True)
    description: str = ""


# Default transition table — the canonical set of allowed moves.
_DEFAULT_TRANSITIONS: list[tuple[OrderStatus, OrderStatus, str]] = [
    # Happy path
    (OrderStatus.DRAFT, OrderStatus.SUBMITTED, "Order submitted for review"),
    (OrderStatus.SUBMITTED, OrderStatus.PENDING_APPROVAL, "Awaiting human approval"),
    (OrderStatus.SUBMITTED, OrderStatus.APPROVED, "Auto-approved (no gate)"),
    (OrderStatus.PENDING_APPROVAL, OrderStatus.APPROVED, "Human approved"),
    (OrderStatus.PENDING_APPROVAL, OrderStatus.REJECTED, "Human rejected"),
    (OrderStatus.APPROVED, OrderStatus.IN_PROGRESS, "Execution started"),
    (OrderStatus.IN_PROGRESS, OrderStatus.SYNCING, "Syncing to ad server"),
    (OrderStatus.SYNCING, OrderStatus.BOOKED, "Ad server confirmed booking"),
    (OrderStatus.BOOKED, OrderStatus.COMPLETED, "Order fulfilled"),
    (OrderStatus.BOOKED, OrderStatus.UNBOOKED, "Booking reversed by ad server"),

    # Failure / cancellation from any active state
    (OrderStatus.DRAFT, OrderStatus.CANCELLED, "Cancelled before submission"),
    (OrderStatus.SUBMITTED, OrderStatus.CANCELLED, "Cancelled after submission"),
    (OrderStatus.SUBMITTED, OrderStatus.FAILED, "Submission processing failed"),
    (OrderStatus.PENDING_APPROVAL, OrderStatus.CANCELLED, "Cancelled during approval"),
    (OrderStatus.APPROVED, OrderStatus.CANCELLED, "Cancelled after approval"),
    (OrderStatus.IN_PROGRESS, OrderStatus.FAILED, "Execution failed"),
    (OrderStatus.IN_PROGRESS, OrderStatus.CANCELLED, "Cancelled during execution"),
    (OrderStatus.SYNCING, OrderStatus.FAILED, "Ad server sync failed"),

    # Re-submission
    (OrderStatus.REJECTED, OrderStatus.DRAFT, "Returned to draft for revision"),
    (OrderStatus.FAILED, OrderStatus.DRAFT, "Reset to draft after failure"),
    (OrderStatus.UNBOOKED, OrderStatus.DRAFT, "Reset to draft after unbooking"),
]


def _build_default_rules() -> list[TransitionRule]:
    return [
        TransitionRule(from_status=f, to_status=t, description=d)
        for f, t, d in _DEFAULT_TRANSITIONS
    ]


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when a state transition is not allowed."""

    def __init__(self, order_id: str, from_status: OrderStatus, to_status: OrderStatus, reason: str = ""):
        self.order_id = order_id
        self.from_status = from_status
        self.to_status = to_status
        msg = f"Cannot transition order {order_id} from {from_status.value} to {to_status.value}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class OrderStateMachine:
    """Formal state machine for order lifecycle management.

    Provides:
    - Configurable transition rules with guard conditions
    - Full audit trail of every state change
    - Event bus integration for state change notifications
    - Query helpers for allowed next states
    """

    def __init__(
        self,
        order_id: str,
        initial_status: OrderStatus = OrderStatus.DRAFT,
        rules: Optional[list[TransitionRule]] = None,
    ):
        self.order_id = order_id
        self._status = initial_status
        self._rules = rules or _build_default_rules()
        self._audit = OrderAuditLog(order_id=order_id)

        # Index rules for fast lookup: (from, to) -> rule
        self._rule_index: dict[tuple[OrderStatus, OrderStatus], TransitionRule] = {
            (r.from_status, r.to_status): r for r in self._rules
        }

    @property
    def status(self) -> OrderStatus:
        return self._status

    @property
    def audit_log(self) -> OrderAuditLog:
        return self._audit

    @property
    def history(self) -> list[StateTransition]:
        return self._audit.transitions

    def allowed_transitions(self) -> list[OrderStatus]:
        """Return the list of states reachable from the current state."""
        return [
            to for (frm, to), _ in self._rule_index.items()
            if frm == self._status
        ]

    def can_transition(self, to_status: OrderStatus, context: Optional[dict[str, Any]] = None) -> bool:
        """Check whether a transition is permitted (including guard)."""
        rule = self._rule_index.get((self._status, to_status))
        if rule is None:
            return False
        if rule.guard is not None:
            return rule.guard(self.order_id, self._status, to_status, context or {})
        return True

    def transition(
        self,
        to_status: OrderStatus,
        *,
        actor: str = "system",
        reason: str = "",
        context: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> StateTransition:
        """Execute a state transition.

        Raises InvalidTransitionError if the transition is not allowed.
        Returns the StateTransition audit record.
        """
        rule = self._rule_index.get((self._status, to_status))
        if rule is None:
            raise InvalidTransitionError(self.order_id, self._status, to_status, "no matching transition rule")

        if rule.guard is not None:
            ctx = context or {}
            if not rule.guard(self.order_id, self._status, to_status, ctx):
                raise InvalidTransitionError(self.order_id, self._status, to_status, "guard condition failed")

        from_status = self._status
        self._status = to_status

        record = StateTransition(
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            reason=reason or rule.description,
            metadata=metadata or {},
        )
        self._audit.append(record)
        return record

    def add_rule(self, rule: TransitionRule) -> None:
        """Add a custom transition rule (e.g. for vertical-specific workflows)."""
        self._rules.append(rule)
        self._rule_index[(rule.from_status, rule.to_status)] = rule

    def remove_rule(self, from_status: OrderStatus, to_status: OrderStatus) -> bool:
        """Remove a transition rule. Returns True if a rule was removed."""
        key = (from_status, to_status)
        if key in self._rule_index:
            rule = self._rule_index.pop(key)
            self._rules.remove(rule)
            return True
        return False

    # Serialization helpers

    def to_dict(self) -> dict[str, Any]:
        """Serialize the machine state for storage."""
        return {
            "order_id": self.order_id,
            "status": self._status.value,
            "audit_log": self._audit.model_dump(mode="json"),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        rules: Optional[list[TransitionRule]] = None,
    ) -> "OrderStateMachine":
        """Restore a machine from stored state."""
        machine = cls(
            order_id=data["order_id"],
            initial_status=OrderStatus(data["status"]),
            rules=rules,
        )
        audit_data = data.get("audit_log", {})
        if audit_data:
            machine._audit = OrderAuditLog(**audit_data)
        return machine


# ---------------------------------------------------------------------------
# Mapping helpers — bridge old enums to the new unified status
# ---------------------------------------------------------------------------

_EXECUTION_STATUS_MAP: dict[str, OrderStatus] = {
    "initialized": OrderStatus.DRAFT,
    "product_setup": OrderStatus.DRAFT,
    "awaiting_proposals": OrderStatus.SUBMITTED,
    "proposal_received": OrderStatus.SUBMITTED,
    "evaluating": OrderStatus.IN_PROGRESS,
    "counter_pending": OrderStatus.IN_PROGRESS,
    "pending_approval": OrderStatus.PENDING_APPROVAL,
    "accepted": OrderStatus.APPROVED,
    "rejected": OrderStatus.REJECTED,
    "deal_created": OrderStatus.APPROVED,
    "syncing_to_ad_server": OrderStatus.SYNCING,
    "completed": OrderStatus.COMPLETED,
    "failed": OrderStatus.FAILED,
}

_EXECUTION_ORDER_STATUS_MAP: dict[str, OrderStatus] = {
    "draft": OrderStatus.DRAFT,
    "proposed": OrderStatus.SUBMITTED,
    "booked": OrderStatus.BOOKED,
    "unbooked": OrderStatus.UNBOOKED,
    "canceled": OrderStatus.CANCELLED,
}


def from_execution_status(value: str) -> OrderStatus:
    """Map a legacy ExecutionStatus value to OrderStatus."""
    return _EXECUTION_STATUS_MAP.get(value, OrderStatus.DRAFT)


def from_execution_order_status(value: str) -> OrderStatus:
    """Map a legacy ExecutionOrderStatus value to OrderStatus."""
    return _EXECUTION_ORDER_STATUS_MAP.get(value, OrderStatus.DRAFT)
