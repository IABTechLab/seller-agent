# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Index Exchange SSP client (REST API).

Index Exchange's REST API lives at the FQDN https://app.indexexchange.com.
Deal-management endpoints (this connector's concern) sit under a shared
/api/deals prefix, with /v3/deals beneath that for deal CRUD operations.
INDEX_EXCHANGE_API_URL is configured as just the FQDN — the /api/deals
prefix is applied in code (_API_BASE_PATH below) rather than baked into the
configured base URL, since /api/deals is only one of several path prefixes
under this host we expect to call as seller-agent's agentic workflows
expand. No MCP server yet (as of March 2026), though they're part of the
IAB Tech Lab Agentic RTB Framework (ARTF) coalition.

Key endpoints (FQDN + path):
  - POST   /api/deals/v3/deals              — Create a deal
  - GET    /api/deals/v3/deals               — List deals
  - GET    /api/deals/v3/deals/{id}          — Get a single deal (id = internalDealID)
  - PATCH  /api/deals/v3/deals/{id}          — Update a deal (requires If-Match ETag)
  - DELETE /api/deals/v3/deals/{id}          — Soft-delete a deal
  - GET    /api/deals/v3/deals/reports       — Deals data export
  - GET    /api/deals/v1/dsps/-/seats        — Resolve dspID(s) from seat IDs
    (see resolve_dsp_ids_for_seat_ids() below)

There is no clone/copy endpoint — clone_deal() below implements cloning by
composing get_deal() + create_deal() instead.

Field names and behavior here are derived directly from deals-web-api (the
Go service implementing /v3/deals), not from public docs.

Known limitations:
  - dspID (directConfigurations.dspID for Direct deal types, or
    marketplaceConfigurations.dspID for Marketplace Package) is required for
    every deal type this connector supports, not just Direct. create_deal()
    will raise ValueError until request.dsp_id is supplied. Callers with
    only a buyer_seat_id (no dsp_id) should call
    resolve_dsp_ids_for_seat_ids() first — see distribute_deal_via_ssp() in
    interfaces/api/main.py for how ambiguous (>1 dspID) resolutions are
    gated behind human approval before create_deal() is called.
  - account.accountID is likewise not sourced anywhere upstream today —
    it's deliberately not a fixed deployment-wide setting, since a single
    seller-agent instance may need to create deals under more than one IX
    account, so it must be supplied per-request via
    SSPDealCreateRequest.account_id until callers are wired up to provide it.
  - No Keycloak JWT refresh: INDEX_EXCHANGE_API_KEY is treated as a
    long-lived token; production tokens expire and must be refreshed via
    the client-credentials grant, which is not implemented here.
  - The new deals-web-api `floorCurrency` field (create-only, feature-flagged)
    is not supported; all deals are assumed USD.
"""

import logging
import uuid
from typing import Any, Optional

from pydantic import BaseModel

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

# Shared prefix for all deal-management endpoints under the configured FQDN.
# Kept out of the configured base URL so other IX path prefixes (e.g. a
# planned /v1/seats DSP-resolution route) can be added without requiring a
# reconfigured base URL per prefix.
_API_BASE_PATH = "/api/deals"

# externalDealID: 3-64 chars, letters/numbers/dashes/underscores/periods, and
# per deals-web-api's validation cannot start with "0".
_EXTERNAL_DEAL_ID_MAX_LEN = 64


def _generate_clone_external_deal_id(source_external_deal_id: str) -> str:
    """Derive a new externalDealID for a clone, guaranteed to fit IX's 64-char limit.

    Appends a random suffix to the source deal's externalDealID (which is
    already known-valid — it belongs to an existing deal) so the clone gets
    a distinct ID, truncating the base as needed so the combined length
    never exceeds IX's max. The source ID's leading character is preserved,
    so the "cannot start with 0" rule stays satisfied.
    """
    suffix = f"-clone-{uuid.uuid4().hex[:8]}"
    base = source_external_deal_id[: _EXTERNAL_DEAL_ID_MAX_LEN - len(suffix)]
    return f"{base}{suffix}"


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


class DspSeatMatch(BaseModel):
    """A single seat/DSP match from resolve_dsp_ids_for_seat_ids().

    A given buyer_seat_id (DSP-facing, alphanumeric) can resolve to more
    than one dspID — collisions are rare but not impossible (empirically
    verified against demand-data-api's rtbTradingDeskExtendedIDs table) —
    so callers must handle zero, one, or multiple matches explicitly rather
    than assuming a 1:1 mapping.
    """

    dsp_id: int
    extended_seat_id: str = ""
    buyer_name: str = ""
    status: str = ""


class IndexExchangeSSPClient(RESTSSPClient):
    """Index Exchange SSP client using their REST API.

    Extends RESTSSPClient with Index Exchange-specific:
    - API path structure (/api/deals/v3/deals under the configured FQDN)
    - Request format (JSON body with IX's actual /v3/deals field names)
    - Response parsing (IX deal objects → normalized SSPDeal)
    - classID/auctionType/programmaticGuaranteed deal type mapping

    Config:
        INDEX_EXCHANGE_API_URL=https://app.indexexchange.com   (FQDN only —
            see _API_BASE_PATH for why the /api/deals prefix isn't baked in here)
        INDEX_EXCHANGE_API_KEY=<keycloak-jwt-bearer-token>

    account.accountID is not a fixed deployment-wide setting — a single
    seller-agent instance may need to create deals under more than one IX
    account — so it's only ever taken from SSPDealCreateRequest.account_id,
    per-request, same as dsp_id.
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
        """Create a deal on Index Exchange via POST /v3/deals."""
        http = self._ensure_connected()

        if not request.external_deal_id:
            raise ValueError(
                "Index Exchange requires external_deal_id (SSPDealCreateRequest.external_deal_id) "
                "to create a deal"
            )
        if request.account_id is None:
            raise ValueError(
                "Index Exchange requires account_id (SSPDealCreateRequest.account_id) "
                "to create a deal"
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
            "account": {"accountID": request.account_id},
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

        resp = await http.post(f"{_API_BASE_PATH}/v3/deals", json=body)
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def resolve_dsp_ids_for_seat_ids(self, seat_ids: list[str]) -> list[DspSeatMatch]:
        """Resolve dspID(s) for the given buyer seat IDs.

        Calls GET /v1/dsps/-/seats?seatIDs=... — deals-web-api's wildcard
        dspID lookup, which searches across all DSPs for seats matching the
        given (DSP-facing, alphanumeric) seat IDs. Only active seats are
        returned; a deleted/paused trading desk is never a valid dsp_id to
        create a new deal against.

        Returns an empty list if none matched, or more than one DspSeatMatch
        if the seat ID(s) collide across multiple DSPs — callers must decide
        how to handle that (e.g. gate on human approval) rather than
        assuming the first match is correct.
        """
        if not seat_ids:
            return []

        http = self._ensure_connected()
        resp = await http.get(
            f"{_API_BASE_PATH}/v1/dsps/-/seats",
            params={"seatIDs": ",".join(seat_ids)},
        )
        resp.raise_for_status()

        return [
            DspSeatMatch(
                dsp_id=seat["dspID"],
                extended_seat_id=seat.get("extendedSeatID", ""),
                buyer_name=seat.get("name", ""),
                status=seat.get("status", ""),
            )
            for seat in resp.json().get("seats", [])
            if seat.get("status") == "A"
        ]

    async def clone_deal(
        self,
        source_deal_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SSPDeal:
        """Clone a deal by composing get_deal() + create_deal().

        Index Exchange has no `/{id}/copy` (or equivalent) route on
        /v3/deals, so this retrieves the source deal — source_deal_id must
        be the internalDealID, same convention as get_deal()/update_deal()
        — and creates a new one with a freshly generated external_deal_id,
        copying over its configuration. account_id and dsp_id are read back
        out of the source deal's raw response, since SSPDeal doesn't carry
        them as first-class fields. `overrides` may override any
        SSPDealCreateRequest field, e.g. a specific external_deal_id or a
        different account_id/dsp_id.
        """
        source = await self.get_deal(source_deal_id)
        raw = source.raw or {}
        account = raw.get("account") or {}
        direct_config = raw.get("directConfigurations") or {}
        marketplace_config = raw.get("marketplaceConfigurations") or {}

        create_request = SSPDealCreateRequest(
            deal_type=source.deal_type,
            name=source.name,
            advertiser=source.advertiser,
            cpm=source.cpm,
            start_date=source.start_date,
            end_date=source.end_date,
            targeting=source.targeting,
            impressions_goal=source.impressions_goal,
            buyer_seat_ids=direct_config.get("seatIDs", []),
            external_deal_id=_generate_clone_external_deal_id(source.deal_id),
            account_id=account.get("accountID"),
            dsp_id=direct_config.get("dspID") or marketplace_config.get("dspID"),
        )
        if overrides:
            create_request = create_request.model_copy(update=overrides)

        return await self.create_deal(create_request)

    async def get_deal(self, deal_id: str) -> SSPDeal:
        """Get deal details from Index Exchange.

        deal_id must be the internalDealID (IX's integer database key), not
        the externalDealID.
        """
        http = self._ensure_connected()

        resp = await http.get(f"{_API_BASE_PATH}/v3/deals/{deal_id}")
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

        resp = await http.get(f"{_API_BASE_PATH}/v3/deals", params=params)
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

        get_resp = await http.get(f"{_API_BASE_PATH}/v3/deals/{deal_id}")
        get_resp.raise_for_status()
        etag = get_resp.headers.get("ETag")
        if not etag:
            raise ValueError(
                f"Index Exchange did not return an ETag for deal {deal_id}; cannot PATCH"
            )

        resp = await http.patch(
            f"{_API_BASE_PATH}/v3/deals/{deal_id}",
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
            resp = await http.get(f"{_API_BASE_PATH}/v3/deals/{deal_id}")
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

        # Status-based heuristic — revisit once deal performance metrics are
        # surfaced through GraphQL, which will allow more accurate scoring.
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
            issues.append("Deal was automatically paused by Index Exchange due to inactivity")
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
