# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""FastAPI authentication dependencies.

Provides Depends()-compatible callables for extracting buyer
identity from API key headers. Anonymous requests (no key)
are allowed and result in PUBLIC tier access.

Usage in endpoints::

    from ad_seller.auth.dependencies import get_api_key_record

    @app.post("/pricing")
    async def get_pricing(
        request: PricingRequest,
        api_key_record: Optional[ApiKeyRecord] = Depends(get_api_key_record),
    ):
        # api_key_record is None if anonymous, ApiKeyRecord if key valid
        # 401 raised automatically for invalid/revoked/expired keys
        ...
"""

import logging
from typing import Optional

from fastapi import Header, HTTPException

from ..models.api_key import ApiKeyRecord

logger = logging.getLogger(__name__)


def _extract_key_from_headers(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> Optional[str]:
    """Extract API key from request headers.

    Checks ``X-Api-Key`` header first, then ``Authorization: Bearer <key>``.
    Returns None if no key is present (anonymous request).
    """
    if x_api_key:
        return x_api_key
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


async def get_api_key_record(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> Optional[ApiKeyRecord]:
    """Validate API key and return the record.

    Returns None for anonymous requests (no key in headers).
    Raises HTTPException(401) for invalid, revoked, or expired keys.

    This is the primary dependency for data endpoints.
    """
    raw_key = _extract_key_from_headers(authorization, x_api_key)
    if raw_key is None:
        return None

    from ..auth.api_key_service import ApiKeyService
    from ..storage.factory import get_storage

    storage = await get_storage()
    service = ApiKeyService(storage)

    try:
        record = await service.validate_key(raw_key)
    except ValueError as e:
        # Key found but revoked or expired
        raise HTTPException(
            status_code=401,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )

    if record is None:
        # Key not found at all
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return record


async def require_api_key_record(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> ApiKeyRecord:
    """Validate API key and require a valid credential.

    Unlike :func:`get_api_key_record` (which allows anonymous PUBLIC-tier
    access by returning ``None``), this dependency REJECTS anonymous
    requests with 401. It reuses the exact same header-binding and
    validation semantics (invalid/revoked/expired keys still raise 401);
    it only additionally forbids the no-credential path.

    Use this to protect privileged control-plane endpoints — e.g. the
    human-in-the-loop approval decide/resume/list endpoints — where an
    unauthenticated caller must never be able to act.
    """
    record = await get_api_key_record(authorization, x_api_key)
    if record is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return record


def principal_from_api_key(record: ApiKeyRecord) -> str:
    """Derive a stable, verified principal identifier from an API key record.

    The returned string is stamped into the approval audit record as the
    VERIFIED approver identity (as opposed to the free-text ``decided_by``
    display field, which the caller can set to anything). It is anchored to
    the immutable ``key_id`` of the authenticated credential and enriched
    with the strongest available buyer identity for readability.
    """
    identity = record.identity
    buyer = (
        getattr(identity, "advertiser_id", None)
        or getattr(identity, "agency_id", None)
        or getattr(identity, "seat_id", None)
        or "unknown"
    )
    return f"apikey:{record.key_id}:{buyer}"
