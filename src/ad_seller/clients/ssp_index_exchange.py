# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Index Exchange SSP client (REST API).

Index Exchange exposes its deal management REST API at
https://app.indexexchange.com/api/deals, with all deal endpoints under a
/v3/deals prefix. No MCP server yet (as of March 2026), though they're part
of the IAB Tech Lab Agentic RTB Framework (ARTF) coalition.

Key endpoints (base URL + path):
  - POST   /v3/deals              — Create a deal
  - GET    /v3/deals               — List deals
  - GET    /v3/deals/{id}          — Get a single deal (id = internalDealID)
  - PATCH  /v3/deals/{id}          — Update a deal (requires If-Match ETag)
  - DELETE /v3/deals/{id}          — Soft-delete a deal
  - GET    /v3/deals/reports       — Deals data export

There is no clone/copy endpoint — see clone_deal() below.

Field names and behavior here are derived directly from deals-web-api (the
Go service implementing /v3/deals), not from public docs, per the DEALS-7818
gap analysis.

Known limitations:
  - directConfigurations.dspID (required for Direct deal types: PMP, PG,
    PREFERRED) has no source anywhere in seller-agent today. create_deal()
    will raise ValueError for these deal types until request.dsp_id is
    supplied by the caller — there is no per-deal "target DSP" concept in
    the current deal/flow model to derive it from.
  - No Keycloak JWT refresh: INDEX_EXCHANGE_API_KEY is treated as a
    long-lived token; production tokens expire and must be refreshed via
    the client-credentials grant, which is not implemented here.
  - The new deals-web-api `floorCurrency` field (create-only, feature-flagged)
    is not supported; all deals are assumed USD.
"""

import logging
from typing import Any, Optional

from .ssp_base import (
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPDealType,
    SSPTroubleshootResult,
    SSPType,
)
from .ssp_rest_client import RESTSSPClient

logger = logging.getLogger(__name__)


def _ix_deal_config(deal_type: SSPDealType) -> tuple[int, str, bool]:
    """Map an SSPDealType to Index Exchange's classID/auctionType/programmaticGuaranteed.

    IX classID values: 1=Direct Deal, 3=Inventory Package, 4=Marketplace
    Package, 5=Deal with Marketplaces. Only 1 and 4 are reachable from the
    deal types seller-agent currently models.
    """
    if deal_type == SSPDealType.PG:
        return 1, "fixed", True
    if deal_type == SSPDealType.PMP:
        return 1, "first", False
    if deal_type == SSPDealType.PREFERRED:
        return 1, "fixed", False
    if deal_type == SSPDealType.AUCTION_PACKAGE:
        return 4, "first", False
    raise ValueError(f"Unsupported Index Exchange deal type: {deal_type}")


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
    - API path structure (/v3/deals under the configured base URL)
    - Request format (JSON body with IX's actual /v3/deals field names)
    - Response parsing (IX deal objects → normalized SSPDeal)
    - classID/auctionType/programmaticGuaranteed deal type mapping

    Config:
        INDEX_EXCHANGE_API_URL=https://app.indexexchange.com/api/deals
        INDEX_EXCHANGE_API_KEY=<keycloak-jwt-bearer-token>
        INDEX_EXCHANGE_ACCOUNT_ID=<publisher account.accountID>  (optional;
            can instead be supplied per-request via SSPDealCreateRequest.account_id)
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> None:
        super().__init__(
            ssp_type=SSPType.INDEX_EXCHANGE,
            ssp_name="Index Exchange",
            base_url=base_url,
            api_key=api_key,
            auth_header="Authorization",
            auth_prefix="Bearer",
        )
        self._account_id = account_id

    # --- Override deal operations with IX-specific paths ---

    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Create a deal on Index Exchange via POST /v3/deals."""
        http = self._ensure_connected()

        account_id = request.account_id if request.account_id is not None else self._account_id
        if not request.external_deal_id:
            raise ValueError(
                "Index Exchange requires external_deal_id (SSPDealCreateRequest.external_deal_id) "
                "to create a deal"
            )
        if account_id is None:
            raise ValueError(
                "Index Exchange requires an account_id — set SSPDealCreateRequest.account_id "
                "or configure INDEX_EXCHANGE_ACCOUNT_ID"
            )

        class_id, auction_type, programmatic_guaranteed = _ix_deal_config(request.deal_type)
        if request.dsp_id is None:
            raise ValueError(
                f"Index Exchange requires dsp_id (SSPDealCreateRequest.dsp_id) for "
                f"deal_type={request.deal_type.value}"
            )

        body: dict[str, Any] = {
            "classID": class_id,
            "name": request.name,
            "externalDealID": request.external_deal_id,
            "account": {"accountID": account_id},
            "auctionType": auction_type,
            "floor": request.cpm,
        }
        if request.start_date:
            body["startDate"] = request.start_date
        if request.end_date:
            body["endDate"] = request.end_date

        if class_id == 1:
            direct_config: dict[str, Any] = {
                "dspID": request.dsp_id,
                "programmaticGuaranteed": programmatic_guaranteed,
            }
            if request.buyer_seat_ids:
                direct_config["seatIDs"] = request.buyer_seat_ids
            if request.impressions_goal:
                direct_config["impressionGoal"] = request.impressions_goal
            body["directConfigurations"] = direct_config
        else:
            # classID 4 (Marketplace Package) requires marketplaceConfigurations
            # instead of directConfigurations; only dspID is confirmed required.
            body["marketplaceConfigurations"] = {"dspID": request.dsp_id}

        if request.advertiser:
            body["labels"] = {"advertiser": request.advertiser}
        if request.targeting:
            body["targeting"] = request.targeting

        resp = await http.post("/v3/deals", json=body)
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
        """Get deal details from Index Exchange.

        deal_id must be the internalDealID (IX's integer database key), not
        the externalDealID.
        """
        http = self._ensure_connected()

        resp = await http.get(f"/v3/deals/{deal_id}")
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
        """Update a deal on Index Exchange via PATCH /v3/deals/{internalDealID}.

        deal_id must be the internalDealID (not externalDealID). IX requires
        an If-Match header carrying the deal's current ETag, obtained via a
        preceding GET. `updates["status"]`, if present, must be "active" or
        "paused" (not "ACTIVE"/"PAUSED") — expired/auto-paused are system-set
        and cannot be patched.
        """
        http = self._ensure_connected()

        get_resp = await http.get(f"/v3/deals/{deal_id}")
        get_resp.raise_for_status()
        etag = get_resp.headers.get("ETag")
        if not etag:
            raise ValueError(
                f"Index Exchange did not return an ETag for deal {deal_id}; cannot PATCH"
            )

        resp = await http.patch(
            f"/v3/deals/{deal_id}",
            json=updates,
            headers={"If-Match": etag},
        )
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
