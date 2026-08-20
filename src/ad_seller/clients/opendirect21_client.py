# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenDirect 2.1 client for agentic-direct MCP server.

Connects to the IAB Tech Lab agentic-direct server which implements
OpenDirect 2.1 specification via MCP (Model Context Protocol).
"""

import asyncio
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
        self._tools: dict[str, Any] = {}

        # The MCP session is held open by a dedicated background task --
        # see connect()/_run_mcp_session() for why.
        self._session_task: Optional[asyncio.Task] = None
        self._session_ready: Optional[asyncio.Event] = None
        self._session_done: Optional[asyncio.Event] = None
        self._session_error: Optional[BaseException] = None

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

        # The MCP session runs entirely inside one background task: the
        # streamablehttp_client/ClientSession context managers are entered
        # AND exited within one unbroken `async with` block, in one task,
        # start to finish -- the task just blocks on _session_done.wait()
        # in between. This is deliberate, not incidental: splitting
        # __aenter__/__aexit__ across separate connect()/disconnect() calls
        # (the previous shape of this method) breaks anyio's same-task
        # cancel-scope invariant the moment the connection attempt fails,
        # and crashes with "Attempted to exit cancel scope in a different
        # task than it was entered in" -- reproduced with a bare
        # streamablehttp_client() call against an unreachable URL,
        # independent of anything else this class does (issue #60 part 2).
        # Mirrors the already-proven-safe pattern in deals_api_mcp_client.py.
        self._session_ready = asyncio.Event()
        self._session_done = asyncio.Event()
        self._session_error = None
        self._session_task = asyncio.create_task(self._run_mcp_session())
        await self._session_ready.wait()
        # self._session_error set means the MCP attempt failed (or was
        # cancelled, in which case it was re-raised out of the background
        # task already) -- self._session stays None and callers fall back
        # to REST via _call_tool().

    async def _run_mcp_session(self) -> None:
        """Own the MCP session's full lifetime in this one task.

        Runs until _session_done is set (normal disconnect()) or the
        connection attempt itself fails, in which case _session_error is
        recorded and _session_ready is released so connect() doesn't hang.
        """
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        assert self._session_ready is not None
        assert self._session_done is not None
        try:
            async with streamablehttp_client(self.mcp_url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    self._tools = {tool.name: tool for tool in tools_result.tools}
                    self._session = session
                    self._session_ready.set()
                    await self._session_done.wait()
        except BaseException as exc:
            self._session_error = exc
            if not self._session_ready.is_set():
                self._session_ready.set()
            # A cancellation means something upstream wants this task to
            # stop; swallowing it here would hide that from the task's own
            # cancellation machinery.
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
        finally:
            self._session = None

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._session_task is not None:
            if self._session_done is not None:
                self._session_done.set()
            try:
                await asyncio.wait_for(self._session_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._session_task.cancel()
                try:
                    await self._session_task
                except (asyncio.CancelledError, Exception):
                    pass
            except Exception:
                pass
            self._session_task = None
        self._session = None

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
