# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for OpenDirect21Client's MCP connection lifecycle (issue #60 part 2).

connect()/disconnect() used to split streamablehttp_client's/ClientSession's
__aenter__ and __aexit__ across separate method calls. That's fine on the
happy path, but the moment the connection attempt itself failed, __aenter__
never returned so __aexit__ was never called -- the transport was abandoned
to Python's async-generator GC finalizer, which runs in whatever task the
garbage collector happens to be executing in, not the task that opened the
connection. anyio requires a cancel scope to be entered and exited by the
same task, so that mismatch crashed with "Attempted to exit cancel scope in
a different task than it was entered in" -- and that crash propagated all
the way up through kickoff_async(), taking down the whole flow.

connect() now runs the MCP session's entire lifetime -- open through close
-- inside one background task via a proper nested `async with`, mirroring
the pattern already proven safe in deals_api_mcp_client.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_seller.clients.opendirect21_client import OpenDirect21Client


@pytest.fixture
def settings_stub():
    with patch("ad_seller.clients.opendirect21_client.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            opendirect_base_url="http://127.0.0.1:1",
            opendirect_api_key=None,
            opendirect_token=None,
        )
        yield mock_settings


class TestConnectionFailure:
    """The regression: a connection attempt that fails must degrade to REST
    fallback cleanly, never crash, and never leak the background task."""

    async def test_unreachable_server_does_not_raise(self, settings_stub):
        client = OpenDirect21Client(base_url="http://127.0.0.1:1")
        await client.connect()  # must not raise
        assert client._session is None
        assert client._session_error is not None
        await client.disconnect()

    async def test_unreachable_server_records_error_for_rest_fallback(self, settings_stub):
        client = OpenDirect21Client(base_url="http://127.0.0.1:1")
        await client.connect()
        assert client._session_error is not None
        assert client._tools == {}
        await client.disconnect()

    async def test_call_tool_falls_back_to_rest_when_mcp_unreachable(self, settings_stub):
        """_call_tool must route through _rest_call, not blow up, when
        the MCP session never established."""
        client = OpenDirect21Client(base_url="http://127.0.0.1:1")
        await client.connect()
        with patch.object(
            client, "_rest_call", new=AsyncMock(return_value=[{"id": "p1"}])
        ) as mock_rest:
            result = await client._call_tool("list_products", {})
        mock_rest.assert_awaited_once()
        assert result == [{"id": "p1"}]
        await client.disconnect()

    async def test_repeated_connect_disconnect_cycles_do_not_crash(self, settings_stub):
        """Regression guard: the original bug reproduced reliably on a
        single failed connection attempt -- this exercises several in a
        row to catch anything that only shows up on reuse."""
        for _ in range(5):
            client = OpenDirect21Client(base_url="http://127.0.0.1:1")
            await client.connect()
            assert client._session is None
            await client.disconnect()

    async def test_disconnect_without_connect_is_a_noop(self, settings_stub):
        client = OpenDirect21Client(base_url="http://127.0.0.1:1")
        await client.disconnect()  # must not raise


class TestConnectionSuccess:
    """The happy path must still work after the rewrite."""

    async def test_successful_connection_sets_session_and_tools(self, settings_stub):
        mock_tool = MagicMock(name="list_products")
        mock_tool.name = "list_products"
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[mock_tool]))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_transport_cm = AsyncMock()
        mock_transport_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock(), None))
        mock_transport_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "mcp.client.streamable_http.streamablehttp_client",
                return_value=mock_transport_cm,
            ),
            patch("mcp.ClientSession", return_value=mock_session),
        ):
            client = OpenDirect21Client(base_url="http://127.0.0.1:1")
            await client.connect()

            assert client._session is mock_session
            assert client._session_error is None
            assert "list_products" in client._tools

            await client.disconnect()
            mock_session.__aexit__.assert_awaited()
            mock_transport_cm.__aexit__.assert_awaited()
