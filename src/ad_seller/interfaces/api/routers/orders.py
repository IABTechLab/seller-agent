# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Order state machine and lifecycle endpoints (seller-cnd / seller-5ks).

Registration order preserved from main.py — in particular
``GET /api/v1/orders/report`` is registered BEFORE
``GET /api/v1/orders/{order_id}``.
"""

from typing import Optional

from fastapi import APIRouter, Depends

from ....services import order_service
from .. import deps
from ..schemas import CreateOrderRequest, TransitionOrderRequest

router = APIRouter()


@router.post("/api/v1/orders", tags=["Orders"])
async def create_order(
    request: CreateOrderRequest,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Create a new order and persist its state machine."""
    return await order_service.create_order(
        deal_id=request.deal_id,
        quote_id=request.quote_id,
        metadata=request.metadata,
    )


@router.get("/api/v1/orders", tags=["Orders"])
async def list_orders(
    status: Optional[str] = None,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """List orders, optionally filtered by status."""
    return await order_service.list_orders(status=status)


@router.get("/api/v1/orders/report", tags=["Orders", "Audit"])
async def get_orders_report(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Summary report across all orders.

    Returns counts by status, transition frequency by actor type,
    and average time-in-state metrics.
    """
    return await order_service.get_orders_report(from_date=from_date, to_date=to_date)


@router.get("/api/v1/orders/{order_id}", tags=["Orders"])
async def get_order(
    order_id: str,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Get order current status and audit trail."""
    return await order_service.get_order(order_id)


@router.get("/api/v1/orders/{order_id}/history", tags=["Orders"])
async def get_order_history(
    order_id: str,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Get the full transition history for an order."""
    return await order_service.get_order_history(order_id)


@router.post("/api/v1/orders/{order_id}/transition", tags=["Orders"])
async def transition_order(
    order_id: str,
    request: TransitionOrderRequest,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Transition an order to a new state.

    Validates the transition against the state machine rules and
    records the change in the audit log.
    """
    return await order_service.transition_order(
        order_id=order_id,
        to_status=request.to_status,
        actor=request.actor,
        reason=request.reason,
        metadata=request.metadata,
    )


@router.get("/api/v1/orders/{order_id}/audit", tags=["Audit"])
async def get_order_audit(
    order_id: str,
    actor: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    _auth: None = Depends(deps._get_optional_api_key_record),
):
    """Detailed audit log for an order with optional filters.

    Filters:
      - actor: filter transitions by actor (exact or prefix match)
      - from_date: ISO date, only transitions on or after this date
      - to_date: ISO date, only transitions on or before this date
    """
    return await order_service.get_order_audit(
        order_id=order_id,
        actor=actor,
        from_date=from_date,
        to_date=to_date,
    )
