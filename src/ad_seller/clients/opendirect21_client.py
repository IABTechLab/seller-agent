# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenDirect 2.1 client for agentic-direct MCP server.

Connects to the IAB Tech Lab agentic-direct server which implements
OpenDirect 2.1 specification via MCP (Model Context Protocol).
"""

from typing import Any, Optional

import httpx

from ..config import get_settings


class OpenDirect21Client:
    """Client for OpenDirect 2.1 via agentic-direct MCP server.

    This client connects to the agentic-direct server using the MCP
    protocol over streamable HTTP (SSE).

    Usage:
        async with OpenDirect21Client() as client:
            products = await client.list_products()
    """

    def __init__(self, base_url: Optional[str] = None):
        """Initialize the OpenDirect 2.1 client.

        Args:
            base_url: Base URL for the agentic-direct server
        """
        settings = get_settings()
        self.base_url = base_url or settings.opendirect_base_url
        self.mcp_url = f"{self.base_url}/mcp/sse"
        self.api_url = f"{self.base_url}/api/v2.1"

        self._http_client: Optional[httpx.AsyncClient] = None
        self._session: Optional[Any] = None
        # streamablehttp_client is an async context manager; must __aexit__ to tear down anyio TaskGroup
        self._mcp_transport_cm: Optional[Any] = None
        self._tools: dict[str, Any] = {}

    async def __aenter__(self) -> "OpenDirect21Client":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to the MCP server and cache available tools."""
        settings = get_settings()

        # Initialize HTTP client for REST fallback
        headers = {}
        if settings.opendirect_api_key:
            headers["X-API-Key"] = settings.opendirect_api_key
        elif settings.opendirect_token:
            headers["Authorization"] = f"Bearer {settings.opendirect_token}"

        self._http_client = httpx.AsyncClient(
            base_url=self.api_url,
            headers=headers,
            timeout=30.0,
        )

        # Try MCP connection (must keep and exit transport CM — it owns an anyio TaskGroup)
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            transport_cm = streamablehttp_client(self.mcp_url)
            read_stream, write_stream, _ = await transport_cm.__aenter__()
            self._mcp_transport_cm = transport_cm
            self._session = ClientSession(read_stream, write_stream)
            await self._session.__aenter__()
            await self._session.initialize()

            # Cache available tools
            tools_result = await self._session.list_tools()
            self._tools = {tool.name: tool for tool in tools_result.tools}
        except Exception:
            await self._teardown_mcp()
            self._session = None
            self._tools = {}

    async def _teardown_mcp(self) -> None:
        """Close MCP session and streamable HTTP transport in order."""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._mcp_transport_cm:
            try:
                await self._mcp_transport_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._mcp_transport_cm = None

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        await self._teardown_mcp()

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool or fall back to REST."""
        if self._session and name in self._tools:
            result = await self._session.call_tool(name, arguments)
            return result.content[0].text if result.content else None
        else:
            # Fall back to REST API
            return await self._rest_call(name, arguments)

    async def _rest_call(self, operation: str, params: dict[str, Any]) -> Any:
        """Make a REST API call."""
        if not self._http_client:
            raise RuntimeError("Client not connected")

        # Map operation names to REST endpoints
        endpoint_map = {
            "list_products": ("GET", "/products"),
            "get_product": ("GET", "/products/{id}"),
            "list_organizations": ("GET", "/organizations"),
            "create_organization": ("POST", "/organizations"),
            "list_accounts": ("GET", "/accounts"),
            "create_account": ("POST", "/accounts"),
            "list_orders": ("GET", "/orders"),
            "create_order": ("POST", "/orders"),
            "list_lines": ("GET", "/lines"),
            "create_line": ("POST", "/lines"),
        }

        if operation not in endpoint_map:
            raise ValueError(f"Unknown operation: {operation}")

        method, endpoint = endpoint_map[operation]

        # Handle path parameters
        if "{id}" in endpoint:
            endpoint = endpoint.replace("{id}", params.pop("id", ""))

        if method == "GET":
            response = await self._http_client.get(endpoint, params=params)
        else:
            response = await self._http_client.post(endpoint, json=params)

        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Organization Operations
    # =========================================================================

    async def list_organizations(
        self,
        role: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List organizations."""
        args = {}
        if role:
            args["role"] = role
        if status:
            args["status"] = status
        return await self._call_tool("list_organizations", args)

    async def create_organization(
        self,
        name: str,
        role: str,
        organization_id: Optional[str] = None,
        status: str = "active",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create an organization."""
        args = {"name": name, "role": role, "status": status}
        if organization_id:
            args["organizationid"] = organization_id
        if metadata:
            args["metadata"] = metadata
        return await self._call_tool("create_organization", args)

    # =========================================================================
    # Product Operations
    # =========================================================================

    async def list_products(
        self,
        seller_organization_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List products."""
        args = {}
        if seller_organization_id:
            args["sellerorganizationid"] = seller_organization_id
        return await self._call_tool("list_products", args)

    async def get_product(self, product_id: str) -> dict[str, Any]:
        """Get a product by ID."""
        return await self._call_tool("get_product", {"productid": product_id})

    async def create_product(
        self,
        name: str,
        seller_organization_id: str,
        inventory_segments: list[str],
        product_id: Optional[str] = None,
        description: Optional[str] = None,
        audience_targeting: Optional[dict[str, Any]] = None,
        ad_product_targeting: Optional[dict[str, Any]] = None,
        content_targeting: Optional[dict[str, Any]] = None,
        commercial_terms: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create a product."""
        args = {
            "name": name,
            "sellerorganizationid": seller_organization_id,
            "inventorysegments": inventory_segments,
        }
        if product_id:
            args["productid"] = product_id
        if description:
            args["description"] = description
        if audience_targeting:
            args["audiencetargeting"] = audience_targeting
        if ad_product_targeting:
            args["adproducttargeting"] = ad_product_targeting
        if content_targeting:
            args["contenttargeting"] = content_targeting
        if commercial_terms:
            args["commercialterms"] = commercial_terms
        return await self._call_tool("create_product", args)

    # =========================================================================
    # Account Operations
    # =========================================================================

    async def list_accounts(
        self,
        buyer_organization_id: Optional[str] = None,
        seller_organization_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List accounts."""
        args = {}
        if buyer_organization_id:
            args["buyerorganizationid"] = buyer_organization_id
        if seller_organization_id:
            args["sellerorganizationid"] = seller_organization_id
        if status:
            args["status"] = status
        return await self._call_tool("list_accounts", args)

    async def create_account(
        self,
        buyer_organization_id: str,
        seller_organization_id: str,
        account_id: Optional[str] = None,
        status: str = "active",
    ) -> dict[str, Any]:
        """Create an account."""
        args = {
            "buyerorganizationid": buyer_organization_id,
            "sellerorganizationid": seller_organization_id,
            "status": status,
        }
        if account_id:
            args["accountid"] = account_id
        return await self._call_tool("create_account", args)

    # =========================================================================
    # Order Operations (OpenDirect 2.1 specific)
    # =========================================================================

    async def list_orders(
        self,
        account_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List orders."""
        args = {}
        if account_id:
            args["accountid"] = account_id
        if status:
            args["status"] = status
        return await self._call_tool("list_orders", args)

    async def create_order(
        self,
        account_id: str,
        name: str,
        start_date: str,
        end_date: str,
        order_id: Optional[str] = None,
        budget: Optional[float] = None,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Create an order."""
        args = {
            "accountid": account_id,
            "name": name,
            "startdate": start_date,
            "enddate": end_date,
            "currency": currency,
        }
        if order_id:
            args["orderid"] = order_id
        if budget:
            args["budget"] = budget
        return await self._call_tool("create_order", args)

    # =========================================================================
    # Line Operations (OpenDirect 2.1 specific)
    # =========================================================================

    async def list_lines(
        self,
        order_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List line items."""
        args = {}
        if order_id:
            args["orderid"] = order_id
        if status:
            args["status"] = status
        return await self._call_tool("list_lines", args)

    async def create_line(
        self,
        order_id: str,
        product_id: str,
        name: str,
        start_date: str,
        end_date: str,
        rate_type: str = "CPM",
        rate: float = 0.0,
        quantity: int = 0,
        line_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a line item."""
        args = {
            "orderid": order_id,
            "productid": product_id,
            "name": name,
            "startdate": start_date,
            "enddate": end_date,
            "ratetype": rate_type,
            "rate": rate,
            "quantity": quantity,
        }
        if line_id:
            args["lineid"] = line_id
        return await self._call_tool("create_line", args)

    # =========================================================================
    # Proposal Operations (mapped from OD 2.1 Change Requests)
    # =========================================================================

    async def list_proposals(
        self,
        account_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List proposals (mapped from change requests in OD 2.1)."""
        args = {}
        if account_id:
            args["accountid"] = account_id
        if status:
            args["status"] = status
        return await self._call_tool("list_change_requests", args)

    async def update_proposal(
        self,
        proposal_id: str,
        status: Optional[str] = None,
        revision_type: Optional[str] = None,
        reason: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update a proposal status."""
        args = {"changeid": proposal_id}
        if status:
            args["status"] = status
        if reason:
            args["reason"] = reason
        args.update(kwargs)
        return await self._call_tool("update_change_request", args)
