# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Helper functions for emitting events from flows.

Thin wrappers that flows call. If the event bus is not configured
or fails, they log and continue (fail-open).
"""

import logging
from typing import Any, Optional

from .models import Event, EventType

logger = logging.getLogger(__name__)


async def emit_event(
    event_type: EventType,
    flow_id: str = "",
    flow_type: str = "",
    proposal_id: str = "",
    deal_id: str = "",
    payload: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> Optional[Event]:
    """Emit an event to the event bus. Fail-open: logs on error.

    Returns the Event if published, None if the bus was unavailable.
    """
    try:
        from .bus import get_event_bus
        bus = await get_event_bus()
        event = Event(
            event_type=event_type,
            flow_id=flow_id,
            flow_type=flow_type,
            proposal_id=proposal_id,
            deal_id=deal_id,
            payload=payload or {},
            metadata=kwargs,
        )
        await bus.publish(event)
        return event
    except Exception as e:
        logger.warning("Failed to emit event %s: %s", event_type, e)
        return None
