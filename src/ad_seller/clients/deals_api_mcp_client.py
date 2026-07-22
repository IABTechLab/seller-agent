# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal-sync connector for IAB deals-api-mcp (HTTP Streamable MCP transport).

Connects to a running deals-api-mcp server via MCP Streamable HTTP and maps
the IAB Deal Sync API v1.0 tool schema to the DealSyncClient interface.

deals-api-mcp tools used:
  - deals_create:  create a new deal (required: name, origin, seller, dealFloor, startDate)
  - deals_status:  get deal + all buyer seat statuses + history
  - deals_list:    list deals with optional status filter
  - deals_update:  update mutable deal fields (blocked after deals_send)
  - deals_pause:   pause an active deal (propagates to provider)
  - deals_resume:  resume a paused deal (propagates to provider)

Note on deal IDs:
  SSPDeal.deal_id holds the internal UUID returned by deals-api-mcp (deal.id).
  All MCP tools validate dealId as z.string().uuid() — they require this UUID.
  SSPDeal.external_deal_id holds the OpenRTB / IAB deal ID (externalDealId,
  e.g. "IAB-...") used for DSP activation.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from .deal_sync_base import DealSyncClient, DealSyncProvider
from .freewheel_mcp_client import FreeWheelMCPClient
from .ssp_base import (
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPDealType,
    SSPTroubleshootResult,
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

# Reverse: SSPDealStatus → sellerStatus integer (for deals_list filter)
_STATUS_TO_SELLER_INT: dict[SSPDealStatus, int] = {v: k for k, v in _SELLER_STATUS_MAP.items()}

_SELLER_STATUS_LABEL: dict[int, str] = {
    0: "Active",
    1: "Paused",
    2: "Pending",
    4: "Complete",
    5: "Archived",
}


class DealsAPIMCPClient(DealSyncClient):
    """Deal-sync connector for deals-api-mcp via MCP Streamable HTTP.

    Wraps FreeWheelMCPClient for transport and maps structured IAB tool
    arguments to/from the DealSyncClient interface.

    deals-api-mcp's TypeScript MCP SDK sets _initialized = true on the first
    session and never resets it (close() does not clear the flag), so the server
    supports exactly ONE session per process lifetime. To satisfy this constraint
    we keep a class-level persistent background task that holds the MCP session
    open for the entire process. __aenter__ starts it on first call and reuses
    it on every subsequent call; __aexit__ is a no-op so the session is never
    torn down between requests.
    """

    provider = DealSyncProvider.DEALS_API_MCP
    provider_name: str = "IAB Deals MCP"
    ssp_name: str = "IAB Deals MCP"  # feeds SSPDeal.ssp_name in _parse_deal

    # ── Class-level persistent session ─────────────────────────────────────
    _shared_mcp: ClassVar[Optional[FreeWheelMCPClient]] = None
    _session_task: ClassVar[Optional[asyncio.Task]] = None
    _session_ready: ClassVar[Optional[asyncio.Event]] = None
    _session_done: ClassVar[Optional[asyncio.Event]] = None
    _session_error: ClassVar[Optional[Exception]] = None
    _session_url: ClassVar[Optional[str]] = None
    _session_lock: ClassVar[Optional[asyncio.Lock]] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._session_lock is None:
            cls._session_lock = asyncio.Lock()
        return cls._session_lock

    def __init__(
        self,
        *,
        mcp_url: str,
        api_key: Optional[str] = None,
        seller_origin: str = "publisher.example.com",
    ) -> None:
        self.ssp_name = "IAB Deals MCP"
        self._mcp_url = mcp_url
        self._api_key = api_key
        self._seller_origin = seller_origin
        self._mcp_client = FreeWheelMCPClient()  # replaced with shared client in __aenter__

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        auth_params = {"api_key": self._api_key} if self._api_key else None
        await self._mcp_client.connect(url=self._mcp_url, auth_params=auth_params)
        logger.info("Connected to deals-api-mcp at %s", self._mcp_url)

    async def disconnect(self) -> None:
        await self._mcp_client.disconnect()

    async def __aenter__(self) -> "DealsAPIMCPClient":
        """Ensure the persistent class-level MCP session is running, then wire
        self._mcp_client to it. Satisfies anyio's cancel-scope invariant by
        running the full streamablehttp_client lifecycle inside a single
        background asyncio Task.
        """
        cls = DealsAPIMCPClient
        async with self._get_lock():
            session_alive = (
                cls._session_task is not None
                and not cls._session_task.done()
                and cls._session_url == self._mcp_url
                and cls._shared_mcp is not None
                and cls._shared_mcp._connected
            )
            if not session_alive:
                await self._start_shared_session()

        if cls._session_error:
            raise cls._session_error

        self._mcp_client = cls._shared_mcp  # type: ignore[assignment]
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        # Session is persistent — do not disconnect between requests.
        # deals-api-mcp's TypeScript MCP SDK will reject a second initialize
        # for the lifetime of the server process after any session termination.
        pass

    async def _start_shared_session(self) -> None:
        """Start a new background task that holds the shared MCP session open."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        cls = DealsAPIMCPClient
        cls._session_ready = asyncio.Event()
        cls._session_done = asyncio.Event()
        cls._session_error = None
        cls._shared_mcp = FreeWheelMCPClient()
        cls._session_url = self._mcp_url

        mcp_url = self._mcp_url
        api_key = self._api_key
        shared_mcp = cls._shared_mcp
        ready = cls._session_ready
        done = cls._session_done

        async def _run_session() -> None:
            try:
                headers = {"x-api-key": api_key} if api_key else None
                async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        shared_mcp._session = session
                        shared_mcp._connected = True
                        ready.set()
                        logger.info("Persistent MCP session established at %s", mcp_url)
                        await done.wait()  # holds connection open indefinitely
            except Exception as exc:
                cls._session_error = exc
                if not ready.is_set():
                    ready.set()
            finally:
                shared_mcp._connected = False
                shared_mcp._session = None

        cls._session_task = asyncio.create_task(_run_session())
        await cls._session_ready.wait()

    # ── Deal Operations ────────────────────────────────────────────────────

    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Map SSPDealCreateRequest → deals_create structured args."""
        cpm = getattr(request, "cpm", None)
        if cpm is None:
            raise ValueError("cpm is required to create a deal via deals-api-mcp")

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        deal_type = getattr(request, "deal_type", None)

        args: dict[str, Any] = {
            "name": getattr(request, "name", None) or "Untitled Deal",
            "origin": self._seller_origin,
            "seller": self._seller_origin,
            "dealFloor": cpm,
            "startDate": getattr(request, "start_date", None) or now_iso,
        }

        # PG deals must be flagged as guaranteed
        if deal_type == SSPDealType.PG:
            args["guar"] = 1

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
        deal = self._parse_deal(raw)
        # deals-api-mcp has no dealType concept — echo back the requested type
        # so callers aren't silently told every deal is PMP.
        if deal_type:
            deal = deal.model_copy(update={"deal_type": deal_type})
        return deal

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
        if status is not None and status in _STATUS_TO_SELLER_INT:
            args["sellerStatus"] = _STATUS_TO_SELLER_INT[status]
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
        overrides = overrides or {}

        # Never invent a floor — same rule as create_deal.
        floor = overrides.get("dealFloor", terms.get("dealFloor"))
        if floor is None:
            raise ValueError("dealFloor is required to clone a deal via deals-api-mcp")

        args: dict[str, Any] = {
            "name": f"Copy of {source_deal.get('name', source_deal_id)}",
            "origin": self._seller_origin,
            "seller": source_deal.get("seller", self.ssp_name),
            "dealFloor": floor,
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
            # deals_status returns labeled summaries under raw["status"]["buyerStatuses"]
            # (not raw["buyerSeats"]) — each entry has "status" (label) and "seatId"
            status_block = raw.get("status", {})
            for seat in status_block.get("buyerStatuses", []):
                if isinstance(seat, dict) and seat.get("status") == "Rejected":
                    issues.append(f"Buyer seat {seat.get('seatId', '?')} was rejected by provider")

        return SSPTroubleshootResult(
            deal_id=deal_id,
            status=self._seller_status_label(raw),
            primary_issues=issues,
            raw=raw,
        )

    # ── Response Parsing ───────────────────────────────────────────────────

    def _parse_deal(self, raw: Any) -> SSPDeal:
        """Parse a deals-api-mcp tool response into SSPDeal."""
        if not isinstance(raw, dict):
            return SSPDeal(deal_id="unknown", ssp_name=self.ssp_name)

        # deals_create: {"success": true, "deal": {...}}
        # deals_status: {"success": true, "deal": {...}, "status": {...}}
        deal = raw.get("deal", raw)
        if not isinstance(deal, dict):
            deal = raw

        terms = deal.get("terms", {}) if isinstance(deal.get("terms"), dict) else {}
        seller_status_int = deal.get("sellerStatus")

        return SSPDeal(
            # Internal UUID is what all MCP tools accept (z.string().uuid()).
            # external_deal_id is the OpenRTB / IAB ID for DSP activation.
            deal_id=str(deal.get("id", "unknown")),
            external_deal_id=deal.get("externalDealId"),
            name=deal.get("name"),
            status=_SELLER_STATUS_MAP.get(seller_status_int, SSPDealStatus.CREATED),
            cpm=terms.get("dealFloor"),
            currency=terms.get("currency", "USD"),
            ssp_name=self.ssp_name,
            raw=raw,
        )

    def _seller_status_label(self, raw: Any) -> str:
        if isinstance(raw, dict):
            # deals_status provides a pre-labeled status block — prefer it
            status_block = raw.get("status", {})
            if isinstance(status_block, dict) and status_block.get("sellerStatus"):
                return status_block["sellerStatus"]
            deal = raw.get("deal", raw)
            if isinstance(deal, dict):
                code = deal.get("sellerStatus")
                return _SELLER_STATUS_LABEL.get(code, "unknown")
        return "unknown"
