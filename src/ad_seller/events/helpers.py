# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Helper functions for emitting events from flows.

Thin wrappers that flows call. For most events, if the event bus is not
configured or fails, they log and continue (fail-open).

Audit-class events (AUDIT_EVENT_TYPES in models.py) are fail-closed: if
the bus fails, the event is written to a durable fallback JSONL file
(see audit_fallback.py); if that also fails, the error propagates so the
calling transaction surfaces it instead of silently losing audit trail.
"""

import logging
from typing import Any, Optional

from .audit_fallback import write_audit_fallback
from .models import AUDIT_EVENT_TYPES, Event, EventType

logger = logging.getLogger(__name__)


def _audit_fallback_record(
    event: Optional[Event],
    event_type: EventType,
    flow_id: str,
    flow_type: str,
    proposal_id: str,
    deal_id: str,
    session_id: str,
    payload: Optional[dict[str, Any]],
    metadata: dict[str, Any],
    error: Exception,
) -> dict[str, Any]:
    """Build the JSONL record for an audit event that failed to publish.

    Uses the constructed Event when available; otherwise reconstructs the
    record from the emit arguments (e.g. when the bus factory itself failed
    before the Event was built).
    """
    if event is not None:
        record = event.model_dump(mode="json")
    else:
        record = {
            "event_type": event_type.value,
            "flow_id": flow_id,
            "flow_type": flow_type,
            "proposal_id": proposal_id,
            "deal_id": deal_id,
            "session_id": session_id,
            "payload": payload or {},
            "metadata": metadata,
        }
    record["emit_error"] = str(error)
    return record


async def emit_event(
    event_type: EventType,
    flow_id: str = "",
    flow_type: str = "",
    proposal_id: str = "",
    deal_id: str = "",
    session_id: str = "",
    payload: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> Optional[Event]:
    """Emit an event to the event bus.

    Fail-open for regular events: logs on error and returns None.
    Fail-closed for audit-class events: on bus failure the event is written
    to the fallback JSONL (returns None); if the fallback write also fails,
    the exception propagates.

    Returns the Event if published, None if the bus was unavailable.
    """
    event: Optional[Event] = None
    try:
        from .bus import get_event_bus

        bus = await get_event_bus()
        event = Event(
            event_type=event_type,
            flow_id=flow_id,
            flow_type=flow_type,
            proposal_id=proposal_id,
            deal_id=deal_id,
            session_id=session_id,
            payload=payload or {},
            metadata=kwargs,
        )
        await bus.publish(event)
        return event
    except Exception as e:
        if event_type not in AUDIT_EVENT_TYPES:
            logger.warning("Failed to emit event %s: %s", event_type, e)
            return None

        record = _audit_fallback_record(
            event,
            event_type,
            flow_id,
            flow_type,
            proposal_id,
            deal_id,
            session_id,
            payload,
            kwargs,
            e,
        )
        try:
            write_audit_fallback(record)
        except Exception as fallback_error:
            logger.error(
                "Audit fallback write failed for %s: %s (bus error: %s)",
                event_type,
                fallback_error,
                e,
            )
            raise
        logger.warning(
            "Event bus failed for audit event %s; wrote to fallback log: %s",
            event_type,
            e,
        )
        return None
