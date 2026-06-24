"""Unit tests for S3CsvAdServerClient.

Tests the S3-backed ad server adapter with mocked boto3 calls.
Validates: glob pattern matching, CSV merging, caching, and error handling.
"""

import csv
import io
import time
from unittest.mock import MagicMock, patch

import pytest

from ad_seller.clients.s3_csv_adapter import S3CsvAdServerClient

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_s3_client():
    """Create a mocked S3 client."""
    with patch("ad_seller.clients.s3_csv_adapter.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        yield mock_client


@pytest.fixture
def adapter(mock_s3_client):
    """Create an S3CsvAdServerClient with mocked S3."""
    client = S3CsvAdServerClient(
        bucket="test-bucket",
        prefix="seller-data/",
        region="us-west-2",
        cache_ttl=60,
    )
    client._s3 = mock_s3_client
    return client


def _make_csv_content(rows: list[dict]) -> bytes:
    """Helper to create CSV bytes from row dicts."""
    if not rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


# ============================================================================
# Tests: _list_csv_files (S3 glob)
# ============================================================================


class TestListCsvFiles:
    def test_finds_base_and_overlays(self, adapter, mock_s3_client):
        """Should find inventory.csv + inventory_nineseven.csv."""
        mock_s3_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "seller-data/inventory.csv"},
                    {"Key": "seller-data/inventory_nineseven.csv"},
                    {"Key": "seller-data/inventory_prosiebensat1.csv"},
                    {"Key": "seller-data/audiences.csv"},
                    {"Key": "seller-data/audiences_nineseven.csv"},
                ]
            }
        ]

        result = adapter._list_csv_files("inventory")
        assert len(result) == 3
        assert "seller-data/inventory.csv" in result
        assert "seller-data/inventory_nineseven.csv" in result
        assert "seller-data/inventory_prosiebensat1.csv" in result
        # Should NOT include audiences files
        assert "seller-data/audiences.csv" not in result

    def test_returns_empty_on_no_match(self, adapter, mock_s3_client):
        """Should return empty list if no matching files."""
        mock_s3_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "seller-data/audiences.csv"}]}
        ]

        result = adapter._list_csv_files("inventory")
        assert result == []

    def test_handles_empty_bucket(self, adapter, mock_s3_client):
        """Should handle empty S3 prefix gracefully."""
        mock_s3_client.get_paginator.return_value.paginate.return_value = [{}]

        result = adapter._list_csv_files("inventory")
        assert result == []


# ============================================================================
# Tests: _read_csv (merge + cache)
# ============================================================================


class TestReadCsv:
    def test_merges_multiple_files(self, adapter, mock_s3_client):
        """Should merge rows from base + overlay files."""
        # Setup: 2 inventory files
        base_rows = [
            {
                "id": "inv-1",
                "name": "Product 1",
                "status": "ACTIVE",
                "inventory_type": "ctv",
                "floor_price_cpm": "45.00",
            },
            {
                "id": "inv-2",
                "name": "Product 2",
                "status": "ACTIVE",
                "inventory_type": "video",
                "floor_price_cpm": "20.00",
            },
        ]
        overlay_rows = [
            {
                "id": "inv-au-1",
                "name": "AU Product",
                "status": "ACTIVE",
                "inventory_type": "ctv",
                "floor_price_cpm": "38.00",
            },
        ]

        mock_s3_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "seller-data/inventory.csv"},
                    {"Key": "seller-data/inventory_nineseven.csv"},
                ]
            }
        ]

        def get_object_side_effect(Bucket, Key):
            if "nineseven" in Key:
                content = _make_csv_content(overlay_rows)
            else:
                content = _make_csv_content(base_rows)
            return {"Body": MagicMock(read=MagicMock(return_value=content))}

        mock_s3_client.get_object.side_effect = get_object_side_effect

        result = adapter._read_csv("inventory.csv")
        assert len(result) == 3
        assert result[0]["id"] == "inv-1"
        assert result[2]["id"] == "inv-au-1"

    def test_cache_hit(self, adapter, mock_s3_client):
        """Should return cached data without hitting S3 again."""
        # Pre-populate cache
        cached_rows = [{"id": "cached-1", "name": "Cached"}]
        adapter._cache["inventory"] = (cached_rows, time.time())

        result = adapter._read_csv("inventory.csv")
        assert result == cached_rows
        # S3 should NOT have been called
        mock_s3_client.get_paginator.assert_not_called()

    def test_cache_expired(self, adapter, mock_s3_client):
        """Should re-read from S3 when cache expires."""
        # Pre-populate cache with expired entry
        cached_rows = [{"id": "old", "name": "Old"}]
        adapter._cache["inventory"] = (cached_rows, time.time() - 999)

        # Setup S3 response
        fresh_rows = [{"id": "fresh", "name": "Fresh", "status": "ACTIVE"}]
        mock_s3_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "seller-data/inventory.csv"}]}
        ]
        mock_s3_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_make_csv_content(fresh_rows)))
        }

        result = adapter._read_csv("inventory.csv")
        assert result[0]["id"] == "fresh"

    def test_invalidate_cache(self, adapter):
        """invalidate_cache should clear all cached data."""
        adapter._cache["inventory"] = ([{"id": "x"}], time.time())
        adapter._cache["audiences"] = ([{"id": "y"}], time.time())

        adapter.invalidate_cache()
        assert adapter._cache == {}


# ============================================================================
# Tests: list_inventory (async)
# ============================================================================


class TestListInventory:
    @pytest.mark.asyncio
    async def test_returns_inventory_items(self, adapter, mock_s3_client):
        """Should return AdServerInventoryItem objects."""
        rows = [
            {
                "id": "inv-ctv-001",
                "name": "Premium CTV",
                "status": "ACTIVE",
                "sizes": "1920x1080",
                "ad_formats": "video",
                "device_types": "3|7",
                "inventory_type": "ctv",
                "content_categories": "IAB1|IAB17",
                "floor_price_cpm": "45.00",
                "currency": "USD",
                "geo_targets": "US|AU",
                "description": "Premium CTV inventory",
            }
        ]

        mock_s3_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "seller-data/inventory.csv"}]}
        ]
        mock_s3_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_make_csv_content(rows)))
        }

        items = await adapter.list_inventory()
        assert len(items) == 1
        assert items[0].id == "inv-ctv-001"
        assert items[0].name == "Premium CTV"


# ============================================================================
# Tests: list_audience_segments (async)
# ============================================================================


class TestListAudienceSegments:
    @pytest.mark.asyncio
    async def test_returns_segments(self, adapter, mock_s3_client):
        """Should return AdServerAudienceSegment objects."""
        rows = [
            {
                "id": "aud-001",
                "name": "Sports Fans 25-54",
                "description": "Active sports viewers",
                "size": "2500000",
                "segment_type": "behavioral",
                "status": "ACTIVE",
            }
        ]

        mock_s3_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "seller-data/audiences.csv"}]}
        ]
        mock_s3_client.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_make_csv_content(rows)))
        }

        segments = await adapter.list_audience_segments()
        assert len(segments) == 1
        assert segments[0].id == "aud-001"
        assert segments[0].size == 2500000
