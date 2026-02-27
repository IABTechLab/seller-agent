# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event bus for human-in-the-loop workflow control."""

from .models import Event, EventType, ApprovalRequest, ApprovalResponse, ApprovalStatus
from .bus import EventBus, get_event_bus, close_event_bus
from .approval import ApprovalGate
from .helpers import emit_event

__all__ = [
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
]
