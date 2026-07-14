"""EP-3.2: the AgentCore MCP entrypoint must not run a background REST sidecar.

Before EP-3.2, ``mcp_main.py`` started FastAPI in a background uvicorn thread
on port 8001 so that MCP tools could reach the REST API over an httpx
loopback. Now the MCP tools call the service layer directly in-process, so the
sidecar is gone. These tests lock that in:

- no ``_start_fastapi_background`` / port-8001 machinery remains, and
- ``main()`` starts the MCP server and never spawns a background thread,
  imports uvicorn, or binds the internal REST port.
"""

import threading
from unittest.mock import MagicMock, patch

from ad_seller.interfaces.agentcore import mcp_main


def test_no_background_fastapi_helper():
    """The background-uvicorn sidecar helper must not exist anymore."""
    assert not hasattr(mcp_main, "_start_fastapi_background")
    assert not hasattr(mcp_main, "_INTERNAL_REST_PORT")


def test_source_has_no_uvicorn_or_port_8001():
    """No uvicorn/threading/port-8001 references remain in the entrypoint."""
    import inspect

    source = inspect.getsource(mcp_main)
    lowered = source.lower()
    assert "uvicorn" not in lowered
    assert "8001" not in source
    assert "threading" not in lowered
    assert "internal_api_port" not in lowered


def test_main_runs_mcp_without_background_thread():
    """main() starts the MCP server and spawns no background REST thread."""
    fake_mcp = MagicMock()

    # Snapshot live threads so we can assert none are added by main().
    threads_before = set(threading.enumerate())

    # Patch the FastMCP instance the entrypoint imports and drives, plus the
    # transport-security import target so main() runs without real network I/O.
    with (
        patch("ad_seller.interfaces.mcp_server.mcp", fake_mcp),
        patch("threading.Thread") as thread_cls,
    ):
        mcp_main.main()

    # The MCP server was run via streamable-http transport …
    fake_mcp.run.assert_called_once_with(transport="streamable-http")
    # … and no background thread (uvicorn REST sidecar) was created.
    thread_cls.assert_not_called()
    assert set(threading.enumerate()) == threads_before
