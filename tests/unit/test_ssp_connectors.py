# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for SSP connectors.

Covers:
- SSP factory creates correct client type based on config
- Index Exchange REST client formats deal payload correctly
- SSP error (timeout, 500) returns graceful error
- Deal distribution to multiple SSPs collects per-SSP results
"""

import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ad_seller.clients.ssp_base import (
    SSPClient,
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPDealType,
    SSPRegistry,
    SSPTroubleshootResult,
    SSPType,
)
from ad_seller.clients.ssp_index_exchange import IndexExchangeSSPClient


def _make_settings(**overrides):
    defaults = {
        "ssp_connectors": "",
        "ssp_routing_rules": "",
        "pubmatic_mcp_url": "",
        "pubmatic_api_key": "",
        "magnite_api_url": "",
        "magnite_api_key": "",
        "index_exchange_api_url": "",
        "index_exchange_api_key": "",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


# =========================================================================
# SSP Factory
# =========================================================================


class TestSSPFactory:
    """SSP factory creates correct client type based on config."""

    def test_creates_index_exchange_client(self):
        from ad_seller.clients.ssp_factory import build_ssp_registry

        settings = _make_settings(
            ssp_connectors="index_exchange",
            index_exchange_api_url="https://api.indexexchange.com",
            index_exchange_api_key="test-key",
        )
        registry = build_ssp_registry(settings)
        client = registry.get_client("index_exchange")
        assert isinstance(client, IndexExchangeSSPClient)
        assert client.ssp_type == SSPType.INDEX_EXCHANGE

    def test_creates_pubmatic_mcp_client(self):
        from ad_seller.clients.ssp_factory import build_ssp_registry

        settings = _make_settings(
            ssp_connectors="pubmatic",
            pubmatic_mcp_url="https://mcp.pubmatic.com/sses",
            pubmatic_api_key="test-key",
        )
        registry = build_ssp_registry(settings)
        client = registry.get_client("pubmatic")
        assert client.ssp_type == SSPType.PUBMATIC
        assert client.ssp_name == "PubMatic"

    def test_empty_connectors_returns_empty_registry(self):
        from ad_seller.clients.ssp_factory import build_ssp_registry

        settings = _make_settings(ssp_connectors="")
        registry = build_ssp_registry(settings)
        assert registry.list_ssps() == []

    def test_unknown_connector_skipped(self):
        from ad_seller.clients.ssp_factory import build_ssp_registry

        settings = _make_settings(ssp_connectors="unknown_ssp")
        registry = build_ssp_registry(settings)
        assert registry.list_ssps() == []

    def test_missing_url_skipped(self):
        from ad_seller.clients.ssp_factory import build_ssp_registry

        settings = _make_settings(
            ssp_connectors="index_exchange",
            index_exchange_api_url="",  # not set
        )
        registry = build_ssp_registry(settings)
        assert registry.list_ssps() == []

    def test_routing_rules_applied(self):
        from ad_seller.clients.ssp_factory import build_ssp_registry

        settings = _make_settings(
            ssp_connectors="index_exchange",
            index_exchange_api_url="https://api.indexexchange.com",
            index_exchange_api_key="key",
            ssp_routing_rules="ctv:index_exchange,display:index_exchange",
        )
        registry = build_ssp_registry(settings)
        client = registry.get_client_for(inventory_type="ctv")
        assert client.ssp_type == SSPType.INDEX_EXCHANGE


# =========================================================================
# Index Exchange REST client
# =========================================================================


class TestIndexExchangeClient:
    """Index Exchange REST client formats deal payload correctly."""

    @pytest.mark.asyncio
    async def test_create_deal_payload_format(self):
        client = IndexExchangeSSPClient(
            base_url="https://api.indexexchange.com",
            api_key="test-key",
        )
        # Manually set the http client to a mock
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "deal_id": "ix-deal-001",
            "deal_name": "Test Deal",
            "deal_type": "pmp",
            "status": "active",
            "floor_price": 15.0,
            "currency": "USD",
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        client._http = mock_http

        request = SSPDealCreateRequest(
            deal_type=SSPDealType.PMP,
            name="Test PMP Deal",
            advertiser="Test Advertiser",
            cpm=15.0,
            currency="USD",
            buyer_seat_ids=["seat-100"],
            impressions_goal=1_000_000,
        )

        result = await client.create_deal(request)

        # Verify the call was made with correct IX-specific field names
        call_args = mock_http.post.call_args
        assert call_args[0][0] == "/api/deals"
        body = call_args[1]["json"]
        assert body["deal_type"] == "pmp"
        assert body["deal_name"] == "Test PMP Deal"
        assert body["advertiser_name"] == "Test Advertiser"
        assert body["floor_price"] == 15.0
        assert body["buyer_seat_ids"] == ["seat-100"]
        assert body["impression_goal"] == 1_000_000

        # Verify parsed result
        assert result.deal_id == "ix-deal-001"
        assert result.ssp_type == SSPType.INDEX_EXCHANGE
        assert result.status == SSPDealStatus.ACTIVE

    def test_parse_deal_maps_status(self):
        client = IndexExchangeSSPClient(
            base_url="https://api.indexexchange.com",
        )
        deal = client._parse_deal({"deal_id": "d1", "status": "paused"})
        assert deal.status == SSPDealStatus.PAUSED

    def test_parse_deal_maps_pg_type(self):
        client = IndexExchangeSSPClient(
            base_url="https://api.indexexchange.com",
        )
        deal = client._parse_deal({"deal_id": "d1", "deal_type": "programmatic_guaranteed"})
        assert deal.deal_type == SSPDealType.PG


# =========================================================================
# SSP Error handling
# =========================================================================


class TestSSPErrors:
    """SSP errors return graceful results."""

    @pytest.mark.asyncio
    async def test_rest_client_not_connected_raises(self):
        from ad_seller.clients.ssp_rest_client import RESTSSPClient

        client = RESTSSPClient(ssp_type=SSPType.CUSTOM, ssp_name="Test")
        # Not connected — should raise ConnectionError
        with pytest.raises(ConnectionError):
            await client.create_deal(SSPDealCreateRequest())

    @pytest.mark.asyncio
    async def test_connect_without_url_raises(self):
        from ad_seller.clients.ssp_rest_client import RESTSSPClient

        client = RESTSSPClient(ssp_type=SSPType.CUSTOM, ssp_name="Test")
        with pytest.raises(ConnectionError, match="Base URL not configured"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        client = IndexExchangeSSPClient(
            base_url="https://api.indexexchange.com",
            api_key="test-key",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )
        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        client._http = mock_http

        with pytest.raises(httpx.HTTPStatusError):
            await client.create_deal(SSPDealCreateRequest())


# =========================================================================
# SSP Registry (deal distribution)
# =========================================================================


class _MockSSPClient(SSPClient):
    """Minimal mock SSP client for registry tests."""

    def __init__(self, name: str, ssp_type: SSPType = SSPType.CUSTOM):
        self.ssp_type = ssp_type
        self.ssp_name = name
        self.deals_created = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def create_deal(self, request):
        deal = SSPDeal(deal_id=f"deal-{self.ssp_name}", ssp_type=self.ssp_type, ssp_name=self.ssp_name)
        self.deals_created.append(deal)
        return deal

    async def clone_deal(self, source_deal_id, overrides=None):
        return SSPDeal(deal_id=f"clone-{source_deal_id}", ssp_type=self.ssp_type)

    async def get_deal(self, deal_id):
        return SSPDeal(deal_id=deal_id, ssp_type=self.ssp_type)

    async def list_deals(self, *, status=None, limit=100):
        return []

    async def update_deal(self, deal_id, updates):
        return SSPDeal(deal_id=deal_id, ssp_type=self.ssp_type)

    async def troubleshoot_deal(self, deal_id):
        return SSPTroubleshootResult(deal_id=deal_id, ssp_type=self.ssp_type)


class TestSSPRegistry:
    """Deal distribution to multiple SSPs collects per-SSP results."""

    @pytest.mark.asyncio
    async def test_distribute_to_multiple_ssps(self):
        registry = SSPRegistry()
        client_a = _MockSSPClient("ssp-a")
        client_b = _MockSSPClient("ssp-b")
        registry.register("ssp-a", client_a)
        registry.register("ssp-b", client_b)

        request = SSPDealCreateRequest(deal_type=SSPDealType.PMP, cpm=15.0)
        results = {}
        for ssp_name in registry.list_ssps():
            client = registry.get_client(ssp_name)
            deal = await client.create_deal(request)
            results[ssp_name] = deal

        assert len(results) == 2
        assert results["ssp-a"].deal_id == "deal-ssp-a"
        assert results["ssp-b"].deal_id == "deal-ssp-b"

    def test_routing_by_inventory_type(self):
        registry = SSPRegistry()
        client_ctv = _MockSSPClient("ctv-ssp")
        client_display = _MockSSPClient("display-ssp")
        registry.register("ctv-ssp", client_ctv)
        registry.register("display-ssp", client_display)
        registry.set_routing_rules({"ctv": "ctv-ssp", "display": "display-ssp"})

        routed = registry.get_client_for(inventory_type="ctv")
        assert routed.ssp_name == "ctv-ssp"

        routed = registry.get_client_for(inventory_type="display")
        assert routed.ssp_name == "display-ssp"

    def test_fallback_to_default_ssp(self):
        registry = SSPRegistry()
        client = _MockSSPClient("default-ssp")
        registry.register("default-ssp", client)

        routed = registry.get_client_for(inventory_type="unknown_type")
        assert routed.ssp_name == "default-ssp"

    def test_no_clients_raises(self):
        registry = SSPRegistry()
        with pytest.raises(RuntimeError, match="No SSP clients registered"):
            registry.get_client_for()

    def test_get_nonexistent_client_raises(self):
        registry = SSPRegistry()
        with pytest.raises(KeyError):
            registry.get_client("nonexistent")
