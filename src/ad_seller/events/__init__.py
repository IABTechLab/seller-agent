# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event bus for human-in-the-loop workflow control."""

from .approval import ApprovalGate
from .audit_fallback import get_audit_fallback_path, write_audit_fallback
from .bus import EventBus, close_event_bus, get_event_bus
from .helpers import emit_event
from .models import (
    AUDIT_EVENT_TYPES,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalStatus,
    Event,
    EventType,
)

__all__ = [
    "AUDIT_EVENT_TYPES",
    "Event",
    "EventType",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalStatus",
    "EventBus",
    "get_event_bus",
    "close_event_bus",
    "ApprovalGate",
    "emit_event",
    "get_audit_fallback_path",
    "write_audit_fallback",
]
