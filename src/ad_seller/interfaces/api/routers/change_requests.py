# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Change request endpoints.

Every route here REQUIRES a credential and every route declares a
``response_model`` (seller-928).

Why: a change request record embeds ``rollback_snapshot`` — a full copy
of the order, including its state-machine audit log with every
transition's actor and reason, plus the order's quote id, deal id and
metadata. Before this module was hardened the listing route took optional
auth, required no filter, and declared no response model, so a single
anonymous ``GET /api/v1/change-requests`` returned every order's audit
trail for every tenant.

The two gates that close it:

* **Authentication.** Create/list/read take ``require_api_key_record``
  (anonymous → 401), matching the approvals router. Review/apply keep
  their stricter operator gate.
* **Ownership.** Authentication alone is not enough — an authenticated
  buyer would still read every other buyer's trail. Each record is
  stamped at creation with an actor derived from the credential
  (``actor_from_api_key``), and buyer callers see only records carrying
  their own actor. Operator callers are unscoped: reviewing a change
  request is the seller's job and needs the whole queue.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ....auth.dependencies import actor_from_api_key, require_api_key_record
from ....models.api_key import ApiKeyRecord, ApiKeyRole
from ....services import order_service
from .. import contract_mappers as cm
from .. import deps
from ..schemas import (
    ChangeRequestApplyResponse,
    ChangeRequestListResponse,
    ChangeRequestResponse,
    CreateChangeRequestModel,
    ReviewChangeRequestModel,
)

router = APIRouter()


def _read_scope(caller: ApiKeyRecord) -> Optional[str]:
    """Actor id a caller's reads are confined to, or None for unscoped.

    Operators see the whole queue — they are the ones who review it.
    Every other credential sees only what it filed.
    """
    if caller.role == ApiKeyRole.OPERATOR:
        return None
    return actor_from_api_key(caller)


@router.post(
    "/api/v1/change-requests",
    tags=["Change Requests"],
    response_model=ChangeRequestResponse,
)
async def create_change_request(
    request: CreateChangeRequestModel,
    caller: ApiKeyRecord = Depends(require_api_key_record),
):
    """Submit a change request for an existing order. Requires authentication.

    The requesting actor is stamped from the presented credential, never
    from the body — ``requested_by`` is exactly the field a human
    reviewer trusts when deciding whether to approve, so a caller must
    not be able to name itself "human:cfo@publisher.example".

    **Idempotency (FD-12):** the request carries a required
    ``idempotency_key``. An identical replay returns the original change
    request without creating a second approval; reusing the key with a
    different body returns ``idempotency_conflict`` (HTTP 409). Keys are
    scoped per actor and order, and expire after 24 hours.
    """
    from ....storage.factory import get_storage

    actor = actor_from_api_key(caller)

    # The actor is part of the key: without it one buyer replaying
    # another's (order_id, idempotency_key) pair would be handed back the
    # other buyer's change request through the replay path, straight past
    # the ownership scoping below.
    storage_key = f"idempotency:change-request:{actor}:{request.order_id}:{request.idempotency_key}"
    payload_hash = cm.request_payload_hash(
        request.model_dump(mode="json", exclude={"idempotency_key"})
    )
    storage = await get_storage()
    try:
        prior = await storage.get(storage_key)
    except Exception:
        prior = None

    if isinstance(prior, dict) and prior.get("change_request_id"):
        if prior.get("payload_hash") != payload_hash:
            raise HTTPException(
                status_code=409,
                detail=cm.idempotency_conflict_detail(
                    f"idempotency_key '{request.idempotency_key}' was already used "
                    "for a different change request."
                ),
            )
        return await order_service.get_change_request(
            prior["change_request_id"],
            requested_by=_read_scope(caller),
        )

    result = await order_service.create_change_request(request, requested_by=actor)

    try:
        await storage.set(
            storage_key,
            {
                "change_request_id": result["change_request_id"],
                "payload_hash": payload_hash,
            },
            ttl=86400,
        )
    except Exception:
        pass

    return result


@router.get(
    "/api/v1/change-requests",
    tags=["Change Requests"],
    response_model=ChangeRequestListResponse,
)
async def list_change_requests(
    order_id: Optional[str] = None,
    status: Optional[str] = None,
    caller: ApiKeyRecord = Depends(require_api_key_record),
):
    """List change requests, optionally filtered by order or status.

    Requires authentication and returns only the caller's own change
    requests; an operator credential sees all of them.
    """
    return await order_service.list_change_requests(
        order_id=order_id,
        status=status,
        requested_by=_read_scope(caller),
    )


@router.get(
    "/api/v1/change-requests/{cr_id}",
    tags=["Change Requests"],
    response_model=ChangeRequestResponse,
)
async def get_change_request(
    cr_id: str,
    caller: ApiKeyRecord = Depends(require_api_key_record),
):
    """Get a change request by ID.

    Requires authentication. Another actor's change request reads as 404,
    so the route cannot be walked as an existence oracle over change
    request ids.
    """
    return await order_service.get_change_request(cr_id, requested_by=_read_scope(caller))


@router.post(
    "/api/v1/change-requests/{cr_id}/review",
    tags=["Change Requests"],
    response_model=ChangeRequestResponse,
)
async def review_change_request(
    cr_id: str,
    request: ReviewChangeRequestModel,
    _operator=Depends(deps._require_operator_api_key_record),
):
    """Approve or reject a pending change request.

    Requires an operator credential — the decision is the seller's, not
    the requesting buyer's.
    """
    return await order_service.review_change_request(
        cr_id=cr_id,
        decision=request.decision,
        decided_by=request.decided_by,
        reason=request.reason,
    )


@router.post(
    "/api/v1/change-requests/{cr_id}/apply",
    tags=["Change Requests"],
    response_model=ChangeRequestApplyResponse,
)
async def apply_change_request(
    cr_id: str,
    _operator=Depends(deps._require_operator_api_key_record),
):
    """Apply an approved change request to the order.

    Updates the order with the proposed values from the change request.
    Requires an operator credential (order mutation).
    """
    return await order_service.apply_change_request(cr_id)
