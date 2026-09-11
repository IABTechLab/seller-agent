# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Change request endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ....services import order_service
from .. import contract_mappers as cm
from .. import deps
from ..schemas import CreateChangeRequestModel, ReviewChangeRequestModel

router = APIRouter()


@router.post("/api/v1/change-requests", tags=["Change Requests"])
async def create_change_request(
    request: CreateChangeRequestModel,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Submit a change request for an existing order.

    Validates the change against the current order state, classifies
    severity, and routes to approval if needed.

    **Idempotency (FD-12):** the request carries a required
    ``idempotency_key``. An identical replay returns the original change
    request without creating a second approval; reusing the key with a
    different body returns ``idempotency_conflict`` (HTTP 409). Keys are
    scoped per order and expire after 24 hours.
    """
    from ....storage.factory import get_storage

    storage_key = f"idempotency:change-request:{request.order_id}:{request.idempotency_key}"
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
        return await order_service.get_change_request(prior["change_request_id"])

    result = await order_service.create_change_request(request)

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


@router.get("/api/v1/change-requests", tags=["Change Requests"])
async def list_change_requests(
    order_id: Optional[str] = None,
    status: Optional[str] = None,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """List change requests, optionally filtered by order or status."""
    return await order_service.list_change_requests(order_id=order_id, status=status)


@router.get("/api/v1/change-requests/{cr_id}", tags=["Change Requests"])
async def get_change_request(
    cr_id: str,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Get a change request by ID."""
    return await order_service.get_change_request(cr_id)


@router.post("/api/v1/change-requests/{cr_id}/review", tags=["Change Requests"])
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


@router.post("/api/v1/change-requests/{cr_id}/apply", tags=["Change Requests"])
async def apply_change_request(
    cr_id: str,
    _operator=Depends(deps._require_operator_api_key_record),
):
    """Apply an approved change request to the order.

    Updates the order with the proposed values from the change request.
    Requires an operator credential (order mutation).
    """
    return await order_service.apply_change_request(cr_id)
