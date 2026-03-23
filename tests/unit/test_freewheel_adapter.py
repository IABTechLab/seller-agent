# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for FreeWheel ad server adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ad_seller.clients.ad_server_base import AdServerType
from ad_seller.clients.freewheel_adapter import FreeWheelAdServerClient


@pytest.fixture
def adapter():
    """Create a FreeWheelAdServerClient with mocked MCP client."""
    client = FreeWheelAdServerClient()
    client._sh_client = AsyncMock()
    client._sh_client.connected = True
    return client


class TestAdapterType:
    def test_ad_server_type(self):
        client = FreeWheelAdServerClient()
        assert client.ad_server_type == AdServerType.FREEWHEEL


class TestOrderMethods:
    """SH programmatic doesn't use orders — all should raise NotImplementedError."""

    @pytest.mark.asyncio
    async def test_create_order_raises(self, adapter):
        with pytest.raises(NotImplementedError, match="does not use orders"):
            await adapter.create_order("Test Order", "adv-123")

    @pytest.mark.asyncio
    async def test_get_order_raises(self, adapter):
        with pytest.raises(NotImplementedError, match="does not use orders"):
            await adapter.get_order("order-123")

    @pytest.mark.asyncio
    async def test_approve_order_raises(self, adapter):
        with pytest.raises(NotImplementedError, match="does not use orders"):
            await adapter.approve_order("order-123")


class TestLineItemMethods:
    """SH programmatic doesn't use line items — all should raise NotImplementedError."""

    @pytest.mark.asyncio
    async def test_create_line_item_raises(self, adapter):
        with pytest.raises(NotImplementedError, match="does not use line items"):
            await adapter.create_line_item("order-123", "Test LI", cost_micros=15_000_000)

    @pytest.mark.asyncio
    async def test_update_line_item_raises(self, adapter):
        with pytest.raises(NotImplementedError, match="does not use line items"):
            await adapter.update_line_item("li-123", {"name": "Updated"})


class TestListInventory:
    @pytest.mark.asyncio
    async def test_list_inventory_calls_mcp(self, adapter):
        adapter._sh_client.call_tool = AsyncMock(return_value=[
            {"id": "pkg-1", "name": "Premium CTV", "status": "ACTIVE"},
            {"id": "pkg-2", "name": "Display ROS", "status": "ACTIVE"},
        ])

        items = await adapter.list_inventory()

        adapter._sh_client.call_tool.assert_called_once_with("list_inventory", {})
        assert len(items) == 2
        assert items[0].id == "pkg-1"
        assert items[0].ad_server_type == AdServerType.FREEWHEEL

    @pytest.mark.asyncio
    async def test_list_inventory_dict_response(self, adapter):
        adapter._sh_client.call_tool = AsyncMock(return_value={
            "items": [{"id": "pkg-1", "name": "Test"}],
        })

        items = await adapter.list_inventory()
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_list_inventory_with_limit(self, adapter):
        adapter._sh_client.call_tool = AsyncMock(return_value=[])

        await adapter.list_inventory(limit=50)
        adapter._sh_client.call_tool.assert_called_once_with("list_inventory", {"limit": 50})


class TestListAudienceSegments:
    @pytest.mark.asyncio
    async def test_list_segments_calls_mcp(self, adapter):
        adapter._sh_client.call_tool = AsyncMock(return_value=[
            {"id": "seg-1", "name": "Sports Fans"},
        ])

        segments = await adapter.list_audience_segments()
        assert len(segments) == 1
        assert segments[0].name == "Sports Fans"


class TestBookDeal:
    @pytest.mark.asyncio
    async def test_book_deal_calls_mcp(self, adapter):
        adapter._sh_client.call_tool = AsyncMock(return_value={
            "id": "fw-deal-001",
            "deal_id": "IAB-ABC123",
            "deal_type": "PD",
            "fixed_price": 25.0,
            "status": "ACTIVE",
        })

        result = await adapter.book_deal(
            deal_id="IAB-ABC123",
            advertiser_name="Test Advertiser",
            deal_type="preferreddeal",
            fixed_price_micros=25_000_000,
        )

        assert result.success is True
        assert result.deal is not None
        assert result.deal.deal_id == "IAB-ABC123"
        assert result.order is None  # No orders for SH programmatic
        assert result.line_items == []

        # Verify MCP was called with dollars (not micros)
        call_args = adapter._sh_client.call_tool.call_args
        assert call_args[0][0] == "book_deal"
        assert call_args[0][1]["fixed_price"] == 25.0


class TestCreateDeal:
    @pytest.mark.asyncio
    async def test_create_deal_calls_book_deal_mcp(self, adapter):
        adapter._sh_client.call_tool = AsyncMock(return_value={
            "id": "fw-deal-002",
            "deal_id": "IAB-DEF456",
            "deal_type": "PA",
            "floor_price": 15.0,
            "status": "ACTIVE",
        })

        deal = await adapter.create_deal(
            deal_id="IAB-DEF456",
            deal_type="private_auction",
            floor_price_micros=15_000_000,
        )

        assert deal.deal_id == "IAB-DEF456"
        assert deal.floor_price_micros == 15_000_000


class TestUpdateDeal:
    @pytest.mark.asyncio
    async def test_update_deal_calls_mcp(self, adapter):
        adapter._sh_client.call_tool = AsyncMock(return_value={
            "id": "fw-deal-001",
            "deal_id": "IAB-ABC123",
            "status": "PAUSED",
        })

        deal = await adapter.update_deal("IAB-ABC123", {"status": "PAUSED"})
        adapter._sh_client.call_tool.assert_called_once()
        assert deal.deal_id == "IAB-ABC123"
