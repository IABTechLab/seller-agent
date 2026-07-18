# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SSP client for IAB deals-api-mcp (HTTP Streamable MCP transport).

Connects to a running deals-api-mcp server via MCP Streamable HTTP and maps
the IAB Deal Sync API v1.0 tool schema to the generic SSPClient interface.

deals-api-mcp tools used:
  - deals_create:  create a new deal (required: name, origin, seller, dealFloor, startDate)
  - deals_status:  get deal + all buyer seat statuses + history
  - deals_list:    list deals with optional status filter
  - deals_update:  update mutable deal fields (blocked after deals_send)
  - deals_pause:   pause an active deal (propagates to provider)
  - deals_resume:  resume a paused deal (propagates to provider)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .freewheel_mcp_client import FreeWheelMCPClient
from .ssp_base import (
    SSPClient,
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPTroubleshootResult,
    SSPType,
)

logger = logging.getLogger(__name__)

# deals-api-mcp sellerStatus integer → SSPDealStatus
# SellerStatus enum: 0=Active, 1=Paused, 2=Pending, 4=Complete, 5=Archived
_SELLER_STATUS_MAP: dict[int, SSPDealStatus] = {
    0: SSPDealStatus.ACTIVE,
    1: SSPDealStatus.PAUSED,
    2: SSPDealStatus.CREATED,
    4: SSPDealStatus.EXPIRED,
    5: SSPDealStatus.ARCHIVED,
}

_SELLER_STATUS_LABEL: dict[int, str] = {
    0: "Active",
    1: "Paused",
    2: "Pending",
    4: "Complete",
    5: "Archived",
}


class DealsAPIMCPClient(SSPClient):
    """SSP connector for deals-api-mcp via MCP Streamable HTTP.

    Wraps FreeWheelMCPClient for transport and maps structured IAB tool
    arguments to/from the generic SSPClient interface.
    """

    ssp_type: SSPType = SSPType.CUSTOM
    ssp_name: str = "IAB Deals MCP"

    def __init__(
        self,
        *,
        mcp_url: str,
        api_key: Optional[str] = None,
        seller_origin: str = "publisher.example.com",
    ) -> None:
        self.ssp_type = SSPType.CUSTOM
        self.ssp_name = "IAB Deals MCP"
        self._mcp_url = mcp_url
        self._api_key = api_key
        self._seller_origin = seller_origin
        self._mcp_client = FreeWheelMCPClient()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        auth_params = {"api_key": self._api_key} if self._api_key else None
        await self._mcp_client.connect(
            url=self._mcp_url,
            auth_params=auth_params,
        )
        logger.info("Connected to deals-api-mcp at %s", self._mcp_url)

    async def disconnect(self) -> None:
        await self._mcp_client.disconnect()

    # ── Deal Operations ────────────────────────────────────────────────────

    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Map SSPDealCreateRequest → deals_create structured args."""
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        args: dict[str, Any] = {
            "name": getattr(request, "name", None) or "Untitled Deal",
            "origin": self._seller_origin,
            "seller": getattr(request, "advertiser", None) or self.ssp_name,
            "dealFloor": getattr(request, "cpm", None) or 1.0,
            "startDate": getattr(request, "start_date", None) or now_iso,
        }

        if getattr(request, "end_date", None):
            args["endDate"] = request.end_date
        if getattr(request, "impressions_goal", None):
            args["units"] = request.impressions_goal
        if getattr(request, "buyer_seat_ids", None):
            args["wseat"] = request.buyer_seat_ids
        if getattr(request, "description", None):
            args["description"] = request.description
        if getattr(request, "currency", None):
            args["currency"] = request.currency

        raw = await self._mcp_client.call_tool("deals_create", args)
        return self._parse_deal(raw)

    async def get_deal(self, deal_id: str) -> SSPDeal:
        raw = await self._mcp_client.call_tool("deals_status", {"dealId": deal_id})
        return self._parse_deal(raw)

    async def list_deals(
        self,
        *,
        status: Optional[SSPDealStatus] = None,
        limit: int = 100,
    ) -> list[SSPDeal]:
        args: dict[str, Any] = {"pageSize": min(limit, 100)}
        raw = await self._mcp_client.call_tool("deals_list", args)
        if isinstance(raw, dict):
            items = raw.get("deals", raw.get("items", []))
            return [self._parse_deal({"deal": d}) for d in items]
        return []

    async def clone_deal(
        self,
        source_deal_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SSPDeal:
        """Clone by fetching the source deal and creating a new one with overrides."""
        source_raw = await self._mcp_client.call_tool("deals_status", {"dealId": source_deal_id})

        # Build create args from source deal's terms, apply overrides
        source_deal = source_raw.get("deal", {}) if isinstance(source_raw, dict) else {}
        terms = source_deal.get("terms", {}) if isinstance(source_deal.get("terms"), dict) else {}
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        args: dict[str, Any] = {
            "name": f"Copy of {source_deal.get('name', source_deal_id)}",
            "origin": self._seller_origin,
            "seller": source_deal.get("seller", self.ssp_name),
            "dealFloor": terms.get("dealFloor", 1.0),
            "startDate": terms.get("startDate", now_iso),
        }
        if terms.get("endDate"):
            args["endDate"] = terms["endDate"]
        if overrides:
            args.update(overrides)

        raw = await self._mcp_client.call_tool("deals_create", args)
        return self._parse_deal(raw)

    async def update_deal(self, deal_id: str, updates: dict[str, Any]) -> SSPDeal:
        raw = await self._mcp_client.call_tool("deals_update", {"id": deal_id, **updates})
        return self._parse_deal(raw)

    async def troubleshoot_deal(self, deal_id: str) -> SSPTroubleshootResult:
        raw = await self._mcp_client.call_tool("deals_status", {"dealId": deal_id})

        issues: list[str] = []
        if isinstance(raw, dict):
            seats = raw.get("buyerSeats", [])
            for seat in seats:
                if isinstance(seat, dict) and seat.get("buyerStatusLabel") == "Rejected":
                    issues.append(f"Buyer seat {seat.get('seatId', '?')} was rejected by provider")

        return SSPTroubleshootResult(
            deal_id=deal_id,
            status=self._seller_status_label(raw),
            primary_issues=issues,
            ssp_type=self.ssp_type,
            raw=raw,
        )

    # ── Response Parsing ───────────────────────────────────────────────────

    def _parse_deal(self, raw: Any) -> SSPDeal:
        """Parse a deals-api-mcp tool response into SSPDeal."""
        if not isinstance(raw, dict):
            return SSPDeal(deal_id="unknown", ssp_type=self.ssp_type, ssp_name=self.ssp_name)

        # deals_create wraps in {"success": true, "deal": {...}}
        # deals_status wraps in {"deal": {...}, "buyerSeats": [...]}
        deal = raw.get("deal", raw)
        if not isinstance(deal, dict):
            deal = raw

        terms = deal.get("terms", {}) if isinstance(deal.get("terms"), dict) else {}
        seller_status_int = deal.get("sellerStatus")

        return SSPDeal(
            deal_id=str(deal.get("externalDealId", deal.get("id", "unknown"))),
            name=deal.get("name"),
            status=_SELLER_STATUS_MAP.get(seller_status_int, SSPDealStatus.CREATED),
            cpm=terms.get("dealFloor"),
            currency=terms.get("currency", "USD"),
            ssp_type=self.ssp_type,
            ssp_name=self.ssp_name,
            raw=raw,
        )

    def _seller_status_label(self, raw: Any) -> str:
        if isinstance(raw, dict):
            deal = raw.get("deal", raw)
            if isinstance(deal, dict):
                code = deal.get("sellerStatus")
                return _SELLER_STATUS_LABEL.get(code, "unknown")
        return "unknown"
