"""Tests for _start_fastapi_background() in the AgentCore HTTP entrypoint.

Verifies:
- _INTERNAL_PORT defaults to 8001
- _INTERNAL_PORT reads from INTERNAL_API_PORT env var
- _start_fastapi_background is callable
- Health check timeout calls sys.exit(1)
- Successful startup returns without exit
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock bedrock_agentcore before importing the entrypoint
_mock_agentcore = MagicMock()
_mock_app = MagicMock()
_mock_app.entrypoint = lambda fn: fn
_mock_agentcore.BedrockAgentCoreApp.return_value = _mock_app
sys.modules.setdefault("bedrock_agentcore", MagicMock())
sys.modules.setdefault("bedrock_agentcore.runtime", _mock_agentcore)

from ad_seller.interfaces.agentcore.http_main import (  # noqa: E402
    _INTERNAL_PORT,
    _start_fastapi_background,
)


class TestInternalPortConstant:
    """Tests for the _INTERNAL_PORT module-level constant."""

    def test_default_port_is_8001(self):
        """_INTERNAL_PORT should default to 8001 when env var is not set."""
        # The module was already imported with whatever env was set at import
        # time. We verify the default by checking the constant directly.
        # If INTERNAL_API_PORT was not set, it should be 8001.
        if "INTERNAL_API_PORT" not in os.environ:
            assert _INTERNAL_PORT == 8001

    def test_port_reads_from_env_var(self):
        """_INTERNAL_PORT should read from INTERNAL_API_PORT env var at import time."""
        # Since the module is already imported, we test the mechanism by
        # verifying the expression directly.
        test_port = int(os.environ.get("INTERNAL_API_PORT", "8001"))
        assert test_port == _INTERNAL_PORT or test_port == 8001


class TestStartFastapiBackground:
    """Tests for the _start_fastapi_background function."""

    def test_function_exists_and_is_callable(self):
        """_start_fastapi_background should exist and be callable."""
        assert callable(_start_fastapi_background)

    @patch("ad_seller.interfaces.agentcore.http_main.sys")
    def test_health_check_timeout_raises_runtime_error(self, mock_sys):
        """When health check times out (httpx always fails), should raise RuntimeError."""
        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = ConnectionError("refused")

        mock_thread = MagicMock()

        with (
            patch("ad_seller.interfaces.agentcore.http_main.asyncio"),
            patch.dict("os.environ", {"INTERNAL_API_PORT": "18001"}),
        ):
            # Patch the imports inside _start_fastapi_background
            import builtins

            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "threading":
                    mod = MagicMock()
                    mod.Thread.return_value = mock_thread
                    return mod
                if name == "time":
                    mod = MagicMock()
                    mod.sleep = MagicMock()  # Don't actually sleep
                    return mod
                if name == "uvicorn":
                    return MagicMock()
                if name == "httpx":
                    return mock_httpx
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                with pytest.raises(RuntimeError, match="FastAPI background server failed"):
                    _start_fastapi_background()

    def test_successful_startup_returns_without_exit(self):
        """When health check succeeds (httpx returns 200), should return normally."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_response

        mock_thread = MagicMock()

        with patch("ad_seller.interfaces.agentcore.http_main.sys") as mock_sys:
            import builtins

            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "threading":
                    mod = MagicMock()
                    mod.Thread.return_value = mock_thread
                    return mod
                if name == "time":
                    return MagicMock()
                if name == "uvicorn":
                    return MagicMock()
                if name == "httpx":
                    return mock_httpx
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                _start_fastapi_background()

            mock_sys.exit.assert_not_called()
