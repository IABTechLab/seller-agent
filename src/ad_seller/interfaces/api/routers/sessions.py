# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Multi-turn buyer conversation session endpoints.

Session orchestration goes through ``services.session_service`` — the
router no longer touches the chat interface directly (adapter rewiring
proper is EP-3.2).
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from ....services import session_service
from .. import deps
from ..schemas import CreateSessionRequest, SessionMessageRequest

router = APIRouter()


async def _owned_session(session_id: str, api_key_record: Any) -> None:
    """Allow the session's own buyer key or an operator key; 404 otherwise.

    A session opened with an API key belongs to that key's buyer. A session
    opened anonymously (only possible with ALLOW_ANONYMOUS_BUYER_WRITES) has
    no owner key, so any caller allowed to write may use it. Not-yours reads
    as 404, the same as a missing session.
    """
    if deps._is_operator(api_key_record):
        return
    data = await session_service.get_session_record(session_id)
    owner = (data or {}).get("buyer_context") or {}
    if data is not None and owner.get("authentication_method") != "api_key":
        return
    caller_key = (
        deps._build_buyer_context(api_key_record=api_key_record).get_pricing_key()
        if api_key_record is not None
        else None
    )
    if data is None or caller_key is None or caller_key != session_service.owner_key(data):
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/sessions", tags=["Sessions"])
async def create_session(
    request: CreateSessionRequest,
    api_key_record=Depends(deps._buyer_write_api_key_record),
):
    """Create a new buyer conversation session (API key required)."""
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
    api_key_record=Depends(deps._require_api_key_record),
):
    """List sessions. Operators may filter by any buyer; buyers see their own."""
    if not deps._is_operator(api_key_record):
        buyer_key = deps._build_buyer_context(api_key_record=api_key_record).get_pricing_key()
    return await session_service.list_sessions(buyer_key=buyer_key, status=status)


@router.get("/sessions/{session_id}", tags=["Sessions"])
async def get_session(
    session_id: str,
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """Get session details and conversation history (owner or operator)."""
    await _owned_session(session_id, api_key_record)
    return await session_service.get_session(session_id)


@router.post("/sessions/{session_id}/messages", tags=["Sessions"])
async def send_session_message(
    session_id: str,
    body: SessionMessageRequest,
    api_key_record=Depends(deps._buyer_write_api_key_record),
):
    """Send a message within a session and get a response (owner or operator)."""
    await _owned_session(session_id, api_key_record)
    return await session_service.send_session_message(session_id, body.message)


@router.post("/sessions/{session_id}/close", tags=["Sessions"])
async def close_session_endpoint(
    session_id: str,
    api_key_record=Depends(deps._buyer_write_api_key_record),
):
    """Close a session (owner or operator)."""
    await _owned_session(session_id, api_key_record)
    return await session_service.close_session(session_id)
