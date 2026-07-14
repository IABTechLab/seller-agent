# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Multi-turn buyer conversation session endpoints.

Session orchestration goes through ``services.session_service`` — the
router no longer touches the chat interface directly (adapter rewiring
proper is EP-3.2).
"""

from typing import Optional

from fastapi import APIRouter, Depends

from ....services import session_service
from .. import deps
from ..schemas import CreateSessionRequest, SessionMessageRequest

router = APIRouter()


@router.post("/sessions", tags=["Sessions"])
async def create_session(
    request: CreateSessionRequest,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Create a new buyer conversation session."""
    # Enforce agent registry
    _, max_tier = await deps._resolve_and_enforce_agent(request.agent_url)

    # API key identity overrides body params; is_authenticated derived from key
    context = deps._build_buyer_context(
        buyer_tier="advertiser"
        if request.advertiser_id
        else ("agency" if request.agency_id else ("seat" if request.seat_id else "public")),
        agency_id=request.agency_id,
        advertiser_id=request.advertiser_id,
        seat_id=request.seat_id,
        api_key_record=api_key_record,
        agent_url=request.agent_url,
        max_access_tier=max_tier,
    )

    return await session_service.create_session(buyer_context=context)


@router.get("/sessions", tags=["Sessions"])
async def list_sessions(
    buyer_key: Optional[str] = None,
    status: Optional[str] = None,
):
    """List sessions, optionally filtered by buyer identity or status."""
    return await session_service.list_sessions(buyer_key=buyer_key, status=status)


@router.get("/sessions/{session_id}", tags=["Sessions"])
async def get_session(session_id: str):
    """Get session details and conversation history."""
    return await session_service.get_session(session_id)


@router.post("/sessions/{session_id}/messages", tags=["Sessions"])
async def send_session_message(session_id: str, body: SessionMessageRequest):
    """Send a message within a session and get a response."""
    return await session_service.send_session_message(session_id, body.message)


@router.post("/sessions/{session_id}/close", tags=["Sessions"])
async def close_session_endpoint(session_id: str):
    """Close a session."""
    return await session_service.close_session(session_id)
