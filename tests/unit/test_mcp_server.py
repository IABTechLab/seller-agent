# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for MCP Server tools.

Covers:
- get_setup_status reports incomplete when name is "Default Publisher"
- get_setup_status reports complete when identity + ad server + media kit configured
- health_check returns healthy status
- get_config returns non-secret config values
"""

import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_settings(**overrides):
    """Create a plain namespace settings object (avoids MagicMock serialization issues)."""
    defaults = {
        "seller_organization_name": "Default Publisher",
        "seller_organization_id": "org-001",
        "gam_network_code": None,
        "freewheel_sh_mcp_url": None,
        "ssp_connectors": "",
        "ssp_routing_rules": "",
        "ad_server_type": "google_ad_manager",
        "gam_enabled": False,
        "freewheel_enabled": False,
        "freewheel_inventory_mode": "deals_only",
        "default_currency": "USD",
        "default_price_floor_cpm": 1.0,
        "approval_gate_enabled": False,
        "approval_timeout_hours": 24,
        "approval_required_flows": "",
        "yield_optimization_enabled": False,
        "programmatic_floor_multiplier": 1.0,
        "preferred_deal_discount_max": 0.15,
        "agent_registry_enabled": False,
        "agent_registry_url": "",
        "pubmatic_mcp_url": "",
        "index_exchange_api_url": "",
        "magnite_api_url": "",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class TestGetSetupStatus:
    """get_setup_status tool tests."""

    @pytest.mark.asyncio
    async def test_incomplete_with_default_publisher(self):
        from ad_seller.interfaces.mcp_server import get_setup_status

        settings = _make_settings(seller_organization_name="Default Publisher")
        storage = AsyncMock()

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ), patch(
            "ad_seller.interfaces.mcp_server._get_storage", new_callable=AsyncMock, return_value=storage
        ):
            result = json.loads(await get_setup_status())

        assert result["publisher_identity"]["configured"] is False
        assert result["setup_complete"] is False
        assert "incomplete" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_complete_when_fully_configured(self):
        from ad_seller.interfaces.mcp_server import get_setup_status

        settings = _make_settings(
            seller_organization_name="My Publisher",
            gam_network_code="12345",
        )
        storage = AsyncMock()

        # Mock MediaKitService to return some packages
        mock_pkg = MagicMock()
        mock_service = AsyncMock()
        mock_service.list_packages_public.return_value = [mock_pkg]

        # The function does: from ..engines.media_kit_service import MediaKitService
        # We patch the module in sys.modules so the local import picks up our mock.
        fake_module = MagicMock()
        fake_module.MediaKitService.return_value = mock_service

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ), patch(
            "ad_seller.interfaces.mcp_server._get_storage", new_callable=AsyncMock, return_value=storage
        ), patch.dict(
            "sys.modules",
            {"ad_seller.engines.media_kit_service": fake_module},
        ):
            result = json.loads(await get_setup_status())

        assert result["publisher_identity"]["configured"] is True
        assert result["ad_server"]["configured"] is True
        assert result["media_kit"]["configured"] is True
        assert result["setup_complete"] is True
        assert "fully configured" in result["message"].lower()


class TestHealthCheck:
    """health_check tool tests."""

    @pytest.mark.asyncio
    async def test_healthy_status(self):
        from ad_seller.interfaces.mcp_server import health_check

        settings = _make_settings()
        storage = AsyncMock()

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ), patch(
            "ad_seller.interfaces.mcp_server._get_storage", new_callable=AsyncMock, return_value=storage
        ):
            result = json.loads(await health_check())

        assert result["status"] == "healthy"
        assert result["checks"]["storage"] == "ok"

    @pytest.mark.asyncio
    async def test_degraded_when_storage_fails(self):
        from ad_seller.interfaces.mcp_server import health_check

        settings = _make_settings()

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ), patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            side_effect=Exception("Storage unavailable"),
        ):
            result = json.loads(await health_check())

        assert result["status"] == "degraded"
        assert "error" in result["checks"]["storage"]


class TestGetConfig:
    """get_config tool tests."""

    @pytest.mark.asyncio
    async def test_returns_non_secret_values(self):
        from ad_seller.interfaces.mcp_server import get_config

        settings = _make_settings(
            seller_organization_name="My Publisher",
            seller_organization_id="org-001",
            default_currency="USD",
            default_price_floor_cpm=2.0,
        )

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ):
            result = json.loads(await get_config())

        assert result["publisher"]["name"] == "My Publisher"
        assert result["publisher"]["org_id"] == "org-001"
        assert result["pricing"]["currency"] == "USD"
        assert result["pricing"]["floor_cpm"] == 2.0
        # Ensure no secret fields like API keys
        config_str = json.dumps(result)
        assert "api_key" not in config_str.lower()
        assert "anthropic" not in config_str.lower()


# =============================================================================
# Task 13: Prompt registration tests
# =============================================================================


class TestPromptRegistration:
    """Verify all 9 MCP prompts are registered."""

    def test_all_prompts_registered(self):
        """All 9 prompts should be registered on the MCP server."""
        from ad_seller.interfaces.mcp_server import mcp

        prompt_names = set()
        for p in mcp._prompt_manager.list_prompts():
            prompt_names.add(p.name)
        expected = {
            "setup", "status", "inventory", "deals", "queue",
            "new-deal", "configure", "buyers", "help",
        }
        assert expected.issubset(prompt_names), f"Missing: {expected - prompt_names}"


# =============================================================================
# Task 14: get_inbound_queue tests
# =============================================================================


class TestGetInboundQueue:
    """Tests for get_inbound_queue composite tool."""

    @pytest.mark.asyncio
    async def test_returns_pending_approvals_and_proposals(self):
        from datetime import datetime, timedelta

        from ad_seller.events.models import (
            ApprovalRequest,
            ApprovalStatus,
            Event,
            EventType,
        )
        from ad_seller.interfaces.mcp_server import get_inbound_queue

        # Create mock approval
        future = datetime.utcnow() + timedelta(hours=12)
        mock_approval = ApprovalRequest(
            approval_id="apr-001",
            event_id="evt-001",
            flow_id="flow-001",
            flow_type="proposal_handling",
            gate_name="proposal_decision",
            proposal_id="prop-001",
            status=ApprovalStatus.PENDING,
            expires_at=future,
            context={"summary": "Deal from BuyerCo"},
        )

        # Create mock proposal event (no subsequent accepted/rejected/countered)
        mock_proposal_event = Event(
            event_id="evt-002",
            event_type=EventType.PROPOSAL_RECEIVED,
            proposal_id="prop-002",
            session_id="sess-001",
            payload={"buyer": "AcmeBuyer"},
        )

        mock_storage = AsyncMock()
        mock_bus = AsyncMock()

        # ApprovalGate.list_pending returns our mock
        mock_gate_list = AsyncMock(return_value=[mock_approval])

        # Event bus: proposal.received returns one event; accepted/rejected/countered return empty
        async def mock_list_events(event_type=None, limit=50, **kwargs):
            if event_type == "proposal.received":
                return [mock_proposal_event]
            return []

        mock_bus.list_events = AsyncMock(side_effect=mock_list_events)

        with patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock, return_value=mock_storage,
        ), patch(
            "ad_seller.interfaces.mcp_server.ApprovalGate",
        ) as MockGate, patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            MockGate.return_value.list_pending = mock_gate_list
            result = json.loads(await get_inbound_queue())

        assert "items" in result
        # Should have at least 2 items: 1 approval + 1 proposal
        assert len(result["items"]) >= 2
        types_found = {item["type"] for item in result["items"]}
        assert "approval" in types_found
        assert "proposal" in types_found

    @pytest.mark.asyncio
    async def test_handles_partial_failure_with_warnings(self):
        from ad_seller.interfaces.mcp_server import get_inbound_queue

        mock_storage = AsyncMock()
        mock_bus = AsyncMock()
        mock_bus.list_events = AsyncMock(side_effect=Exception("Event bus down"))

        # ApprovalGate works fine but returns empty list
        mock_gate_list = AsyncMock(return_value=[])

        with patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock, return_value=mock_storage,
        ), patch(
            "ad_seller.interfaces.mcp_server.ApprovalGate",
        ) as MockGate, patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            MockGate.return_value.list_pending = mock_gate_list
            result = json.loads(await get_inbound_queue())

        assert "warnings" in result
        assert len(result["warnings"]) > 0


# =============================================================================
# Task 15: get_buyer_activity tests
# =============================================================================


class TestGetBuyerActivity:
    """Tests for get_buyer_activity composite tool."""

    @pytest.mark.asyncio
    async def test_returns_buyers_grouped_by_identity(self):
        from ad_seller.events.models import Event, EventType
        from ad_seller.interfaces.mcp_server import get_buyer_activity

        # Two events from the same buyer session
        ev1 = Event(
            event_type=EventType.PROPOSAL_RECEIVED,
            session_id="sess-buyer-a",
            proposal_id="prop-100",
            payload={"buyer": "BuyerAgentA"},
            metadata={"agent_id": "agent-a", "agent_url": "https://buyer-a.example.com"},
        )
        ev2 = Event(
            event_type=EventType.NEGOTIATION_STARTED,
            session_id="sess-buyer-a",
            payload={"buyer": "BuyerAgentA"},
            metadata={"agent_id": "agent-a", "agent_url": "https://buyer-a.example.com"},
        )
        # One event from a different buyer
        ev3 = Event(
            event_type=EventType.PROPOSAL_RECEIVED,
            session_id="sess-buyer-b",
            proposal_id="prop-200",
            payload={"buyer": "BuyerAgentB"},
            metadata={"agent_id": "agent-b", "agent_url": "https://buyer-b.example.com"},
        )

        mock_bus = AsyncMock()
        mock_bus.list_events = AsyncMock(return_value=[ev1, ev2, ev3])

        with patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            result = json.loads(await get_buyer_activity(days=7))

        assert "buyers" in result
        assert len(result["buyers"]) == 2
        # Check that activity counts are correct
        buyer_a = next((b for b in result["buyers"] if b["agent_id"] == "agent-a"), None)
        assert buyer_a is not None
        assert len(buyer_a["activity_summary"]) == 2

    @pytest.mark.asyncio
    async def test_respects_days_parameter(self):
        from ad_seller.interfaces.mcp_server import get_buyer_activity

        mock_bus = AsyncMock()
        mock_bus.list_events = AsyncMock(return_value=[])

        with patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            result = json.loads(await get_buyer_activity(days=3))

        assert "buyers" in result
        assert result["buyers"] == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_gracefully(self):
        from ad_seller.interfaces.mcp_server import get_buyer_activity

        mock_bus = AsyncMock()
        mock_bus.list_events = AsyncMock(return_value=[])

        with patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            result = json.loads(await get_buyer_activity())

        assert result["buyers"] == []
        assert result["count"] == 0


# =============================================================================
# Task 16: list_configurable_flows tests
# =============================================================================


class TestListConfigurableFlows:
    """Tests for list_configurable_flows composite tool."""

    @pytest.mark.asyncio
    async def test_returns_approval_gate_config(self):
        from ad_seller.interfaces.mcp_server import list_configurable_flows

        settings = _make_settings(
            approval_gate_enabled=True,
            approval_timeout_hours=48,
            approval_required_flows="proposal_decision,deal_registration",
        )

        mock_bus = AsyncMock()
        mock_bus._subscribers = {"proposal.received": [lambda e: None]}

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings,
        ), patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            result = json.loads(await list_configurable_flows())

        ag = result["approval_gates"]
        assert ag["enabled"] is True
        assert ag["timeout_hours"] == 48
        assert "proposal_decision" in ag["required_flows"]
        assert ag["configurable"] is True

    @pytest.mark.asyncio
    async def test_returns_guard_conditions(self):
        from ad_seller.interfaces.mcp_server import list_configurable_flows

        settings = _make_settings()

        mock_bus = AsyncMock()
        mock_bus._subscribers = {}

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings,
        ), patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            result = json.loads(await list_configurable_flows())

        gc = result["guard_conditions"]
        assert len(gc["rules"]) > 0
        # Each rule should have from/to/description
        rule = gc["rules"][0]
        assert "from_status" in rule
        assert "to_status" in rule
        assert "description" in rule
        assert gc["configurable"] is True

    @pytest.mark.asyncio
    async def test_each_section_has_configurable_hint(self):
        from ad_seller.interfaces.mcp_server import list_configurable_flows

        settings = _make_settings()

        mock_bus = AsyncMock()
        mock_bus._subscribers = {}

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings,
        ), patch(
            "ad_seller.interfaces.mcp_server.get_event_bus",
            new_callable=AsyncMock, return_value=mock_bus,
        ):
            result = json.loads(await list_configurable_flows())

        for section in ["approval_gates", "guard_conditions", "event_flows"]:
            assert section in result, f"Missing section: {section}"
            assert "configurable" in result[section], f"No configurable hint in {section}"
