# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Low-level MCP client wrapper for FreeWheel MCP servers.

Handles MCP transport negotiation (Streamable HTTP and SSE), JSON-RPC
tool invocation, session management, and error normalization for both
Streaming Hub and Buyer Cloud MCPs.
"""

import logging
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class FreeWheelMCPError(Exception):
    """Error from a FreeWheel MCP tool call."""

    def __init__(self, message: str, code: Optional[int] = None, data: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.data = data


class FreeWheelMCPClient:
    """Wraps mcp.ClientSession for calling FreeWheel MCP tools.

    Usage:
        client = FreeWheelMCPClient()
        await client.connect("https://shmcp.freewheel.com", auth_params={...})
        result = await client.call_tool("list_inventory", {})
        await client.disconnect()
    """

    def __init__(self) -> None:
        self._session: Any = None  # mcp.ClientSession
        self._transport: Any = None  # MCP transport context manager
        self._transport_mode: Optional[str] = None
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
        """Connect to a FreeWheel MCP server.

        Args:
            url: MCP server URL (e.g. https://shmcp.freewheel.com)
            auth_params: Credentials to pass to the login tool
            login_tool: Name of the login tool (e.g. "streaming_hub_login")
        """
        from mcp import ClientSession

        logger.info("Connecting to FreeWheel MCP at %s", url)

        attempts = _build_transport_attempts(url)
        last_error: Optional[Exception] = None

        for mode, candidate_url in attempts:
            try:
                self._session = None
                self._transport = None

                if mode == "streamable_http":
                    from mcp.client.streamable_http import streamablehttp_client

                    self._transport = streamablehttp_client(candidate_url)
                    transport_tuple = await self._transport.__aenter__()
                    read_stream, write_stream = transport_tuple[0], transport_tuple[1]
                else:
                    from mcp.client.sse import sse_client

                    self._transport = sse_client(candidate_url)
                    read_stream, write_stream = await self._transport.__aenter__()

                self._session = ClientSession(read_stream, write_stream)
                await self._session.__aenter__()
                await self._session.initialize()

                self._connected = True
                self._transport_mode = mode
                self._url = candidate_url
                logger.info("MCP session established with %s (transport=%s)", candidate_url, mode)
                break
            except Exception as exc:
                last_error = exc
                await self._close_partial_connection()
                logger.debug(
                    "MCP connect attempt failed (transport=%s, url=%s): %s",
                    mode,
                    candidate_url,
                    exc,
                )
        else:
            tried = ", ".join(f"{mode}:{candidate_url}" for mode, candidate_url in attempts)
            message = f"Unable to connect to FreeWheel MCP. Tried: {tried}."
            if last_error:
                message = f"{message} Last error: {last_error}"
            raise ConnectionError(message) from last_error

        # Authenticate if login tool provided
        if login_tool and auth_params:
            result = await self.call_tool(login_tool, auth_params)
            # Store session ID if returned
            if isinstance(result, dict) and "session_id" in result:
                self._session_id = result["session_id"]
                logger.info("Authenticated with session_id: %s...", self._session_id[:8])

    async def reconnect(
        self,
        auth_params: Optional[dict[str, str]] = None,
        login_tool: Optional[str] = None,
    ) -> None:
        """Re-authenticate on an existing connection (e.g. after session expiry).

        Calls the login tool again without tearing down the SSE transport.
        """
        if not self._connected or not self._session:
            raise ConnectionError("Cannot reconnect — not connected. Call connect() first.")

        self._session_id = None  # Clear stale session

        if login_tool and auth_params:
            result = await self.call_tool(login_tool, auth_params)
            if isinstance(result, dict) and "session_id" in result:
                self._session_id = result["session_id"]
                logger.info("Re-authenticated with session_id: %s...", self._session_id[:8])

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

        await self._close_partial_connection()
        self._session_id = None
        logger.info("Disconnected from FreeWheel MCP at %s", self._url)

    async def _close_partial_connection(self) -> None:
        """Close session/transport resources after failed or completed connections."""

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
        self._transport_mode = None

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
                error_text = (
                    result.content[0].text
                    if hasattr(result.content[0], "text")
                    else str(result.content[0])
                )
            raise FreeWheelMCPError(
                f"MCP tool '{tool_name}' failed: {error_text}",
                data={"tool": tool_name, "arguments": arguments},
            )

        # Extract text content and parse as JSON
        if result.content:
            import json

            text = (
                result.content[0].text
                if hasattr(result.content[0], "text")
                else str(result.content[0])
            )
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


def _build_transport_attempts(url: str) -> list[tuple[str, str]]:
    """Build ordered MCP transport/url attempts from a configured endpoint."""
    parsed = urlparse(url)
    base = url.rstrip("/")
    path = (parsed.path or "").rstrip("/")

    attempts: list[tuple[str, str]] = []
    if path.endswith("/sse"):
        if base:
            attempts.append(("sse", base))
        root_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        if root_url:
            attempts.append(("streamable_http", root_url))
    else:
        if base:
            attempts.append(("streamable_http", base))
            attempts.append(("sse", f"{base}/sse"))
            attempts.append(("sse", f"{base}/mcp-sse/sse"))
            attempts.append(("sse", base))

    # Preserve order while deduplicating attempts.
    unique_attempts: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for attempt in attempts:
        if attempt in seen:
            continue
        seen.add(attempt)
        unique_attempts.append(attempt)
    return unique_attempts
