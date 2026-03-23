# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for FreeWheel response normalizer."""

import pytest

from ad_seller.clients.ad_server_base import AdServerType, DealStatus
from ad_seller.clients.freewheel_normalizer import (
    dollars_to_micros,
    micros_to_dollars,
    normalize_audience_segments,
    normalize_booking_result,
    normalize_deal,
    normalize_inventory,
)


class TestPriceConversion:
    def test_dollars_to_micros(self):
        assert dollars_to_micros(15.0) == 15_000_000
        assert dollars_to_micros(28.50) == 28_500_000
        assert dollars_to_micros(0.05) == 50_000
        assert dollars_to_micros(0) == 0

    def test_micros_to_dollars(self):
        assert micros_to_dollars(15_000_000) == 15.0
        assert micros_to_dollars(28_500_000) == 28.5
        assert micros_to_dollars(0) == 0.0


class TestNormalizeInventory:
    def test_basic_inventory(self):
        raw = [
            {"id": "pkg-1", "name": "Premium CTV", "status": "ACTIVE"},
            {"id": "pkg-2", "name": "Display Run-of-Site", "status": "ACTIVE"},
        ]
        items = normalize_inventory(raw)
        assert len(items) == 2
        assert items[0].id == "pkg-1"
        assert items[0].name == "Premium CTV"
        assert items[0].ad_server_type == AdServerType.FREEWHEEL

    def test_empty_inventory(self):
        assert normalize_inventory([]) == []

    def test_size_parsing(self):
        raw = [{"id": "1", "name": "Test", "sizes": ["300x250", "728x90"]}]
        items = normalize_inventory(raw)
        assert items[0].sizes == [(300, 250), (728, 90)]

    def test_tuple_sizes(self):
        raw = [{"id": "1", "name": "Test", "sizes": [[300, 250], [728, 90]]}]
        items = normalize_inventory(raw)
        assert items[0].sizes == [(300, 250), (728, 90)]


class TestNormalizeAudienceSegments:
    def test_basic_segments(self):
        raw = [
            {"id": "seg-1", "name": "Sports Fans", "size": 5000000},
            {"id": "seg-2", "name": "News Readers", "description": "Daily news"},
        ]
        segments = normalize_audience_segments(raw)
        assert len(segments) == 2
        assert segments[0].name == "Sports Fans"
        assert segments[0].size == 5000000
        assert segments[1].description == "Daily news"
        assert segments[0].ad_server_type == AdServerType.FREEWHEEL

    def test_empty_segments(self):
        assert normalize_audience_segments([]) == []


class TestNormalizeDeal:
    def test_basic_deal(self):
        raw = {
            "id": "fw-deal-001",
            "deal_id": "IAB-A1B2C3",
            "name": "Test Deal",
            "deal_type": "PD",
            "fixed_price": 28.0,
            "status": "ACTIVE",
            "buyer_seat_ids": ["ttd-123"],
        }
        deal = normalize_deal(raw)
        assert deal.deal_id == "IAB-A1B2C3"
        assert deal.deal_type == "preferreddeal"
        assert deal.fixed_price_micros == 28_000_000
        assert deal.status == DealStatus.ACTIVE
        assert deal.buyer_seat_ids == ["ttd-123"]
        assert deal.ad_server_type == AdServerType.FREEWHEEL

    def test_draft_status(self):
        raw = {"id": "1", "deal_id": "D1", "status": "DRAFT"}
        deal = normalize_deal(raw)
        assert deal.status == DealStatus.DRAFT

    def test_private_auction(self):
        raw = {"id": "1", "deal_id": "D1", "deal_type": "PA", "floor_price": 15.0}
        deal = normalize_deal(raw)
        assert deal.deal_type == "privateauction"
        assert deal.floor_price_micros == 15_000_000


class TestNormalizeBookingResult:
    def test_successful_booking(self):
        raw = {
            "id": "fw-deal-001",
            "deal_id": "IAB-ABC123",
            "deal_type": "PD",
            "fixed_price": 25.0,
            "status": "ACTIVE",
        }
        result = normalize_booking_result(raw)
        assert result.success is True
        assert result.order is None  # SH programmatic has no orders
        assert result.line_items == []  # SH programmatic has no line items
        assert result.deal is not None
        assert result.deal.deal_id == "IAB-ABC123"
        assert result.ad_server_type == AdServerType.FREEWHEEL

    def test_empty_response(self):
        result = normalize_booking_result({})
        assert result.success is False
