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
            index_exchange_api_url="https://app.indexexchange.com",
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
            index_exchange_api_url="https://app.indexexchange.com",
            index_exchange_api_key="key",
            ssp_routing_rules="ctv:index_exchange,display:index_exchange",
        )
        registry = build_ssp_registry(settings)
        client = registry.get_client_for(inventory_type="ctv")
        assert client.ssp_type == SSPType.INDEX_EXCHANGE


# =========================================================================
# Index Exchange REST client
# =========================================================================


def _ix_response(**overrides) -> dict:
    """A realistic /v3/deals response shape, per deals-web-api's BaseDealResponseResource."""
    base = {
        "internalDealID": 987654,
        "externalDealID": "IX-DEAL-001",
        "classID": 1,
        "name": "Test Deal",
        "account": {"accountID": 12345},
        "startDate": "2026-01-01",
        "endDate": "2026-12-31",
        "auctionType": "first",
        "floor": 15.0,
        "status": "active",
        "directConfigurations": {
            "dspID": 5551,
            "seatIDs": ["seat-100"],
            "programmaticGuaranteed": False,
            "impressionGoal": 1_000_000,
        },
        "labels": {"advertiser": "Test Advertiser"},
        "targeting": [
            {
                "targetingType": "standard",
                "keyName": "Country",
                "sets": [{"values": [{"value": "US"}], "operator": "ANY_OF"}],
            }
        ],
    }
    base.update(overrides)
    return base


def _mock_response(json_data=None, headers=None, status_error=None):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    if status_error:
        resp.raise_for_status.side_effect = status_error
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _valid_create_request(**overrides) -> SSPDealCreateRequest:
    defaults = dict(
        deal_type=SSPDealType.PMP,
        name="Test PMP Deal",
        advertiser="Test Advertiser",
        cpm=15.0,
        start_date="2026-01-01",
        end_date="2026-12-31",
        buyer_seat_ids=["seat-100"],
        impressions_goal=1_000_000,
        external_deal_id="EXT-DEAL-001",
        account_id=12345,
        dsp_id=5551,
    )
    defaults.update(overrides)
    return SSPDealCreateRequest(**defaults)


class TestIndexExchangeClient:
    """Index Exchange REST client formats /v3/deals requests/responses correctly."""

    # --- create_deal() ---

    @pytest.mark.asyncio
    async def test_create_deal_pmp_payload_shape(self):
        client = IndexExchangeSSPClient(
            base_url="https://app.indexexchange.com", api_key="test-key"
        )
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(_ix_response())
        client._http = mock_http

        request = _valid_create_request(deal_type=SSPDealType.PMP)
        result = await client.create_deal(request)

        call_args = mock_http.post.call_args
        assert call_args[0][0] == "/api/deals/v3/deals"
        body = call_args[1]["json"]

        assert body["classID"] == 1
        assert body["name"] == "Test PMP Deal"
        assert body["externalDealID"] == "EXT-DEAL-001"
        assert body["account"] == {"accountID": 12345}
        assert body["startDate"] == "2026-01-01"
        assert body["endDate"] == "2026-12-31"
        assert body["auctionType"] == "first"
        assert body["floor"] == 15.0
        assert body["directConfigurations"] == {
            "dspID": 5551,
            "programmaticGuaranteed": False,
            "seatIDs": ["seat-100"],
            "impressionGoal": 1_000_000,
        }
        assert body["labels"] == {"advertiser": "Test Advertiser"}

        # Old invented field names must be gone
        for old_field in (
            "deal_type",
            "deal_name",
            "advertiser_name",
            "floor_price",
            "currency",
            "start_date",
            "end_date",
            "buyer_seat_ids",
            "impression_goal",
        ):
            assert old_field not in body

        assert result.ssp_type == SSPType.INDEX_EXCHANGE
        assert result.status == SSPDealStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_create_deal_pg_sets_fixed_auction_and_programmatic_guaranteed(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(_ix_response())
        client._http = mock_http

        await client.create_deal(_valid_create_request(deal_type=SSPDealType.PG))

        body = mock_http.post.call_args[1]["json"]
        assert body["auctionType"] == "fixed"
        assert body["directConfigurations"]["programmaticGuaranteed"] is True

    @pytest.mark.asyncio
    async def test_create_deal_preferred_sets_fixed_auction_not_guaranteed(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(_ix_response())
        client._http = mock_http

        await client.create_deal(_valid_create_request(deal_type=SSPDealType.PREFERRED))

        body = mock_http.post.call_args[1]["json"]
        assert body["auctionType"] == "fixed"
        assert body["directConfigurations"]["programmaticGuaranteed"] is False

    @pytest.mark.asyncio
    async def test_create_deal_auction_package_uses_marketplace_configurations(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(_ix_response(classID=4))
        client._http = mock_http

        await client.create_deal(_valid_create_request(deal_type=SSPDealType.AUCTION_PACKAGE))

        body = mock_http.post.call_args[1]["json"]
        assert body["classID"] == 4
        assert body["marketplaceConfigurations"] == {"dspID": 5551}
        assert "directConfigurations" not in body

    @pytest.mark.asyncio
    async def test_create_deal_missing_external_deal_id_raises(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        client._http = AsyncMock()

        with pytest.raises(ValueError, match="external_deal_id"):
            await client.create_deal(_valid_create_request(external_deal_id=None))

    @pytest.mark.asyncio
    async def test_create_deal_missing_account_id_raises(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        client._http = AsyncMock()

        with pytest.raises(ValueError, match="account_id"):
            await client.create_deal(_valid_create_request(account_id=None))

    @pytest.mark.asyncio
    async def test_create_deal_missing_dsp_id_raises(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        client._http = AsyncMock()

        with pytest.raises(ValueError, match="dsp_id"):
            await client.create_deal(_valid_create_request(dsp_id=None))

    # --- clone_deal() ---

    @pytest.mark.asyncio
    async def test_clone_deal_composes_get_and_create(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response())
        mock_http.post.return_value = _mock_response(_ix_response())
        client._http = mock_http

        await client.clone_deal("987654")

        assert mock_http.get.call_args[0][0] == "/api/deals/v3/deals/987654"
        assert mock_http.post.call_args[0][0] == "/api/deals/v3/deals"
        body = mock_http.post.call_args[1]["json"]
        assert body["externalDealID"] != "IX-DEAL-001"
        assert body["externalDealID"].startswith("IX-DEAL-001-clone-")
        assert body["account"] == {"accountID": 12345}
        assert body["directConfigurations"]["dspID"] == 5551

    @pytest.mark.asyncio
    async def test_clone_deal_marketplace_package_reads_dsp_from_marketplace_configurations(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(
            _ix_response(
                classID=4,
                directConfigurations={},
                marketplaceConfigurations={"dspID": 777},
            )
        )
        mock_http.post.return_value = _mock_response(_ix_response(classID=4))
        client._http = mock_http

        await client.clone_deal("987654")

        body = mock_http.post.call_args[1]["json"]
        assert body["classID"] == 4
        assert body["marketplaceConfigurations"] == {"dspID": 777}

    @pytest.mark.asyncio
    async def test_clone_deal_applies_overrides(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response())
        mock_http.post.return_value = _mock_response(_ix_response())
        client._http = mock_http

        await client.clone_deal(
            "987654", overrides={"external_deal_id": "MY-CUSTOM-ID", "name": "Custom Name"}
        )

        body = mock_http.post.call_args[1]["json"]
        assert body["externalDealID"] == "MY-CUSTOM-ID"
        assert body["name"] == "Custom Name"

    @pytest.mark.asyncio
    async def test_clone_deal_generated_id_never_exceeds_64_chars(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        long_source_id = "a" * 64  # already at IX's max length
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response(externalDealID=long_source_id))
        mock_http.post.return_value = _mock_response(_ix_response())
        client._http = mock_http

        await client.clone_deal("987654")

        body = mock_http.post.call_args[1]["json"]
        assert len(body["externalDealID"]) <= 64
        assert body["externalDealID"] != long_source_id
        assert "-clone-" in body["externalDealID"]

    # --- get_deal() ---

    @pytest.mark.asyncio
    async def test_get_deal_uses_v3_path(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response())
        client._http = mock_http

        await client.get_deal("987654")

        assert mock_http.get.call_args[0][0] == "/api/deals/v3/deals/987654"

    # --- list_deals() ---

    @pytest.mark.asyncio
    async def test_list_deals_uses_pageSize_pageOffset(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response({"totalCount": 1, "deals": [_ix_response()]})
        client._http = mock_http

        deals = await client.list_deals(limit=50)

        assert mock_http.get.call_args[0][0] == "/api/deals/v3/deals"
        params = mock_http.get.call_args[1]["params"]
        assert params["pageSize"] == 50
        assert params["pageOffset"] == 0
        assert "limit" not in params
        assert len(deals) == 1

    @pytest.mark.parametrize(
        "status,expected_param",
        [
            (SSPDealStatus.ACTIVE, "active"),
            (SSPDealStatus.PAUSED, "paused"),
            (SSPDealStatus.EXPIRED, "expired"),
        ],
    )
    @pytest.mark.asyncio
    async def test_list_deals_status_mapping(self, status, expected_param):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response({"totalCount": 0, "deals": []})
        client._http = mock_http

        await client.list_deals(status=status)

        assert mock_http.get.call_args[1]["params"]["status"] == expected_param

    @pytest.mark.parametrize("status", [SSPDealStatus.CREATED, SSPDealStatus.ARCHIVED])
    @pytest.mark.asyncio
    async def test_list_deals_sends_no_status_param_when_unmapped(self, status):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response({"totalCount": 0, "deals": []})
        client._http = mock_http

        await client.list_deals(status=status)

        assert "status" not in mock_http.get.call_args[1]["params"]

    # --- update_deal() ---

    @pytest.mark.asyncio
    async def test_update_deal_uses_patch_with_if_match_etag(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response(), headers={"ETag": '"etag-123"'})
        mock_http.patch.return_value = _mock_response(_ix_response(status="paused"))
        client._http = mock_http

        await client.update_deal("987654", {"status": "paused"})

        assert mock_http.get.call_args[0][0] == "/api/deals/v3/deals/987654"
        assert mock_http.patch.call_args[0][0] == "/api/deals/v3/deals/987654"
        assert mock_http.patch.call_args[1]["json"] == {"status": "paused"}
        assert mock_http.patch.call_args[1]["headers"] == {"If-Match": '"etag-123"'}

    @pytest.mark.asyncio
    async def test_update_deal_raises_without_etag(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response(), headers={})
        client._http = mock_http

        with pytest.raises(ValueError, match="ETag"):
            await client.update_deal("987654", {"status": "paused"})

    @pytest.mark.asyncio
    async def test_update_deal_412_propagates(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response(), headers={"ETag": '"stale"'})
        mock_http.patch.return_value = _mock_response(
            status_error=httpx.HTTPStatusError(
                "Precondition Failed", request=MagicMock(), response=MagicMock(status_code=412)
            )
        )
        client._http = mock_http

        with pytest.raises(httpx.HTTPStatusError):
            await client.update_deal("987654", {"status": "paused"})

    # --- _parse_deal() ---

    def test_parse_deal_maps_fields(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        deal = client._parse_deal(_ix_response())

        assert deal.deal_id == "IX-DEAL-001"
        assert deal.cpm == 15.0
        assert deal.start_date == "2026-01-01"
        assert deal.end_date == "2026-12-31"
        assert deal.impressions_goal == 1_000_000
        assert deal.advertiser == "Test Advertiser"
        assert deal.currency == "USD"
        assert deal.targeting == _ix_response()["targeting"]

    @pytest.mark.parametrize(
        "status_str,expected",
        [
            ("active", SSPDealStatus.ACTIVE),
            ("paused", SSPDealStatus.PAUSED),
            ("expired", SSPDealStatus.EXPIRED),
            ("auto-paused", SSPDealStatus.PAUSED),
        ],
    )
    def test_parse_deal_status_map(self, status_str, expected):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        deal = client._parse_deal(_ix_response(status=status_str))
        assert deal.status == expected

    def test_status_map_has_no_archived_or_pending(self):
        from ad_seller.clients.ssp_index_exchange import _IX_STATUS_MAP

        assert "archived" not in _IX_STATUS_MAP
        assert "pending" not in _IX_STATUS_MAP

    def test_parse_deal_maps_pg_type(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        deal = client._parse_deal(
            _ix_response(
                classID=1,
                auctionType="fixed",
                directConfigurations={"programmaticGuaranteed": True},
            )
        )
        assert deal.deal_type == SSPDealType.PG

    def test_parse_deal_maps_pmp_type(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        deal = client._parse_deal(
            _ix_response(
                classID=1,
                auctionType="first",
                directConfigurations={"programmaticGuaranteed": False},
            )
        )
        assert deal.deal_type == SSPDealType.PMP

    def test_parse_deal_maps_preferred_type(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        deal = client._parse_deal(
            _ix_response(
                classID=1,
                auctionType="fixed",
                directConfigurations={"programmaticGuaranteed": False},
            )
        )
        assert deal.deal_type == SSPDealType.PREFERRED

    def test_parse_deal_maps_auction_package_type(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        deal = client._parse_deal(_ix_response(classID=4, directConfigurations={}))
        assert deal.deal_type == SSPDealType.AUCTION_PACKAGE

    # --- troubleshoot_deal() ---

    @pytest.mark.parametrize(
        "status_str,expected_score",
        [("active", 90), ("paused", 50), ("auto-paused", 20), ("expired", 0)],
    )
    @pytest.mark.asyncio
    async def test_troubleshoot_deal_health_score_by_status(self, status_str, expected_score):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(_ix_response(status=status_str))
        client._http = mock_http

        result = await client.troubleshoot_deal("987654")

        assert result.health_score == expected_score
        assert result.deal_id == "IX-DEAL-001"

    @pytest.mark.asyncio
    async def test_troubleshoot_deal_flags_missing_seat_ids(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(
            _ix_response(directConfigurations={"dspID": 5551, "seatIDs": []})
        )
        client._http = mock_http

        result = await client.troubleshoot_deal("987654")

        assert any("seat" in issue.lower() for issue in result.primary_issues)

    @pytest.mark.asyncio
    async def test_troubleshoot_deal_api_failure_returns_unreachable(self):
        client = IndexExchangeSSPClient(base_url="https://app.indexexchange.com")
        mock_http = AsyncMock()
        mock_http.get.side_effect = httpx.ConnectError("connection refused")
        client._http = mock_http

        result = await client.troubleshoot_deal("987654")

        assert result.status == "unreachable"
        assert result.health_score == 0


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
            base_url="https://app.indexexchange.com",
            api_key="test-key",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )
        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        client._http = mock_http

        request = SSPDealCreateRequest(
            deal_type=SSPDealType.PMP,
            name="Test Deal",
            cpm=15.0,
            external_deal_id="EXT-DEAL-001",
            account_id=12345,
            dsp_id=5551,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.create_deal(request)


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
        deal = SSPDeal(
            deal_id=f"deal-{self.ssp_name}", ssp_type=self.ssp_type, ssp_name=self.ssp_name
        )
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
