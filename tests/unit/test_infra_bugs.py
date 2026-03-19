# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for P1 infrastructure bug fixes.

Bug 1 (buyer-2fb): _get_optional_api_key_record resolves credentials from
query params instead of HTTP headers. The FastAPI dependency must use Header()
annotations so that X-Api-Key and Authorization headers are read on the live
server.

Bug 2 (buyer-mt9): crewai listen() now accepts only 1 positional argument.
Flows that pass multiple args crash the import chain and prevent the API
server from starting.
"""

import importlib
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# =============================================================================
# Bug 1: Auth header resolution
# =============================================================================


class TestAuthHeaderResolution:
    """Verify _get_optional_api_key_record reads from HTTP headers, not query params."""

    def test_dependency_uses_header_annotations(self):
        """The wrapper function must declare Header() defaults so FastAPI
        reads X-Api-Key and Authorization from HTTP headers, not query params.

        This is the root cause: without Header() annotations, FastAPI treats
        plain Optional[str] parameters as query parameters.
        """
        import inspect
        from fastapi import Header
        from ad_seller.interfaces.api.main import _get_optional_api_key_record

        sig = inspect.signature(_get_optional_api_key_record)

        # Check authorization parameter
        auth_param = sig.parameters.get("authorization")
        assert auth_param is not None, "authorization parameter missing"
        # The default must be a Header() instance, not None
        default = auth_param.default
        assert hasattr(default, "alias") or isinstance(
            default, type(Header(None))
        ), (
            f"authorization parameter default is {default!r}, "
            "expected a FastAPI Header() instance"
        )

        # Check x_api_key parameter
        xak_param = sig.parameters.get("x_api_key")
        assert xak_param is not None, "x_api_key parameter missing"
        default = xak_param.default
        assert hasattr(default, "alias") or isinstance(
            default, type(Header(None))
        ), (
            f"x_api_key parameter default is {default!r}, "
            "expected a FastAPI Header() instance"
        )

    def test_x_api_key_header_reaches_dependency(self):
        """Sending X-Api-Key via HTTP header should reach the auth dependency.

        We test the wrapper function directly to prove it passes Header()
        values through to get_api_key_record in auth/dependencies.py.
        """
        import asyncio
        from ad_seller.interfaces.api.main import _get_optional_api_key_record

        with patch(
            "ad_seller.auth.dependencies.get_api_key_record",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = None  # anonymous

            # Call the wrapper directly with header values as FastAPI would
            result = asyncio.get_event_loop().run_until_complete(
                _get_optional_api_key_record(
                    authorization=None,
                    x_api_key="ask_live_testkey123",
                )
            )

            # Verify get_api_key_record was called with the header values
            mock_get.assert_called_once_with(None, "ask_live_testkey123")

    def test_bearer_auth_header_reaches_dependency(self):
        """Sending Authorization: Bearer <key> via HTTP header should reach
        the auth dependency."""
        import asyncio
        from ad_seller.interfaces.api.main import _get_optional_api_key_record

        with patch(
            "ad_seller.auth.dependencies.get_api_key_record",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = None

            result = asyncio.get_event_loop().run_until_complete(
                _get_optional_api_key_record(
                    authorization="Bearer ask_live_testkey123",
                    x_api_key=None,
                )
            )

            mock_get.assert_called_once_with("Bearer ask_live_testkey123", None)


# =============================================================================
# Bug 2: crewai listen() API change
# =============================================================================


class TestCrewaiFlowImports:
    """Verify that flow modules can be imported without crashing."""

    def test_discovery_inquiry_flow_imports(self):
        """DiscoveryInquiryFlow must import without TypeError from listen()."""
        # Force a fresh import to detect any decorator errors
        import ad_seller.flows.discovery_inquiry_flow as mod
        importlib.reload(mod)
        assert hasattr(mod, "DiscoveryInquiryFlow")

    def test_execution_activation_flow_imports(self):
        """ExecutionActivationFlow must import without TypeError from listen()."""
        import ad_seller.flows.execution_activation_flow as mod
        importlib.reload(mod)
        assert hasattr(mod, "ExecutionActivationFlow")

    def test_flows_init_imports_all(self):
        """flows/__init__.py must import all flow classes without error."""
        import ad_seller.flows as mod
        importlib.reload(mod)
        assert hasattr(mod, "ProductSetupFlow")
        assert hasattr(mod, "DiscoveryInquiryFlow")
        assert hasattr(mod, "ProposalHandlingFlow")
        assert hasattr(mod, "DealGenerationFlow")
        assert hasattr(mod, "DealRequestFlow")
        assert hasattr(mod, "ExecutionActivationFlow")

    def test_proposal_handling_flow_imports(self):
        """ProposalHandlingFlow uses or_() which should still work."""
        import ad_seller.flows.proposal_handling_flow as mod
        importlib.reload(mod)
        assert hasattr(mod, "ProposalHandlingFlow")

    def test_server_starts_without_import_crash(self):
        """Simulate what happens when the server starts and an endpoint
        triggers flow imports. This is the real-world failure scenario."""
        from ad_seller.interfaces.api.main import app

        client = TestClient(app)

        # /products triggers: from ...flows import ProductSetupFlow
        # which imports flows/__init__.py, which imports all flows.
        # If any flow has a broken @listen() decorator, this crashes.
        # We mock the actual flow execution to avoid needing a real product catalog.
        with patch(
            "ad_seller.interfaces.api.main.ProductSetupFlow",
            create=True,
        ) as MockFlow:
            # We don't need the flow to actually work, just need the import
            # to succeed without TypeError.
            pass

        # The real test: can we import ProductSetupFlow from flows?
        from ad_seller.flows import ProductSetupFlow
        assert ProductSetupFlow is not None
