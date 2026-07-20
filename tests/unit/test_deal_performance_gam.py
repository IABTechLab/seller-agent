# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests: GET /api/v1/deals/{id}/performance must return REAL
GAM-report-derived delivery data, not a hardcoded placeholder.

Background (bead ar-j8hl): main's PR #12 wired GAMSoapClient.get_delivery_report
into get_deal_performance; the integration/v2 service-layer refactor regressed
the endpoint to a placeholder that returns the same fabricated numbers
(1,000,000 available / 0 served) for every deal, while the published
gam-reporting guide promises "real delivery figures".

The GAM boundary is mocked the way main's PR #12 tests mock GAM clients
(unittest.mock patch of the client class / settings), so no network or
googleads dependency is exercised.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_seller.services import deal_service

pytestmark = pytest.mark.asyncio


def _mock_storage_with_deals(deals: dict[str, dict]) -> AsyncMock:
    storage = AsyncMock()
    storage.get_deal = AsyncMock(side_effect=lambda did: deals.get(did))
    return storage


def _gam_settings(enabled: bool = True) -> MagicMock:
    s = MagicMock()
    s.gam_enabled = enabled
    s.gam_network_code = "12345678" if enabled else None
    s.gam_json_key_path = "/path/to/creds.json" if enabled else None
    return s


def _mock_gam_client(reports_by_order: dict[str, dict]) -> MagicMock:
    """A GAMSoapClient whose get_delivery_report serves canned per-order data."""
    client = MagicMock()
    client.connect = MagicMock()
    client.disconnect = MagicMock()
    client.get_delivery_report = MagicMock(
        side_effect=lambda order_ids, days=30: reports_by_order[str(order_ids[0])]
    )
    return client


# Two GAM orders with clearly different delivery states
REPORT_ORDER_111 = {
    "orders": [
        {
            "order_id": "111",
            "order_name": "Order for DEMO-AAA",
            "status": "DELIVERING",
            "line_items": [
                {"id": "1", "name": "Line-1", "status": "DELIVERING", "impressions_goal": 500000}
            ],
        }
    ],
    "report_rows": [],
    "summary": {"impressions": 250000, "clicks": 1200, "revenue_usd": 5000.0},
}

REPORT_ORDER_222 = {
    "orders": [
        {
            "order_id": "222",
            "order_name": "Order for DEMO-BBB",
            "status": "DELIVERING",
            "line_items": [
                {"id": "2", "name": "Line-2", "status": "DELIVERING", "impressions_goal": 2000000}
            ],
        }
    ],
    "report_rows": [],
    "summary": {"impressions": 100000, "clicks": 300, "revenue_usd": 1500.0},
}


class TestDealPerformanceRealGAMData:
    """The regression: distinct deals must not share fabricated numbers."""

    async def test_distinct_deals_return_distinct_report_derived_data(self):
        """Two deals trafficked to different GAM orders must report their own
        delivery numbers — the placeholder regression returns identical
        fabricated stats (1,000,000 / 0 / not_started) for both."""
        deals = {
            "DEMO-AAA": {"deal_id": "DEMO-AAA", "gam_order_id": "111"},
            "DEMO-BBB": {"deal_id": "DEMO-BBB", "gam_order_id": "222"},
        }
        gam = _mock_gam_client({"111": REPORT_ORDER_111, "222": REPORT_ORDER_222})

        with (
            patch(
                "ad_seller.storage.factory.get_storage",
                return_value=_mock_storage_with_deals(deals),
            ),
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=gam),
        ):
            perf_a = await deal_service.get_deal_performance("DEMO-AAA")
            perf_b = await deal_service.get_deal_performance("DEMO-BBB")

        assert perf_a["impressions_served"] == 250000
        assert perf_b["impressions_served"] == 100000
        assert perf_a["impressions_served"] != perf_b["impressions_served"], (
            "Distinct deals returned identical delivery stats — "
            "placeholder regression (ar-j8hl)"
        )

    async def test_performance_fields_derive_from_gam_report(self):
        """Served/available/fill/cpm/pacing all come from the GAM report."""
        deals = {"DEMO-AAA": {"deal_id": "DEMO-AAA", "gam_order_id": "111"}}
        gam = _mock_gam_client({"111": REPORT_ORDER_111})

        with (
            patch(
                "ad_seller.storage.factory.get_storage",
                return_value=_mock_storage_with_deals(deals),
            ),
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=gam),
        ):
            perf = await deal_service.get_deal_performance("DEMO-AAA")

        # 250,000 served of a 500,000-impression goal
        assert perf["impressions_available"] == 500000
        assert perf["impressions_served"] == 250000
        assert perf["fill_rate"] == 50.0
        # $5,000 revenue / 250,000 impressions * 1000 = $20 CPM
        assert perf["avg_cpm_actual"] == 20.0
        assert perf["delivery_pacing"] == "on_track"
        # run_report boundary was actually exercised
        gam.get_delivery_report.assert_called_once_with(["111"], days=30)

    async def test_gam_order_id_read_from_metadata_fallback(self):
        """main's PR #12 also honored deal.metadata.gam_order_id."""
        deals = {
            "DEMO-CCC": {"deal_id": "DEMO-CCC", "metadata": {"gam_order_id": "222"}},
        }
        gam = _mock_gam_client({"222": REPORT_ORDER_222})

        with (
            patch(
                "ad_seller.storage.factory.get_storage",
                return_value=_mock_storage_with_deals(deals),
            ),
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=gam),
        ):
            perf = await deal_service.get_deal_performance("DEMO-CCC")

        assert perf["impressions_served"] == 100000


class TestDealPerformanceFallbacks:
    """Behavior preserved from main: placeholder only when GAM can't answer."""

    async def test_placeholder_when_gam_not_configured(self):
        deals = {"DEMO-AAA": {"deal_id": "DEMO-AAA", "gam_order_id": "111"}}

        with (
            patch(
                "ad_seller.storage.factory.get_storage",
                return_value=_mock_storage_with_deals(deals),
            ),
            patch("ad_seller.config.get_settings", return_value=_gam_settings(enabled=False)),
        ):
            perf = await deal_service.get_deal_performance("DEMO-AAA")

        assert perf["impressions_available"] == 1000000
        assert perf["impressions_served"] == 0
        assert perf["delivery_pacing"] == "not_started"

    async def test_placeholder_when_deal_never_trafficked(self):
        """No gam_order_id on the deal — nothing to report against."""
        deals = {"DEMO-AAA": {"deal_id": "DEMO-AAA"}}

        with (
            patch(
                "ad_seller.storage.factory.get_storage",
                return_value=_mock_storage_with_deals(deals),
            ),
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
        ):
            perf = await deal_service.get_deal_performance("DEMO-AAA")

        assert perf["impressions_served"] == 0
        assert perf["impressions_available"] == 1000000

    async def test_gam_error_falls_back_to_placeholder(self):
        """A GAM outage must not 500 the buyer-facing endpoint."""
        deals = {"DEMO-AAA": {"deal_id": "DEMO-AAA", "gam_order_id": "111"}}
        gam = MagicMock()
        gam.connect = MagicMock(side_effect=RuntimeError("GAM unreachable"))

        with (
            patch(
                "ad_seller.storage.factory.get_storage",
                return_value=_mock_storage_with_deals(deals),
            ),
            patch("ad_seller.config.get_settings", return_value=_gam_settings()),
            patch("ad_seller.clients.gam_soap_client.GAMSoapClient", return_value=gam),
        ):
            perf = await deal_service.get_deal_performance("DEMO-AAA")

        assert perf["impressions_available"] == 1000000
        assert perf["impressions_served"] == 0

    async def test_unknown_deal_404s(self):
        from fastapi import HTTPException

        with patch(
            "ad_seller.storage.factory.get_storage",
            return_value=_mock_storage_with_deals({}),
        ):
            with pytest.raises(HTTPException) as exc:
                await deal_service.get_deal_performance("DEMO-NOPE")
        assert exc.value.status_code == 404


def _fake_booking_entities(order_id: str = "54058762"):
    from ad_seller.clients.ad_server_base import (
        AdServerDeal,
        AdServerLineItem,
        AdServerOrder,
    )

    order = AdServerOrder(id=order_id, name="Order for DEMO-AAA", advertiser_id="adv-1")
    line_item = AdServerLineItem(id="li-1", order_id=order_id, name="Line for DEMO-AAA")
    deal = AdServerDeal(id="deal-1", deal_id="DEMO-AAA")
    return order, line_item, deal


class TestGamOrderIdPersistence:
    """The setter side: trafficking a deal into GAM must link gam_order_id
    onto the stored deal record (main's tools/gam/book_deal.py behavior,
    re-homed in GAMAdServerClient.book_deal after EP-8.2 removed the
    abandoned CrewAI tools)."""

    async def test_book_deal_persists_gam_order_id_on_deal_record(self):
        from ad_seller.clients.gam_adapter import GAMAdServerClient

        deals = {"DEMO-AAA": {"deal_id": "DEMO-AAA", "status": "proposed"}}
        storage = AsyncMock()
        storage.get_deal = AsyncMock(side_effect=lambda did: deals.get(did))
        storage.set_deal = AsyncMock(
            side_effect=lambda did, data: deals.__setitem__(did, data)
        )

        with patch("ad_seller.clients.gam_adapter.GAMSoapClient"), patch(
            "ad_seller.clients.gam_adapter.GAMRestClient"
        ):
            adapter = GAMAdServerClient()

        order, line_item, deal = _fake_booking_entities()
        adapter._soap.get_or_create_advertiser = MagicMock(return_value="adv-1")
        adapter.create_order = AsyncMock(return_value=order)
        adapter.create_line_item = AsyncMock(return_value=line_item)
        adapter.create_deal = AsyncMock(return_value=deal)

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            result = await adapter.book_deal("DEMO-AAA", "Acme Advertiser")

        assert result.success is True
        assert deals["DEMO-AAA"]["gam_order_id"] == "54058762"

    async def test_book_deal_survives_storage_failure(self):
        """Linkage is best-effort — a storage error must not fail the booking."""
        from ad_seller.clients.gam_adapter import GAMAdServerClient

        storage = AsyncMock()
        storage.get_deal = AsyncMock(side_effect=RuntimeError("storage down"))

        with patch("ad_seller.clients.gam_adapter.GAMSoapClient"), patch(
            "ad_seller.clients.gam_adapter.GAMRestClient"
        ):
            adapter = GAMAdServerClient()

        order, line_item, deal = _fake_booking_entities()
        adapter._soap.get_or_create_advertiser = MagicMock(return_value="adv-1")
        adapter.create_order = AsyncMock(return_value=order)
        adapter.create_line_item = AsyncMock(return_value=line_item)
        adapter.create_deal = AsyncMock(return_value=deal)

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            result = await adapter.book_deal("DEMO-AAA", "Acme Advertiser")

        assert result.success is True
