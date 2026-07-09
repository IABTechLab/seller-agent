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
Go service implementing /v3/deals), not from public docs.

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
    "auto-paused": SSPDealStatus.PAUSED,
}

# GET /v3/deals `status` filter values — IX has no equivalent for CREATED or
# ARCHIVED, so those send no status filter (unfiltered list).
_IX_LIST_STATUS_MAP = {
    SSPDealStatus.ACTIVE: "active",
    SSPDealStatus.PAUSED: "paused",
    SSPDealStatus.EXPIRED: "expired",
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
        """Index Exchange has no clone/copy endpoint.

        There is no `/{id}/copy` (or equivalent) route on /v3/deals. To
        duplicate a deal, callers must retrieve it with get_deal() and then
        create_deal() a new one with a fresh external_deal_id, copying over
        whatever fields should carry forward.
        """
        raise NotImplementedError(
            "Index Exchange has no deal clone endpoint. Use get_deal() to "
            "retrieve the source deal, then create_deal() with a new "
            "external_deal_id to duplicate it."
        )

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
        """List deals from Index Exchange via GET /v3/deals."""
        http = self._ensure_connected()

        params: dict[str, Any] = {"pageOffset": 0, "pageSize": limit}
        ix_status = _IX_LIST_STATUS_MAP.get(status) if status else None
        if ix_status:
            params["status"] = ix_status

        resp = await http.get("/v3/deals", params=params)
        resp.raise_for_status()

        data = resp.json()
        return [self._parse_deal(d) for d in data.get("deals", [])]

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
        """Diagnose a deal via GET /v3/deals/{internalDealID}.

        Index Exchange has no dedicated troubleshooting endpoint (unlike
        PubMatic's MCP), so health is derived from the deal's own status and
        configuration rather than a reporting API.
        """
        http = self._ensure_connected()

        try:
            resp = await http.get(f"/v3/deals/{deal_id}")
            resp.raise_for_status()
            deal_data = resp.json()
        except Exception as exc:
            return SSPTroubleshootResult(
                deal_id=deal_id,
                health_score=0,
                status="unreachable",
                primary_issues=[f"Failed to fetch deal from Index Exchange: {exc}"],
                ssp_type=self.ssp_type,
            )

        status_str = str(deal_data.get("status", "")).lower()
        issues: list[str] = []
        recommendations: list[dict[str, str]] = []

        if status_str == "active":
            health_score: Optional[int] = 90
        elif status_str == "paused":
            health_score = 50
            issues.append("Deal is paused")
            recommendations.append(
                {"action": "Resume the deal via PATCH if pausing was unintentional"}
            )
        elif status_str == "auto-paused":
            health_score = 20
            issues.append("Deal was automatically paused by Index Exchange")
            recommendations.append(
                {
                    "action": "Review deal configuration (floor, targeting, budget) "
                    "that may have triggered the system pause"
                }
            )
        elif status_str == "expired":
            health_score = 0
            issues.append("Deal has expired")
        else:
            health_score = None

        class_id = deal_data.get("classID")
        direct_config = deal_data.get("directConfigurations") or {}
        if class_id == 1 and not direct_config.get("seatIDs"):
            issues.append("No buyer seat IDs are configured")
        if not deal_data.get("floor"):
            issues.append("Floor price is not set")

        return SSPTroubleshootResult(
            deal_id=str(deal_data.get("externalDealID", deal_id)),
            health_score=health_score,
            status=status_str or "unknown",
            primary_issues=issues,
            recommendations=recommendations,
            ssp_type=self.ssp_type,
            raw=deal_data,
        )

    # --- Index Exchange-specific response parsing ---

    def _parse_deal(self, raw: dict[str, Any]) -> SSPDeal:
        """Parse an Index Exchange /v3/deals response into a normalized SSPDeal."""
        status_str = str(raw.get("status", "")).lower()
        class_id = raw.get("classID")
        direct_config = raw.get("directConfigurations") or {}
        labels = raw.get("labels") or {}

        deal_type = SSPDealType.PMP
        if class_id == 1:
            if direct_config.get("programmaticGuaranteed"):
                deal_type = SSPDealType.PG
            elif raw.get("auctionType") == "fixed":
                deal_type = SSPDealType.PREFERRED
            else:
                deal_type = SSPDealType.PMP
        elif class_id == 4:
            deal_type = SSPDealType.AUCTION_PACKAGE

        return SSPDeal(
            deal_id=str(raw.get("externalDealID", "unknown")),
            name=raw.get("name"),
            deal_type=deal_type,
            status=_IX_STATUS_MAP.get(status_str, SSPDealStatus.CREATED),
            advertiser=labels.get("advertiser"),
            cpm=raw.get("floor"),
            currency="USD",
            start_date=raw.get("startDate"),
            end_date=raw.get("endDate"),
            targeting=raw.get("targeting"),
            impressions_goal=direct_config.get("impressionGoal"),
            ssp_type=SSPType.INDEX_EXCHANGE,
            ssp_name="Index Exchange",
            raw=raw,
        )
