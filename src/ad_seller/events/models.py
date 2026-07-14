# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event and approval data models."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events emitted by the system."""

    # Proposal lifecycle
    PROPOSAL_RECEIVED = "proposal.received"
    PROPOSAL_EVALUATED = "proposal.evaluated"
    PROPOSAL_ACCEPTED = "proposal.accepted"
    PROPOSAL_REJECTED = "proposal.rejected"
    PROPOSAL_COUNTERED = "proposal.countered"

    # Deal lifecycle
    DEAL_CREATED = "deal.created"
    DEAL_REGISTERED = "deal.registered"

    # Execution lifecycle
    DEAL_SYNCED = "deal.synced"
    EXECUTION_COMPLETED = "execution.completed"

    # Approval gates
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    APPROVAL_TIMED_OUT = "approval.timed_out"

    # Session lifecycle
    SESSION_CREATED = "session.created"
    SESSION_RESUMED = "session.resumed"
    SESSION_CLOSED = "session.closed"

    # Package lifecycle
    PACKAGE_CREATED = "package.created"
    PACKAGE_UPDATED = "package.updated"
    PACKAGE_SYNCED = "package.synced"

    # Negotiation lifecycle
    NEGOTIATION_STARTED = "negotiation.started"
    NEGOTIATION_ROUND = "negotiation.round"
    NEGOTIATION_CONCLUDED = "negotiation.concluded"


# Audit-class event types: money decisions and order state transitions whose
# loss would break the audit trail. Emission for these is fail-closed: if the
# event bus fails, the event is appended to a durable fallback JSONL file; if
# that also fails, the error propagates to the caller instead of being
# swallowed. Non-audit events keep the existing fail-open behavior.
# See events/helpers.py and events/audit_fallback.py.
AUDIT_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        # Proposal decisions / negotiation concessions
        EventType.PROPOSAL_ACCEPTED,
        EventType.PROPOSAL_REJECTED,
        EventType.PROPOSAL_COUNTERED,
        # Deal / order state transitions
        EventType.DEAL_CREATED,
        EventType.DEAL_REGISTERED,
        EventType.DEAL_SYNCED,
        EventType.EXECUTION_COMPLETED,
        # Approval decisions
        EventType.APPROVAL_REQUESTED,
        EventType.APPROVAL_GRANTED,
        EventType.APPROVAL_DENIED,
        EventType.APPROVAL_TIMED_OUT,
        # Negotiation rounds / concessions
        EventType.NEGOTIATION_STARTED,
        EventType.NEGOTIATION_ROUND,
        EventType.NEGOTIATION_CONCLUDED,
    }
)


class Event(BaseModel):
    """An event emitted by the system."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    flow_id: str = ""
    flow_type: str = ""
    proposal_id: str = ""
    deal_id: str = ""
    session_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class ApprovalRequest(BaseModel):
    """A request for human approval that gates a flow."""

    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str  # The event that triggered this approval request
    flow_id: str
    flow_type: str
    gate_name: str  # e.g. "proposal_decision", "deal_registration"
    proposal_id: str = ""
    deal_id: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    context: dict[str, Any] = Field(default_factory=dict)
    flow_state_snapshot: dict[str, Any] = Field(default_factory=dict)


class ApprovalResponse(BaseModel):
    """A human's response to an approval request."""

    approval_id: str
    decision: str  # "approve", "reject", or "counter"
    decided_by: str = "unknown"  # Free-text display label (untrusted)
    # Verified approver principal, derived from the authenticated API key
    # (see auth.dependencies.principal_from_api_key). This is the identity
    # the audit trail trusts — ``decided_by`` above is display-only.
    decided_by_principal: str = ""
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    reason: str = ""
    modifications: dict[str, Any] = Field(default_factory=dict)
