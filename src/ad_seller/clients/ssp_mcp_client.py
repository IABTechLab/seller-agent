# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP-based SSP client for SSPs that expose MCP servers.

First integration: PubMatic (mcp.pubmatic.com/sses)
Pattern: Any SSP with an MCP server can use this client.

PubMatic's MCP tools:
  - deal_management: create/clone deals via conversational interface
  - deal_troubleshooting: diagnose deal performance by deal_id
"""

import json
import logging
from typing import Any, Optional

from .freewheel_mcp_client import FreeWheelMCPClient, FreeWheelMCPError
from .ssp_base import (
    SSPClient,
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPDealType,
    SSPTroubleshootResult,
    SSPType,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Deal type mapping
# =============================================================================

_DEAL_TYPE_TO_SSP: dict[SSPDealType, str] = {
    SSPDealType.PMP: "PMP",
    SSPDealType.PG: "PG",
    SSPDealType.PREFERRED: "Preferred",
    SSPDealType.AUCTION_PACKAGE: "Auction Package",
}

_STATUS_MAP: dict[str, SSPDealStatus] = {
    "created": SSPDealStatus.CREATED,
    "active": SSPDealStatus.ACTIVE,
    "paused": SSPDealStatus.PAUSED,
    "expired": SSPDealStatus.EXPIRED,
    "archived": SSPDealStatus.ARCHIVED,
}


class MCPSSPClient(SSPClient):
    """SSP client that communicates via MCP protocol (JSON-RPC over SSE).

    Works with any SSP that exposes MCP tools for deal management.
    The tool names and response formats are configurable per SSP.

    Default tool names match PubMatic's MCP server:
      - deal_management: create/clone deals
      - deal_troubleshooting: diagnose deal issues
    """

    def __init__(
        self,
        *,
        ssp_type: SSPType = SSPType.CUSTOM,
        ssp_name: str = "MCP SSP",
        mcp_url: Optional[str] = None,
        api_key: Optional[str] = None,
        # Tool name overrides (different SSPs may name tools differently)
        deal_management_tool: str = "deal_management",
        deal_troubleshooting_tool: str = "deal_troubleshooting",
        deal_list_tool: Optional[str] = None,  # Not all SSPs have this
        deal_update_tool: Optional[str] = None,
    ) -> None:
        self.ssp_type = ssp_type
        self.ssp_name = ssp_name
        self._mcp_url = mcp_url
        self._api_key = api_key
        self._mcp_client = FreeWheelMCPClient()  # Reuse MCP client wrapper
        self._deal_management_tool = deal_management_tool
        self._deal_troubleshooting_tool = deal_troubleshooting_tool
        self._deal_list_tool = deal_list_tool
        self._deal_update_tool = deal_update_tool

    async def connect(self) -> None:
        """Connect to the SSP's MCP server."""
        if not self._mcp_url:
            raise ConnectionError(f"MCP URL not configured for {self.ssp_name}")

        auth_params = {}
        if self._api_key:
            auth_params["api_key"] = self._api_key

        await self._mcp_client.connect(
            url=self._mcp_url,
            auth_params=auth_params if auth_params else None,
        )
        logger.info("Connected to %s MCP at %s", self.ssp_name, self._mcp_url)

    async def disconnect(self) -> None:
        """Disconnect from the SSP's MCP server."""
        await self._mcp_client.disconnect()

    # --- Deal Operations ---

    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Create a deal via the SSP's MCP deal management tool.

        For conversational MCP tools (like PubMatic), we construct a
        structured query from the request parameters.
        """
        deal_type_str = _DEAL_TYPE_TO_SSP.get(request.deal_type, "PMP")

        # Build a structured query for the deal management tool
        parts = [f"Create a new {deal_type_str} deal"]
        if request.advertiser:
            parts.append(f"for advertiser {request.advertiser}")
        if request.cpm:
            parts.append(f"with a ${request.cpm} CPM")
        if request.priority:
            parts.append(f"and priority {request.priority}")
        if request.name:
            parts.append(f"named '{request.name}'")
        if request.start_date and request.end_date:
            parts.append(f"running from {request.start_date} to {request.end_date}")
        if request.buyer_seat_ids:
            parts.append(f"for buyer seats: {', '.join(request.buyer_seat_ids)}")
        if request.impressions_goal:
            parts.append(f"with {request.impressions_goal} impression goal")

        query = " ".join(parts)

        args: dict[str, Any] = {
            "query": query,
            "message_history": [],
        }

        raw = await self._mcp_client.call_tool(self._deal_management_tool, args)
        return self._parse_deal_response(raw)

    async def clone_deal(
        self,
        source_deal_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SSPDeal:
        """Clone a deal via the SSP's MCP deal management tool."""
        parts = [f"Clone deal {source_deal_id}"]
        if overrides:
            for key, value in overrides.items():
                parts.append(f"but change {key} to {value}")

        query = " ".join(parts)
        args: dict[str, Any] = {
            "query": query,
            "message_history": [],
        }

        raw = await self._mcp_client.call_tool(self._deal_management_tool, args)
        return self._parse_deal_response(raw)

    async def get_deal(self, deal_id: str) -> SSPDeal:
        """Get deal details. Uses troubleshooting tool as a read path."""
        raw = await self._mcp_client.call_tool(
            self._deal_troubleshooting_tool,
            {"deal_id": deal_id},
        )
        return self._parse_deal_from_troubleshoot(raw, deal_id)

    async def list_deals(
        self,
        *,
        status: Optional[SSPDealStatus] = None,
        limit: int = 100,
    ) -> list[SSPDeal]:
        """List deals. Not all MCP SSPs support this — returns empty if unavailable."""
        if not self._deal_list_tool:
            logger.warning("%s does not expose a deal list tool", self.ssp_name)
            return []

        args: dict[str, Any] = {"limit": limit}
        if status:
            args["status"] = status.value

        raw = await self._mcp_client.call_tool(self._deal_list_tool, args)

        if isinstance(raw, list):
            return [self._parse_deal_response(d) for d in raw]
        elif isinstance(raw, dict):
            items = raw.get("deals", raw.get("items", raw.get("data", [])))
            return [self._parse_deal_response(d) for d in items]
        return []

    async def update_deal(
        self,
        deal_id: str,
        updates: dict[str, Any],
    ) -> SSPDeal:
        """Update a deal. Uses deal management tool with update query."""
        if self._deal_update_tool:
            raw = await self._mcp_client.call_tool(
                self._deal_update_tool,
                {"deal_id": deal_id, **updates},
            )
        else:
            # Fall back to conversational update via deal_management
            parts = [f"Update deal {deal_id}:"]
            for key, value in updates.items():
                parts.append(f"change {key} to {value}")

            raw = await self._mcp_client.call_tool(
                self._deal_management_tool,
                {"query": " ".join(parts), "message_history": []},
            )

        return self._parse_deal_response(raw)

    # --- Troubleshooting ---

    async def troubleshoot_deal(self, deal_id: str) -> SSPTroubleshootResult:
        """Diagnose deal performance via the SSP's troubleshooting tool."""
        raw = await self._mcp_client.call_tool(
            self._deal_troubleshooting_tool,
            {"deal_id": deal_id},
        )

        return self._parse_troubleshoot_response(raw, deal_id)

    # --- Response Parsing ---

    def _parse_deal_response(self, raw: Any) -> SSPDeal:
        """Parse a deal from an MCP tool response."""
        if isinstance(raw, str):
            # Text-only response — extract what we can
            return SSPDeal(
                deal_id="unknown",
                ssp_type=self.ssp_type,
                ssp_name=self.ssp_name,
                raw={"text": raw},
            )

        if not isinstance(raw, dict):
            return SSPDeal(deal_id="unknown", ssp_type=self.ssp_type, ssp_name=self.ssp_name)

        # Check for structuredContent (PubMatic pattern)
        deal_info = raw.get("structuredContent", {}).get("deal_info", raw)

        status_str = str(deal_info.get("status", "created")).lower()

        return SSPDeal(
            deal_id=str(deal_info.get("id", deal_info.get("deal_id", "unknown"))),
            name=deal_info.get("name"),
            deal_type=self._map_deal_type(deal_info.get("deal_type")),
            status=_STATUS_MAP.get(status_str, SSPDealStatus.CREATED),
            advertiser=deal_info.get("advertiser"),
            cpm=deal_info.get("cpm"),
            currency=deal_info.get("currency", "USD"),
            priority=deal_info.get("priority"),
            start_date=deal_info.get("start_date"),
            end_date=deal_info.get("end_date"),
            targeting=deal_info.get("targeting"),
            ssp_type=self.ssp_type,
            ssp_name=self.ssp_name,
            raw=raw,
        )

    def _parse_deal_from_troubleshoot(self, raw: Any, deal_id: str) -> SSPDeal:
        """Extract deal info from a troubleshooting response."""
        if not isinstance(raw, dict):
            return SSPDeal(deal_id=deal_id, ssp_type=self.ssp_type, ssp_name=self.ssp_name)

        sc = raw.get("structuredContent", {})
        deal_info = sc.get("deal_info", {})

        return SSPDeal(
            deal_id=str(deal_info.get("id", deal_id)),
            name=deal_info.get("name"),
            deal_type=self._map_deal_type(deal_info.get("deal_type")),
            advertiser=deal_info.get("advertiser"),
            start_date=deal_info.get("start_date"),
            end_date=deal_info.get("end_date"),
            ssp_type=self.ssp_type,
            ssp_name=self.ssp_name,
            raw=raw,
        )

    def _parse_troubleshoot_response(self, raw: Any, deal_id: str) -> SSPTroubleshootResult:
        """Parse a troubleshooting response."""
        if not isinstance(raw, dict):
            return SSPTroubleshootResult(
                deal_id=deal_id,
                ssp_type=self.ssp_type,
                raw={"text": str(raw)} if raw else None,
            )

        sc = raw.get("structuredContent", {})
        perf = sc.get("performance_summary", {})

        return SSPTroubleshootResult(
            deal_id=deal_id,
            health_score=perf.get("health_score"),
            status=perf.get("status", "unknown"),
            primary_issues=perf.get("primary_issues", []),
            root_causes=sc.get("root_causes", []),
            recommendations=sc.get("recommendations", []),
            ssp_type=self.ssp_type,
            raw=raw,
        )

    @staticmethod
    def _map_deal_type(raw_type: Optional[str]) -> SSPDealType:
        """Map SSP-specific deal type string to normalized enum."""
        if not raw_type:
            return SSPDealType.PMP
        t = raw_type.upper()
        if t in ("PMP", "PRIVATE_MARKETPLACE"):
            return SSPDealType.PMP
        elif t in ("PG", "PROGRAMMATIC_GUARANTEED"):
            return SSPDealType.PG
        elif t in ("PREFERRED", "PREFERRED_DEAL"):
            return SSPDealType.PREFERRED
        elif t in ("AUCTION_PACKAGE", "AP"):
            return SSPDealType.AUCTION_PACKAGE
        return SSPDealType.PMP
