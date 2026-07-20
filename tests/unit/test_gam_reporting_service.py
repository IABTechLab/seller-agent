# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the GAM reporting service (main PR #12's /gam surface,
re-implemented in the v2 service layer; consumed by the /gam/orders and
/gam/report REST endpoints and the list_gam_orders / get_gam_delivery_report
MCP tools)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from ad_seller.services import gam_reporting_service

pytestmark = pytest.mark.asyncio


def _gam_settings(enabled: bool = True) -> MagicMock:
    s = MagicMock()
    s.gam_enabled = enabled
    s.gam_network_code = "12345678" if enabled else None
    s.gam_json_key_path = "/path/to/creds.json" if enabled else None
    return s


class TestConfigurationGate:
    async def test_list_orders_503_when_gam_not_configured(self):
        with patch("ad_seller.config.get_settings", return_value=_gam_settings(False)):
            with pytest.raises(HTTPException) as exc:
                await gam_reporting_service.list_gam_orders()
        assert exc.value.status_code == 503

    async def test_report_503_when_gam_not_configured(self):
        with patch("ad_seller.config.get_settings", return_value=_gam_settings(False)):
            with pytest.raises(HTTPException) as exc:
                await gam_reporting_service.get_gam_delivery_report("111")
        assert exc.value.status_code == 503

    async def test_report_400_on_empty_order_ids(self):
        with patch("ad_seller.config.get_settings", return_value=_gam_settings()):
            with pytest.raises(HTTPException) as exc:
                await gam_reporting_service.get_gam_delivery_report(" , ")
        assert exc.value.status_code == 400


class TestListOrders:
    async def test_lists_orders_from_ad_server(self):
        client = MagicMock()
        client.list_orders = MagicMock(
            return_value=[{"id": "111", "name": "Order A", "status": "DELIVERING"}]
        )
        client.get_current_user = MagicMock(
            return_value=MagicMock(id="7", name="Ops", email="ops@example.com")
        )

        with (
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=client),
        ):
            result = await gam_reporting_service.list_gam_orders(limit=10)

        assert result["count"] == 1
        assert result["orders"][0]["id"] == "111"
        assert result["network_code"] == "12345678"
        client.list_orders.assert_called_once_with(limit=10)

    async def test_agent_created_only_resolves_deal_storage_links(self):
        """Deal storage is the source of truth for agent-created orders:
        every stored deal with a gam_order_id is resolved to its GAM order."""
        client = MagicMock()
        client.get_order_by_id = MagicMock(
            return_value={"id": "111", "name": "Order for DEMO-AAA", "status": "DELIVERING"}
        )
        client.get_current_user = MagicMock(return_value=MagicMock())

        storage = AsyncMock()
        storage.list_deals = AsyncMock(
            return_value=[
                {"deal_id": "DEMO-AAA", "gam_order_id": "111"},
                {"deal_id": "DEMO-ZZZ"},  # never trafficked — must be skipped
            ]
        )

        with (
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=client),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
        ):
            result = await gam_reporting_service.list_gam_orders(agent_created_only=True)

        assert result["count"] == 1
        assert result["orders"][0]["external_order_id"] == "DEMO-AAA"
        assert result["orders"][0]["agent_created"] is True
        client.get_order_by_id.assert_called_once_with("111")

    async def test_ad_server_failure_maps_to_502(self):
        client = MagicMock()
        client.connect = MagicMock(side_effect=RuntimeError("SOAP down"))

        with (
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=client),
        ):
            with pytest.raises(HTTPException) as exc:
                await gam_reporting_service.list_gam_orders()
        assert exc.value.status_code == 502


class TestDeliveryReport:
    async def test_report_passes_parsed_ids_and_days(self):
        report = {"orders": [], "report_rows": [], "summary": {"impressions": 5}}
        client = MagicMock()
        client.get_delivery_report = MagicMock(return_value=report)

        with (
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=client),
        ):
            result = await gam_reporting_service.get_gam_delivery_report(
                " 111, 222 ", days=7
            )

        assert result == report
        client.get_delivery_report.assert_called_once_with(["111", "222"], days=7)
