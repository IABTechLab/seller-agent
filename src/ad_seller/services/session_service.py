# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer conversation session service.

Extracted from ``interfaces/api/main.py`` (EP-3.1). Wraps the chat
interface behind a service boundary so API routers no longer import
another interface module directly. The chat adapter itself is untouched
(rewiring it onto services is EP-3.2); imports stay function-level as in
the original endpoint bodies.
"""

import logging
from typing import Any, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def create_session(buyer_context: Any) -> dict[str, Any]:
    """Create a new buyer conversation session."""
    from ..interfaces.chat.main import ChatInterface
    from ..storage.factory import get_storage

    storage = await get_storage()

    chat = ChatInterface(storage=storage)
    await chat.initialize()
    session = await chat.start_session(buyer_context=buyer_context)

    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "buyer_pricing_key": session.get_buyer_pricing_key(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


async def list_sessions(
    buyer_key: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    """List sessions, optionally filtered by buyer identity or status."""
    from ..models.session import Session, SessionStatus
    from ..storage.factory import get_storage

    storage = await get_storage()

    if buyer_key:
        sessions_data = await storage.get_buyer_sessions(buyer_key)
    else:
        sessions_data = await storage.list_sessions()

    results = []
    for data in sessions_data:
        s = Session(**data)
        # Lazy expiration check
        if s.is_expired() and s.status != SessionStatus.EXPIRED:
            s.status = SessionStatus.EXPIRED
            await storage.set_session(s.session_id, s.model_dump(mode="json"))
        # Apply status filter
        if status and s.status.value != status:
            continue
        results.append(
            {
                "session_id": s.session_id,
                "status": s.status.value,
                "buyer_pricing_key": s.get_buyer_pricing_key(),
                "message_count": len(s.messages),
                "negotiation_stage": s.negotiation.stage,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
        )

    return {"sessions": results}


async def get_session(session_id: str) -> dict[str, Any]:
    """Get session details and conversation history."""
    from ..models.session import Session
    from ..storage.factory import get_storage

    storage = await get_storage()
    data = await storage.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    session = Session(**data)
    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "buyer_pricing_key": session.get_buyer_pricing_key(),
        "negotiation": session.negotiation.model_dump(),
        "messages": [m.model_dump(mode="json") for m in session.messages],
        "linked_flow_ids": session.linked_flow_ids,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


async def send_session_message(session_id: str, message: str) -> dict[str, Any]:
    """Send a message within a session and get a response."""
    from ..interfaces.chat.main import ChatInterface
    from ..storage.factory import get_storage

    storage = await get_storage()

    chat = ChatInterface(storage=storage)
    await chat.initialize()

    try:
        await chat.resume_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    response = await chat.process_message_async(
        message=message,
        session_id=session_id,
    )

    session = chat._current_session
    return {
        "session_id": session_id,
        "text": response.get("text", ""),
        "type": response.get("type", "general"),
        "message_count": len(session.messages) if session else 0,
        "negotiation_stage": session.negotiation.stage if session else "unknown",
    }


async def close_session(session_id: str) -> dict[str, Any]:
    """Close a session."""
    from ..interfaces.chat.main import ChatInterface
    from ..storage.factory import get_storage

    storage = await get_storage()

    chat = ChatInterface(storage=storage)
    await chat.close_session(session_id)

    return {"session_id": session_id, "status": "closed"}
