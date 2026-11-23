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


def _opened_with_key(data: dict[str, Any]) -> bool:
    context = data.get("buyer_context") or {}
    return bool(data.get("owner_key_id")) or context.get("authentication_method") == "api_key"


def _key_owns(data: dict[str, Any], api_key_record: Any) -> bool:
    """Whether the stored session was opened by this API key.

    Sessions record the opening key's ``key_id``. Keys carry optional,
    non-unique identity fields, so two keys can share a pricing key; only
    sessions opened with a key before ``owner_key_id`` was recorded fall
    back to matching the pricing key, and never on the shared ``public``.
    """
    if api_key_record is None or not _opened_with_key(data):
        return False
    owner_key_id = data.get("owner_key_id")
    if owner_key_id:
        return getattr(api_key_record, "key_id", None) == owner_key_id
    pricing_key = session_service.owner_key(data)
    caller_key = deps._build_buyer_context(api_key_record=api_key_record).get_pricing_key()
    return pricing_key != "public" and pricing_key == caller_key


async def _owned_session(session_id: str, api_key_record: Any) -> None:
    """Allow the key that opened the session or an operator key; 404 otherwise.

    A session opened without a key (anonymously, before keys were required
    or with ALLOW_ANONYMOUS_BUYER_WRITES) has no owner. It is operator-only,
    unless ALLOW_ANONYMOUS_BUYER_WRITES is on, in which case any caller the
    route admits may use it. Not-yours reads as 404, the same as a missing
    session.
    """
    if deps._is_operator(api_key_record):
        return
    data = await session_service.get_session_record(session_id)
    if data is not None and not _opened_with_key(data):
        if deps._get_api_settings().allow_anonymous_buyer_writes:
            return
        raise HTTPException(status_code=404, detail="Session not found")
    if data is None or not _key_owns(data, api_key_record):
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

    return await session_service.create_session(
        buyer_context=context,
        owner_key_id=getattr(api_key_record, "key_id", None),
    )


@router.get("/sessions", tags=["Sessions"])
async def list_sessions(
    buyer_key: Optional[str] = None,
    status: Optional[str] = None,
    api_key_record=Depends(deps._require_api_key_record),
):
    """List sessions. Operators may filter by any buyer; buyers see their own."""
    if deps._is_operator(api_key_record):
        return await session_service.list_sessions(buyer_key=buyer_key, status=status)
    # A key's sessions are all indexed under its own pricing key.
    return await session_service.list_sessions(
        buyer_key=deps._build_buyer_context(api_key_record=api_key_record).get_pricing_key(),
        status=status,
        include=lambda data: _key_owns(data, api_key_record),
    )


@router.get("/sessions/{session_id}", tags=["Sessions"])
async def get_session(
    session_id: str,
    api_key_record=Depends(deps._buyer_write_api_key_record),
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
