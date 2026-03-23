# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""REST-based SSP client for SSPs that expose REST APIs.

For SSPs like Magnite, Index Exchange, OpenX, etc. that don't have
MCP servers but provide REST APIs for deal management.

This is a base implementation — each SSP will need response parsing
customized to their API format. Subclass and override _parse_* methods.
"""

import logging
from typing import Any, Optional

import httpx

from .ssp_base import (
    SSPClient,
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPTroubleshootResult,
    SSPType,
)

logger = logging.getLogger(__name__)


class RESTSSPClient(SSPClient):
    """SSP client that communicates via REST API.

    Generic implementation — subclass for SSP-specific API formats.
    Provides the HTTP plumbing; subclasses customize request/response mapping.
    """

    def __init__(
        self,
        *,
        ssp_type: SSPType = SSPType.CUSTOM,
        ssp_name: str = "REST SSP",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer",
        timeout: int = 30,
    ) -> None:
        self.ssp_type = ssp_type
        self.ssp_name = ssp_name
        self._base_url = base_url
        self._api_key = api_key
        self._auth_header = auth_header
        self._auth_prefix = auth_prefix
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Create HTTP client with auth headers."""
        if not self._base_url:
            raise ConnectionError(f"Base URL not configured for {self.ssp_name}")

        headers = {}
        if self._api_key:
            headers[self._auth_header] = f"{self._auth_prefix} {self._api_key}"

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
        )
        logger.info("Connected to %s REST API at %s", self.ssp_name, self._base_url)

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None

    def _ensure_connected(self) -> httpx.AsyncClient:
        if not self._http:
            raise ConnectionError(f"Not connected to {self.ssp_name}. Call connect() first.")
        return self._http

    # --- Deal Operations ---
    # Subclasses should override these with SSP-specific API paths and parsing.

    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Create a deal via REST API. Override for SSP-specific endpoints."""
        http = self._ensure_connected()
        body = request.model_dump(exclude_none=True)

        resp = await http.post("/api/v1/deals", json=body)
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def clone_deal(
        self,
        source_deal_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SSPDeal:
        """Clone a deal via REST API."""
        http = self._ensure_connected()
        body = {"source_deal_id": source_deal_id, **(overrides or {})}

        resp = await http.post(f"/api/v1/deals/{source_deal_id}/clone", json=body)
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def get_deal(self, deal_id: str) -> SSPDeal:
        """Get deal by ID via REST API."""
        http = self._ensure_connected()

        resp = await http.get(f"/api/v1/deals/{deal_id}")
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    async def list_deals(
        self,
        *,
        status: Optional[SSPDealStatus] = None,
        limit: int = 100,
    ) -> list[SSPDeal]:
        """List deals via REST API."""
        http = self._ensure_connected()
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status.value

        resp = await http.get("/api/v1/deals", params=params)
        resp.raise_for_status()

        data = resp.json()
        items = data if isinstance(data, list) else data.get("deals", data.get("data", []))
        return [self._parse_deal(d) for d in items]

    async def update_deal(
        self,
        deal_id: str,
        updates: dict[str, Any],
    ) -> SSPDeal:
        """Update a deal via REST API."""
        http = self._ensure_connected()

        resp = await http.patch(f"/api/v1/deals/{deal_id}", json=updates)
        resp.raise_for_status()
        return self._parse_deal(resp.json())

    # --- Troubleshooting ---

    async def troubleshoot_deal(self, deal_id: str) -> SSPTroubleshootResult:
        """Troubleshoot a deal via REST API. Override for SSP-specific endpoints."""
        http = self._ensure_connected()

        resp = await http.get(f"/api/v1/deals/{deal_id}/troubleshoot")
        resp.raise_for_status()
        return self._parse_troubleshoot(resp.json(), deal_id)

    # --- Response Parsing (override in subclasses) ---

    def _parse_deal(self, raw: dict[str, Any]) -> SSPDeal:
        """Parse SSP-specific deal response. Override for custom formats."""
        return SSPDeal(
            deal_id=str(raw.get("id", raw.get("deal_id", "unknown"))),
            name=raw.get("name"),
            status=SSPDealStatus(raw.get("status", "created")),
            advertiser=raw.get("advertiser"),
            cpm=raw.get("cpm"),
            currency=raw.get("currency", "USD"),
            start_date=raw.get("start_date"),
            end_date=raw.get("end_date"),
            targeting=raw.get("targeting"),
            ssp_type=self.ssp_type,
            ssp_name=self.ssp_name,
            raw=raw,
        )

    def _parse_troubleshoot(self, raw: dict[str, Any], deal_id: str) -> SSPTroubleshootResult:
        """Parse SSP-specific troubleshooting response. Override for custom formats."""
        return SSPTroubleshootResult(
            deal_id=deal_id,
            health_score=raw.get("health_score"),
            status=raw.get("status", "unknown"),
            primary_issues=raw.get("issues", []),
            root_causes=raw.get("root_causes", []),
            recommendations=raw.get("recommendations", []),
            ssp_type=self.ssp_type,
            raw=raw,
        )
