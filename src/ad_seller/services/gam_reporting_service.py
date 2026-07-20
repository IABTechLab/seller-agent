# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""GAM reporting service (main PR #12's /gam surface, service-layer edition).

Read-only operator/reporting operations against Google Ad Manager:
list recent orders (optionally only agent-created ones, resolved through
deal storage's ``gam_order_id`` links) and pull delivery reports.

Raises ``HTTPException`` exactly as the endpoints should surface them:
503 when GAM is not configured, 400 for bad order-id input, 502 when the
ad server call fails. The MCP tools and REST router are thin adapters
over these functions.
"""

from typing import Any

from fastapi import HTTPException

GAM_NOT_CONFIGURED_MSG = (
    "GAM not configured — set GAM_ENABLED=true, GAM_NETWORK_CODE, GAM_JSON_KEY_PATH"
)


def _require_gam_configured() -> Any:
    """Return settings, or raise 503 when the GAM trio is not configured."""
    from ..config import get_settings

    settings = get_settings()
    if not (settings.gam_enabled and settings.gam_network_code and settings.gam_json_key_path):
        raise HTTPException(status_code=503, detail=GAM_NOT_CONFIGURED_MSG)
    return settings


async def list_gam_orders(limit: int = 50, agent_created_only: bool = False) -> dict[str, Any]:
    """List recent GAM orders directly from the ad server.

    When ``agent_created_only`` is true, deal storage is the source of
    truth: every stored deal with a ``gam_order_id`` link (written at
    trafficking time) is resolved to its GAM order.
    """
    settings = _require_gam_configured()
    try:
        from ..clients.gam_soap_client import GAMSoapClient
        from ..storage.factory import get_storage

        client = GAMSoapClient()
        client.connect()

        if agent_created_only:
            storage = await get_storage()
            all_deals = await storage.list_deals()
            linked = [
                {"deal_id": d.get("deal_id"), "gam_order_id": d.get("gam_order_id")}
                for d in all_deals
                if d.get("gam_order_id")
            ]
            orders = []
            for link in linked[:limit]:
                order = client.get_order_by_id(link["gam_order_id"])
                if order:
                    order["external_order_id"] = link["deal_id"]
                    order["agent_created"] = True
                    orders.append(order)
        else:
            orders = client.list_orders(limit=limit)

        user = client.get_current_user()
        client.disconnect()
        return {
            "network_code": settings.gam_network_code,
            "user": {
                "id": str(getattr(user, "id", "")),
                "name": getattr(user, "name", ""),
                "email": getattr(user, "email", ""),
            },
            "orders": orders,
            "count": len(orders),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


async def get_gam_delivery_report(order_ids: str, days: int = 30) -> dict[str, Any]:
    """Pull a delivery report from GAM for comma-separated numeric order IDs."""
    _require_gam_configured()
    ids = [oid.strip() for oid in order_ids.split(",") if oid.strip()]
    if not ids:
        raise HTTPException(
            status_code=400,
            detail="order_ids must be a comma-separated list of numeric GAM order IDs",
        )
    try:
        from ..clients.gam_soap_client import GAMSoapClient

        client = GAMSoapClient()
        client.connect()
        report = client.get_delivery_report(ids, days=days)
        client.disconnect()
        return report
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
