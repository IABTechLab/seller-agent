# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Low-level MCP client wrapper for FreeWheel MCP servers.

Handles SSE connection, JSON-RPC tool invocation, session management,
and error normalization for both Streaming Hub and Buyer Cloud MCPs.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FreeWheelMCPError(Exception):
    """Error from a FreeWheel MCP tool call."""

    def __init__(self, message: str, code: Optional[int] = None, data: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.data = data


class FreeWheelMCPClient:
    """Wraps mcp.ClientSession for calling FreeWheel MCP tools over SSE.

    Usage:
        client = FreeWheelMCPClient()
        await client.connect("https://shmcp.freewheel.com", auth_params={...})
        result = await client.call_tool("list_inventory", {})
        await client.disconnect()
    """

    def __init__(self) -> None:
        self._session: Any = None  # mcp.ClientSession
        self._transport: Any = None  # SSE transport
        self._session_id: Optional[str] = None
        self._url: Optional[str] = None
        self._connected: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(
        self,
        url: str,
        auth_params: Optional[dict[str, str]] = None,
        login_tool: Optional[str] = None,
    ) -> None:
        """Connect to a FreeWheel MCP server via SSE.

        Args:
            url: MCP server URL (e.g. https://shmcp.freewheel.com)
            auth_params: Credentials to pass to the login tool
            login_tool: Name of the login tool (e.g. "streaming_hub_login")
        """
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        self._url = url
        logger.info("Connecting to FreeWheel MCP at %s", url)

        # Establish SSE transport and session
        self._transport = sse_client(url)
        read_stream, write_stream = await self._transport.__aenter__()
        self._session = ClientSession(read_stream, write_stream)
        await self._session.__aenter__()

        # Initialize the MCP session
        await self._session.initialize()
        self._connected = True
        logger.info("MCP session established with %s", url)

        # Authenticate if login tool provided
        if login_tool and auth_params:
            result = await self.call_tool(login_tool, auth_params)
            # Store session ID if returned
            if isinstance(result, dict) and "session_id" in result:
                self._session_id = result["session_id"]
                logger.info("Authenticated with session_id: %s...", self._session_id[:8])

    async def disconnect(self, logout_tool: Optional[str] = None) -> None:
        """Disconnect from the MCP server.

        Args:
            logout_tool: Name of the logout tool to call before disconnecting
        """
        if logout_tool and self._connected:
            try:
                await self.call_tool(logout_tool, {})
            except Exception as e:
                logger.warning("Logout tool failed (non-fatal): %s", e)

        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None

        if self._transport:
            try:
                await self._transport.__aexit__(None, None, None)
            except Exception:
                pass
            self._transport = None

        self._connected = False
        self._session_id = None
        logger.info("Disconnected from FreeWheel MCP at %s", self._url)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool and return the parsed result.

        Args:
            tool_name: MCP tool name (e.g. "list_inventory", "book_deal")
            arguments: Tool arguments as a dict

        Returns:
            Parsed tool result (dict or list)

        Raises:
            FreeWheelMCPError: If the tool returns an error
            ConnectionError: If not connected
        """
        if not self._connected or not self._session:
            raise ConnectionError("Not connected to FreeWheel MCP. Call connect() first.")

        # Inject session_id if we have one
        if self._session_id and "session_id" not in arguments:
            arguments = {**arguments, "session_id": self._session_id}

        logger.debug("Calling MCP tool: %s(%s)", tool_name, list(arguments.keys()))

        result = await self._session.call_tool(tool_name, arguments=arguments)

        # MCP tool results contain a list of content blocks
        if result.isError:
            error_text = ""
            if result.content:
                error_text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
            raise FreeWheelMCPError(
                f"MCP tool '{tool_name}' failed: {error_text}",
                data={"tool": tool_name, "arguments": arguments},
            )

        # Extract text content and parse as JSON
        if result.content:
            import json

            text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text

        return None

    async def list_tools(self) -> list[str]:
        """List available tools on the connected MCP server."""
        if not self._connected or not self._session:
            raise ConnectionError("Not connected to FreeWheel MCP.")

        result = await self._session.list_tools()
        return [tool.name for tool in result.tools]
