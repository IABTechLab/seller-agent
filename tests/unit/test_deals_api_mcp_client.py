# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for DealsAPIMCPClient.

All tests mock FreeWheelMCPClient.call_tool so no live server is required.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_seller.clients.deals_api_mcp_client import (
    _SELLER_STATUS_MAP,
    DealsAPIMCPClient,
)
from ad_seller.clients.ssp_base import (
    SSPDeal,
    SSPDealCreateRequest,
    SSPDealStatus,
    SSPDealType,
    SSPType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> DealsAPIMCPClient:
    client = DealsAPIMCPClient(
        mcp_url="http://localhost:3100/mcp",
        seller_origin="test.example.com",
    )
    client._mcp_client = MagicMock()
    client._mcp_client.call_tool = AsyncMock()
    return client


def _deal_response(
    *,
    deal_id: str = "ext-123",
    internal_id: str = "abc",
    name: str = "Test Deal",
    seller_status: int = 2,
    floor: float = 10.0,
    currency: str = "USD",
) -> dict:
    """Build a minimal deals_create / deals_status response payload."""
    return {
        "success": True,
        "deal": {
            "id": internal_id,
            "externalDealId": deal_id,
            "name": name,
            "sellerStatus": seller_status,
            "seller": "Test Publisher",
            "terms": {
                "dealFloor": floor,
                "currency": currency,
                "startDate": "2026-01-01T00:00:00Z",
            },
        },
    }


# ---------------------------------------------------------------------------
# _parse_deal
# ---------------------------------------------------------------------------


class TestParseDeal:
    def setup_method(self):
        self.client = _make_client()

    def test_extracts_internal_uuid_as_deal_id(self):
        # deal_id stores internal UUID (used by all MCP tools); external_deal_id is for DSP
        raw = _deal_response(internal_id="uuid-abc", deal_id="IAB-999", seller_status=0)
        deal = self.client._parse_deal(raw)
        assert deal.deal_id == "uuid-abc"
        assert deal.external_deal_id == "IAB-999"

    def test_uses_internal_id_field(self):
        raw = {"success": True, "deal": {"id": "fallback-id", "sellerStatus": 2, "terms": {}}}
        deal = self.client._parse_deal(raw)
        assert deal.deal_id == "fallback-id"
        assert deal.external_deal_id is None

    def test_maps_seller_status_0_to_active(self):
        deal = self.client._parse_deal(_deal_response(seller_status=0))
        assert deal.status == SSPDealStatus.ACTIVE

    def test_maps_seller_status_1_to_paused(self):
        deal = self.client._parse_deal(_deal_response(seller_status=1))
        assert deal.status == SSPDealStatus.PAUSED

    def test_maps_seller_status_2_to_created(self):
        deal = self.client._parse_deal(_deal_response(seller_status=2))
        assert deal.status == SSPDealStatus.CREATED

    def test_maps_seller_status_4_to_expired(self):
        deal = self.client._parse_deal(_deal_response(seller_status=4))
        assert deal.status == SSPDealStatus.EXPIRED

    def test_maps_seller_status_5_to_archived(self):
        deal = self.client._parse_deal(_deal_response(seller_status=5))
        assert deal.status == SSPDealStatus.ARCHIVED

    def test_unknown_seller_status_defaults_to_created(self):
        deal = self.client._parse_deal(_deal_response(seller_status=99))
        assert deal.status == SSPDealStatus.CREATED

    def test_extracts_floor_and_currency(self):
        deal = self.client._parse_deal(_deal_response(floor=25.5, currency="EUR"))
        assert deal.cpm == 25.5
        assert deal.currency == "EUR"

    def test_returns_unknown_for_non_dict_input(self):
        deal = self.client._parse_deal("not a dict")
        assert deal.deal_id == "unknown"
        assert deal.ssp_type == SSPType.CUSTOM  # SSPDeal model default

    def test_handles_missing_terms_gracefully(self):
        raw = {"deal": {"id": "x", "externalDealId": "ext-x", "sellerStatus": 0}}
        deal = self.client._parse_deal(raw)
        assert deal.deal_id == "x"  # internal UUID
        assert deal.external_deal_id == "ext-x"
        assert deal.cpm is None

    def test_ssp_name_and_type_always_set(self):
        deal = self.client._parse_deal(_deal_response())
        assert deal.ssp_name == "IAB Deals MCP"
        assert deal.ssp_type == SSPType.CUSTOM  # SSPDeal model default; excluded from API responses


# ---------------------------------------------------------------------------
# create_deal
# ---------------------------------------------------------------------------


class TestCreateDeal:
    def setup_method(self):
        self.client = _make_client()

    @pytest.mark.asyncio
    async def test_maps_request_fields_to_mcp_args(self):
        self.client._mcp_client.call_tool.return_value = _deal_response(internal_id="new-uuid-1", deal_id="IAB-new-1")

        request = SSPDealCreateRequest(
            name="Q3 Video",
            advertiser="Acme Corp",
            cpm=30.0,
            start_date="2026-07-01T00:00:00Z",
            end_date="2026-09-30T00:00:00Z",
            impressions_goal=500_000,
            buyer_seat_ids=["seat-a", "seat-b"],
            currency="GBP",
        )
        deal = await self.client.create_deal(request)

        call_args = self.client._mcp_client.call_tool.call_args
        tool_name, args = call_args[0]
        assert tool_name == "deals_create"
        assert args["name"] == "Q3 Video"
        assert args["seller"] == "test.example.com"  # configured seller origin, not advertiser
        assert args["dealFloor"] == 30.0
        assert args["startDate"] == "2026-07-01T00:00:00Z"
        assert args["endDate"] == "2026-09-30T00:00:00Z"
        assert args["units"] == 500_000
        assert args["wseat"] == ["seat-a", "seat-b"]
        assert args["currency"] == "GBP"
        assert args["origin"] == "test.example.com"
        assert deal.deal_id == "new-uuid-1"  # internal UUID
        assert deal.external_deal_id == "IAB-new-1"

    @pytest.mark.asyncio
    async def test_uses_defaults_for_missing_optional_fields(self):
        self.client._mcp_client.call_tool.return_value = _deal_response()

        await self.client.create_deal(SSPDealCreateRequest(cpm=5.0))

        call_args = self.client._mcp_client.call_tool.call_args
        _, args = call_args[0]
        assert args["name"] == "Untitled Deal"
        assert args["dealFloor"] == 5.0
        assert "endDate" not in args
        assert "units" not in args

    @pytest.mark.asyncio
    async def test_raises_on_missing_cpm(self):
        with pytest.raises(ValueError, match="cpm is required"):
            await self.client.create_deal(SSPDealCreateRequest())

    @pytest.mark.asyncio
    async def test_pg_deal_sets_guar_flag(self):
        self.client._mcp_client.call_tool.return_value = _deal_response(internal_id="pg-uuid-1")
        await self.client.create_deal(SSPDealCreateRequest(cpm=50.0, deal_type=SSPDealType.PG))

        _, args = self.client._mcp_client.call_tool.call_args[0]
        assert args.get("guar") == 1

    @pytest.mark.asyncio
    async def test_non_pg_deal_has_no_guar_flag(self):
        self.client._mcp_client.call_tool.return_value = _deal_response(internal_id="pmp-uuid-1")
        await self.client.create_deal(SSPDealCreateRequest(cpm=20.0, deal_type=SSPDealType.PMP))

        _, args = self.client._mcp_client.call_tool.call_args[0]
        assert "guar" not in args

    @pytest.mark.asyncio
    async def test_returns_ssp_deal_instance(self):
        self.client._mcp_client.call_tool.return_value = _deal_response(internal_id="ret-uuid-1")
        result = await self.client.create_deal(SSPDealCreateRequest(cpm=10.0))
        assert isinstance(result, SSPDeal)


# ---------------------------------------------------------------------------
# get_deal
# ---------------------------------------------------------------------------


class TestGetDeal:
    def setup_method(self):
        self.client = _make_client()

    @pytest.mark.asyncio
    async def test_calls_deals_status_with_deal_id(self):
        self.client._mcp_client.call_tool.return_value = _deal_response(internal_id="uuid-42", deal_id="IAB-42")
        deal = await self.client.get_deal("uuid-42")

        self.client._mcp_client.call_tool.assert_called_once_with(
            "deals_status", {"dealId": "uuid-42"}
        )
        assert deal.deal_id == "uuid-42"  # internal UUID returned by deals_status
        assert deal.external_deal_id == "IAB-42"


# ---------------------------------------------------------------------------
# list_deals
# ---------------------------------------------------------------------------


class TestListDeals:
    def setup_method(self):
        self.client = _make_client()

    @pytest.mark.asyncio
    async def test_returns_list_of_ssp_deals(self):
        self.client._mcp_client.call_tool.return_value = {
            "deals": [
                {
                    "id": "i1",
                    "externalDealId": "e1",
                    "sellerStatus": 0,
                    "name": "Deal A",
                    "terms": {"dealFloor": 5.0, "currency": "USD"},
                },
                {
                    "id": "i2",
                    "externalDealId": "e2",
                    "sellerStatus": 1,
                    "name": "Deal B",
                    "terms": {"dealFloor": 8.0, "currency": "USD"},
                },
            ]
        }

        deals = await self.client.list_deals()
        assert len(deals) == 2
        assert deals[0].deal_id == "i1"  # internal UUID
        assert deals[0].external_deal_id == "e1"
        assert deals[0].status == SSPDealStatus.ACTIVE
        assert deals[1].deal_id == "i2"  # internal UUID
        assert deals[1].external_deal_id == "e2"
        assert deals[1].status == SSPDealStatus.PAUSED

    @pytest.mark.asyncio
    async def test_caps_page_size_at_100(self):
        self.client._mcp_client.call_tool.return_value = {"deals": []}
        await self.client.list_deals(limit=999)

        _, args = self.client._mcp_client.call_tool.call_args[0]
        assert args["pageSize"] == 100

    @pytest.mark.asyncio
    async def test_passes_status_filter_to_mcp(self):
        self.client._mcp_client.call_tool.return_value = {"deals": []}
        await self.client.list_deals(status=SSPDealStatus.ACTIVE)

        _, args = self.client._mcp_client.call_tool.call_args[0]
        assert args["sellerStatus"] == 0  # ACTIVE maps to sellerStatus=0

    @pytest.mark.asyncio
    async def test_no_status_filter_omits_seller_status_param(self):
        self.client._mcp_client.call_tool.return_value = {"deals": []}
        await self.client.list_deals()

        _, args = self.client._mcp_client.call_tool.call_args[0]
        assert "sellerStatus" not in args

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_non_dict_response(self):
        self.client._mcp_client.call_tool.return_value = "unexpected"
        result = await self.client.list_deals()
        assert result == []


# ---------------------------------------------------------------------------
# clone_deal
# ---------------------------------------------------------------------------


class TestCloneDeal:
    def setup_method(self):
        self.client = _make_client()

    @pytest.mark.asyncio
    async def test_prefixes_name_with_copy_of(self):
        source = _deal_response(internal_id="src-uuid-1", deal_id="IAB-src-1", name="Original Deal", floor=20.0)
        new_deal = _deal_response(internal_id="clone-uuid-1", deal_id="IAB-clone-1", name="Copy of Original Deal")

        # clone_deal: (1) deals_status for source, (2) deals_create
        self.client._mcp_client.call_tool.side_effect = [source, new_deal]
        await self.client.clone_deal("src-uuid-1")

        create_call = self.client._mcp_client.call_tool.call_args_list[1]
        _, args = create_call[0]
        assert args["name"] == "Copy of Original Deal"
        assert args["dealFloor"] == 20.0

    @pytest.mark.asyncio
    async def test_applies_overrides(self):
        source = _deal_response(internal_id="src-uuid-2", deal_id="IAB-src-2", floor=10.0)
        new_deal = _deal_response(internal_id="clone-uuid-2", deal_id="IAB-clone-2")

        # clone_deal: (1) deals_status for source, (2) deals_create
        self.client._mcp_client.call_tool.side_effect = [source, new_deal]
        await self.client.clone_deal("src-uuid-2", overrides={"dealFloor": 99.0, "name": "Custom"})

        create_call = self.client._mcp_client.call_tool.call_args_list[1]
        _, args = create_call[0]
        assert args["dealFloor"] == 99.0
        assert args["name"] == "Custom"


# ---------------------------------------------------------------------------
# troubleshoot_deal
# ---------------------------------------------------------------------------


class TestTroubleshootDeal:
    def setup_method(self):
        self.client = _make_client()

    @pytest.mark.asyncio
    async def test_reports_rejected_buyer_seats(self):
        # deals_status returns labeled summaries under status.buyerStatuses, not top-level buyerSeats
        self.client._mcp_client.call_tool.return_value = {
            "deal": {"id": "d1", "externalDealId": "e1", "sellerStatus": 0, "terms": {}},
            "status": {
                "sellerStatus": "Active",
                "buyerStatuses": [
                    {"seatId": "seat-1", "providerId": "mock", "status": "Rejected", "platformDealId": None},
                    {"seatId": "seat-2", "providerId": "mock", "status": "Approved", "platformDealId": None},
                ],
            },
        }

        result = await self.client.troubleshoot_deal("d1")
        assert len(result.primary_issues) == 1
        assert "seat-1" in result.primary_issues[0]

    @pytest.mark.asyncio
    async def test_no_issues_when_all_seats_approved(self):
        self.client._mcp_client.call_tool.return_value = {
            "deal": {"id": "d1", "externalDealId": "e1", "sellerStatus": 0, "terms": {}},
            "status": {
                "sellerStatus": "Active",
                "buyerStatuses": [
                    {"seatId": "seat-1", "providerId": "mock", "status": "Approved", "platformDealId": None},
                ],
            },
        }

        result = await self.client.troubleshoot_deal("d1")
        assert result.primary_issues == []

    @pytest.mark.asyncio
    async def test_deal_id_preserved_in_result(self):
        self.client._mcp_client.call_tool.return_value = {
            "deal": {"id": "target-deal", "sellerStatus": 2, "terms": {}},
            "status": {"sellerStatus": "Pending", "buyerStatuses": []},
        }

        result = await self.client.troubleshoot_deal("target-deal")
        assert result.deal_id == "target-deal"


# ---------------------------------------------------------------------------
# Status map completeness
# ---------------------------------------------------------------------------


class TestSellerStatusMap:
    def test_all_known_codes_present(self):
        # IAB spec codes: 0=Active, 1=Paused, 2=Pending, 4=Complete, 5=Archived
        assert set(_SELLER_STATUS_MAP.keys()) == {0, 1, 2, 4, 5}

    def test_code_3_is_not_mapped(self):
        # Code 3 is unassigned in the IAB spec — must not silently map to a status
        assert 3 not in _SELLER_STATUS_MAP


# ---------------------------------------------------------------------------
# DealSyncRegistry factory registration
# ---------------------------------------------------------------------------


class TestDealSyncFactory:
    def test_registers_deals_api_mcp_when_configured(self):
        from unittest.mock import MagicMock

        from ad_seller.clients.deal_sync_factory import build_deal_sync_registry
        from ad_seller.clients.deals_api_mcp_client import DealsAPIMCPClient

        settings = MagicMock()
        settings.deal_sync_connectors = "deals_api_mcp"
        settings.deals_api_mcp_url = "http://localhost:3100/mcp"
        settings.deals_api_mcp_key = None
        settings.deals_api_mcp_seller_origin = "publisher.example.com"

        registry = build_deal_sync_registry(settings)
        assert "deals_api_mcp" in registry.list_providers()
        assert isinstance(registry.get_client("deals_api_mcp"), DealsAPIMCPClient)

    def test_registers_nothing_when_url_missing(self):
        from unittest.mock import MagicMock

        from ad_seller.clients.deal_sync_factory import build_deal_sync_registry

        settings = MagicMock()
        settings.deal_sync_connectors = "deals_api_mcp"
        settings.deals_api_mcp_url = None

        registry = build_deal_sync_registry(settings)
        assert registry.list_providers() == []

    def test_empty_connectors_returns_empty_registry(self):
        from unittest.mock import MagicMock

        from ad_seller.clients.deal_sync_factory import build_deal_sync_registry

        settings = MagicMock()
        settings.deal_sync_connectors = ""

        registry = build_deal_sync_registry(settings)
        assert registry.list_providers() == []
