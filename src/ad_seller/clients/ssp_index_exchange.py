# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Index Exchange SSP client (REST API).

Index Exchange exposes a REST API for deal management at api.indexexchange.com.
No MCP server yet (as of March 2026), though they're part of the IAB Tech Lab
Agentic RTB Framework (ARTF) coalition.

Key endpoints:
  - POST /api/deals — Create PMP deal
  - GET /api/deals — List/get deals
  - GET /api/cal/v1/mappings/deals — Deal mapping (name, dates, IDs)
  - Reporting API for supply, demand, deals, brand data

Docs: https://api.indexexchange.com/reference
      https://kb.indexexchange.com/publishers/pmp/deals_api.htm

TODO(index-exchange-auth): Full API docs require IX account for detailed
request/response schemas and auth mechanism.
"""

import logging
from typing import Any, Optional

from .ssp_rest_client import RESTSSPClient
from .ssp_base import (
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPDealType,
    SSPTroubleshootResult,
    SSPType,
)

logger = logging.getLogger(__name__)


# Index Exchange deal type mapping
_IX_DEAL_TYPE_MAP = {
    SSPDealType.PMP: "pmp",
    SSPDealType.PG: "programmatic_guaranteed",
    SSPDealType.PREFERRED: "preferred",
}

_IX_STATUS_MAP = {
    "active": SSPDealStatus.ACTIVE,
    "paused": SSPDealStatus.PAUSED,
    "expired": SSPDealStatus.EXPIRED,
    "archived": SSPDealStatus.ARCHIVED,
    "pending": SSPDealStatus.CREATED,
}


class IndexExchangeSSPClient(RESTSSPClient):
    """Index Exchange SSP client using their REST API.

    Extends RESTSSPClient with Index Exchange-specific:
    - API path structure (/api/deals, /api/cal/v1/mappings/deals)
    - Request format (JSON body with IX-specific field names)
    - Response parsing (IX deal objects → normalized SSPDeal)
    - Deal type mapping

    Config:
        INDEX_EXCHANGE_API_URL=https://api.indexexchange.com
        INDEX_EXCHANGE_API_KEY=your-api-key
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        super().__init__(
            ssp_type=SSPType.INDEX_EXCHANGE,
            ssp_name="Index Exchange",
            base_url=base_url,
            api_key=api_key,
            auth_header="Authorization",
            auth_prefix="Bearer",
        )

    # --- Override deal operations with IX-specific paths ---

    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Create a PMP deal on Index Exchange."""
        http = self._ensure_connected()

        body: dict[str, Any] = {
            "deal_type": _IX_DEAL_TYPE_MAP.get(request.deal_type, "pmp"),
        }
        if request.name:
            body["deal_name"] = request.name
        if request.advertiser:
            body["advertiser_name"] = request.advertiser
        if request.cpm:
            body["floor_price"] = request.cpm
        if request.currency:
            body["currency"] = request.currency
        if request.start_date:
            body["start_date"] = request.start_date
        if request.end_date:
            body["end_date"] = request.end_date
        if request.buyer_seat_ids:
            body["buyer_seat_ids"] = request.buyer_seat_ids
        if request.impressions_goal:
            body["impression_goal"] = request.impressions_goal
        if request.targeting:
            body["targeting"] = request.targeting

        resp = await http.post("/api/deals", json=body)
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def clone_deal(
        self,
        source_deal_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SSPDeal:
        """Clone a deal on Index Exchange."""
        http = self._ensure_connected()

        body = {"source_deal_id": source_deal_id}
        if overrides:
            body.update(overrides)

        resp = await http.post(f"/api/deals/{source_deal_id}/copy", json=body)
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def get_deal(self, deal_id: str) -> SSPDeal:
        """Get deal details from Index Exchange."""
        http = self._ensure_connected()

        resp = await http.get(f"/api/deals/{deal_id}")
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def list_deals(
        self,
        *,
        status: Optional[SSPDealStatus] = None,
        limit: int = 100,
    ) -> list[SSPDeal]:
        """List deals from Index Exchange."""
        http = self._ensure_connected()

        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status.value

        resp = await http.get("/api/deals", params=params)
        resp.raise_for_status()

        data = resp.json()
        items = data if isinstance(data, list) else data.get("deals", data.get("data", []))
        return [self._parse_deal(d) for d in items]

    async def update_deal(
        self,
        deal_id: str,
        updates: dict[str, Any],
    ) -> SSPDeal:
        """Update a deal on Index Exchange."""
        http = self._ensure_connected()

        resp = await http.post(f"/api/deals/{deal_id}", json=updates)
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def troubleshoot_deal(self, deal_id: str) -> SSPTroubleshootResult:
        """Troubleshoot a deal on Index Exchange.

        Index Exchange doesn't have a dedicated troubleshooting endpoint
        (unlike PubMatic's MCP). We use the reporting API to pull deal
        performance data and flag issues.

        TODO: Integrate with IX Reporting API for real diagnostics.
        """
        http = self._ensure_connected()

        # Get deal details as a baseline
        try:
            resp = await http.get(f"/api/deals/{deal_id}")
            resp.raise_for_status()
            deal_data = resp.json()
        except Exception:
            deal_data = {}

        return SSPTroubleshootResult(
            deal_id=deal_id,
            status=deal_data.get("status", "unknown"),
            primary_issues=[],
            root_causes=[],
            recommendations=[
                {"action": "Check IX reporting dashboard for detailed deal diagnostics"},
            ],
            ssp_type=self.ssp_type,
            raw=deal_data,
        )

    # --- Index Exchange-specific response parsing ---

    def _parse_deal(self, raw: dict[str, Any]) -> SSPDeal:
        """Parse Index Exchange deal response to normalized SSPDeal."""
        status_str = str(raw.get("status", "pending")).lower()

        # IX may use different field names
        deal_type_raw = raw.get("deal_type", raw.get("type", "pmp"))
        deal_type = SSPDealType.PMP
        if deal_type_raw in ("pmp", "PMP"):
            deal_type = SSPDealType.PMP
        elif deal_type_raw in ("programmatic_guaranteed", "pg", "PG"):
            deal_type = SSPDealType.PG
        elif deal_type_raw in ("preferred", "preferred_deal"):
            deal_type = SSPDealType.PREFERRED

        return SSPDeal(
            deal_id=str(raw.get("deal_id", raw.get("id", "unknown"))),
            name=raw.get("deal_name", raw.get("name")),
            deal_type=deal_type,
            status=_IX_STATUS_MAP.get(status_str, SSPDealStatus.CREATED),
            advertiser=raw.get("advertiser_name", raw.get("advertiser")),
            cpm=raw.get("floor_price", raw.get("cpm")),
            currency=raw.get("currency", "USD"),
            start_date=raw.get("start_date"),
            end_date=raw.get("end_date"),
            targeting=raw.get("targeting"),
            impressions_goal=raw.get("impression_goal", raw.get("impressions_goal")),
            ssp_type=SSPType.INDEX_EXCHANGE,
            ssp_name="Index Exchange",
            raw=raw,
        )
