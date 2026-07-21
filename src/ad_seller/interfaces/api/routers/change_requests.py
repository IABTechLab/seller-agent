# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Change request endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends

from ....services import order_service
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
    """
    return await order_service.create_change_request(request)


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
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Approve or reject a pending change request."""
    return await order_service.review_change_request(
        cr_id=cr_id,
        decision=request.decision,
        decided_by=request.decided_by,
        reason=request.reason,
    )


@router.post("/api/v1/change-requests/{cr_id}/apply", tags=["Change Requests"])
async def apply_change_request(
    cr_id: str,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Apply an approved change request to the order.

    Updates the order with the proposed values from the change request.
    """
    return await order_service.apply_change_request(cr_id)
