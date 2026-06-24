# Author: AWS
# Contributed to IAB Tech Lab

"""S3-backed ad server client for AgentCore deployments.

Reads CSV data from S3 at runtime — no redeploy needed to update inventory.
Just upload/delete CSVs in the S3 prefix and the agent picks them up.

Uses the same globbing pattern as csv_adapter: reads all files matching
<type>*.csv (e.g. inventory.csv + inventory_nineseven.csv) and merges rows.

Configuration:
    AD_SERVER_TYPE=s3
    S3_DATA_BUCKET=a4a-data-omixaj
    S3_DATA_PREFIX=seller-data/

IAM: The AgentCore execution role must have s3:GetObject and s3:ListBucket
on the configured bucket/prefix. This is already the case for a4a-data-omixaj.
"""

import csv
import io
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from .ad_server_base import (
    AdServerAudienceSegment,
    AdServerClient,
    AdServerDeal,
    AdServerInventoryItem,
    AdServerLineItem,
    AdServerOrder,
    AdServerType,
    BookingResult,
    DealStatus,
    LineItemStatus,
    OrderStatus,
)

logger = logging.getLogger(__name__)

# Cache TTL in seconds — how long to keep S3 data before re-fetching
DEFAULT_CACHE_TTL = 300  # 5 minutes


class S3CsvAdServerClient(AdServerClient):
    """Ad server client backed by CSV files in S3.

    Drop-in replacement for CSVAdServerClient. Reads from S3 instead of
    local filesystem. Supports the same globbing convention: all files
    matching <stem>*.csv in the prefix are merged (additive overlays).

    Cache: Data is cached in-memory for CACHE_TTL seconds. A health check
    or explicit call to invalidate_cache() forces a re-read from S3.
    """

    ad_server_type = AdServerType.CSV  # Reuse CSV type for compatibility

    def __init__(
        self,
        bucket: str,
        prefix: str = "seller-data/",
        region: str = "us-west-2",
        cache_ttl: int = DEFAULT_CACHE_TTL,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/"
        self._region = region
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[list[dict[str, str]], float]] = {}
        self._lock = threading.Lock()
        self._s3 = boto3.client("s3", region_name=region)
        # In-memory store for writes (deals, orders) — ephemeral per session
        self._deals: list[dict[str, str]] = []
        self._orders: list[dict[str, str]] = []
        self._line_items: list[dict[str, str]] = []
        logger.info(
            "S3CsvAdServerClient initialized: s3://%s/%s (cache TTL: %ds)",
            bucket,
            self._prefix,
            cache_ttl,
        )

    # -- S3 Helpers -----------------------------------------------------------

    def _list_csv_files(self, stem: str) -> list[str]:
        """List all S3 keys matching <prefix><stem>*.csv pattern."""
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    filename = key[len(self._prefix) :]
                    # Match: inventory.csv, inventory_nineseven.csv, inventory_prosiebensat1.csv
                    if filename.startswith(stem) and filename.endswith(".csv"):
                        keys.append(key)
            return sorted(keys)
        except ClientError as e:
            logger.error("Failed to list S3 objects for stem '%s': %s", stem, e)
            return []

    def _read_csv_from_s3(self, key: str) -> list[dict[str, str]]:
        """Read a single CSV file from S3 and return list of row dicts."""
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))
            return list(reader)
        except ClientError as e:
            logger.error("Failed to read s3://%s/%s: %s", self._bucket, key, e)
            return []

    def _read_csv(self, filename: str) -> list[dict[str, str]]:
        """Read and merge all CSV files matching the stem pattern.

        Example: _read_csv("inventory.csv") reads:
          - seller-data/inventory.csv
          - seller-data/inventory_nineseven.csv
          - seller-data/inventory_prosiebensat1.csv
        and returns merged rows.

        Results are cached for CACHE_TTL seconds.
        """
        stem = filename.rsplit(".", 1)[0]  # "inventory" from "inventory.csv"

        # Check cache
        with self._lock:
            if stem in self._cache:
                rows, ts = self._cache[stem]
                if time.time() - ts < self._cache_ttl:
                    return rows

        # Cache miss — read from S3
        keys = self._list_csv_files(stem)
        if not keys:
            logger.debug("No S3 objects found for stem '%s'", stem)
            return []

        all_rows = []
        for key in keys:
            rows = self._read_csv_from_s3(key)
            all_rows.extend(rows)
            if rows:
                logger.debug("Read %d rows from s3://%s/%s", len(rows), self._bucket, key)

        # Update cache
        with self._lock:
            self._cache[stem] = (all_rows, time.time())

        logger.info("S3 read: %s → %d keys, %d total rows", stem, len(keys), len(all_rows))
        return all_rows

    def invalidate_cache(self) -> None:
        """Force re-read from S3 on next access."""
        with self._lock:
            self._cache.clear()
        logger.info("S3 cache invalidated")

    # -- Connection (no-op for S3) -------------------------------------------

    async def connect(self) -> None:
        """No connection needed for S3."""
        pass

    async def disconnect(self) -> None:
        """No disconnection needed for S3."""
        pass

    # -- Inventory Operations ------------------------------------------------

    async def list_inventory(
        self,
        *,
        limit: int = 100,
        filter_str: Optional[str] = None,
    ) -> list[AdServerInventoryItem]:
        """List inventory items from S3 CSV files."""
        rows = self._read_csv("inventory.csv")

        items = []
        for row in rows[:limit]:
            # Extra columns go into raw dict (same pattern as CSV adapter)
            raw: dict = {}
            base_fields = {"id", "name", "parent_id", "status", "sizes"}
            for key, value in row.items():
                if key not in base_fields and value:
                    if key in ("ad_formats", "device_types", "content_categories", "geo_targets"):
                        raw[key] = self._split_pipe(value)
                    elif key == "floor_price_cpm":
                        try:
                            raw[key] = float(value)
                        except (ValueError, TypeError):
                            raw[key] = 0.0
                    else:
                        raw[key] = value

            item = AdServerInventoryItem(
                id=row.get("id", ""),
                name=row.get("name", ""),
                parent_id=row.get("parent_id") or None,
                status=row.get("status", "ACTIVE"),
                sizes=self._parse_sizes(row.get("sizes", "")),
                ad_server_type=AdServerType.CSV,
            )
            # Attach raw data for downstream use (floor_price_cpm, inventory_type, etc.)
            item.__dict__["raw"] = raw

            items.append(item)
        return items

    # -- Audience Operations -------------------------------------------------

    async def list_audience_segments(
        self,
        *,
        limit: int = 500,
        filter_str: Optional[str] = None,
    ) -> list[AdServerAudienceSegment]:
        """List audience segments from S3 CSV files."""
        rows = self._read_csv("audiences.csv")

        segments = []
        for row in rows[:limit]:
            segments.append(
                AdServerAudienceSegment(
                    id=row.get("id", ""),
                    name=row.get("name", ""),
                    description=row.get("description", ""),
                    size=int(row.get("size", 0)) if row.get("size") else 0,
                    segment_type=row.get("segment_type", ""),
                    status=row.get("status", "ACTIVE"),
                )
            )
        return segments

    # -- Deal Operations (in-memory, ephemeral) ------------------------------

    async def create_deal(
        self,
        order_id: str,
        name: str,
        *,
        deal_type: str = "private_auction",
        floor_price_micros: int = 0,
        buyer_seat_ids: Optional[list[str]] = None,
    ) -> AdServerDeal:
        """Create a deal (stored in-memory for session duration)."""
        deal_id = f"DEAL-{uuid.uuid4().hex[:8].upper()}"
        deal = AdServerDeal(
            id=deal_id,
            name=name,
            order_id=order_id,
            deal_type=deal_type,
            status=DealStatus.ACTIVE,
            floor_price_micros=floor_price_micros,
            buyer_seat_ids=buyer_seat_ids or [],
        )
        self._deals.append(
            {
                "id": deal_id,
                "name": name,
                "order_id": order_id,
                "deal_type": deal_type,
                "status": "active",
            }
        )
        return deal

    async def update_deal(
        self,
        deal_id: str,
        *,
        status: Optional[str] = None,
        floor_price_micros: Optional[int] = None,
    ) -> AdServerDeal:
        """Update a deal."""
        for d in self._deals:
            if d["id"] == deal_id:
                if status:
                    d["status"] = status
                return AdServerDeal(
                    id=deal_id,
                    name=d["name"],
                    order_id=d["order_id"],
                    deal_type=d["deal_type"],
                    status=DealStatus(status or d["status"]),
                )
        raise ValueError(f"Deal not found: {deal_id}")

    # -- Order Operations (in-memory) ----------------------------------------

    async def create_order(
        self,
        name: str,
        advertiser_id: str,
        *,
        agency_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> AdServerOrder:
        """Create an order (in-memory)."""
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        order = AdServerOrder(
            id=order_id,
            name=name,
            advertiser_id=advertiser_id,
            status=OrderStatus.DRAFT,
        )
        self._orders.append({"id": order_id, "name": name, "status": "draft"})
        return order

    async def get_order(self, order_id: str) -> AdServerOrder:
        """Get an order."""
        for o in self._orders:
            if o["id"] == order_id:
                return AdServerOrder(
                    id=order_id, name=o["name"], advertiser_id="", status=OrderStatus(o["status"])
                )
        raise ValueError(f"Order not found: {order_id}")

    async def approve_order(self, order_id: str) -> AdServerOrder:
        """Approve an order."""
        for o in self._orders:
            if o["id"] == order_id:
                o["status"] = "approved"
                return AdServerOrder(
                    id=order_id, name=o["name"], advertiser_id="", status=OrderStatus.APPROVED
                )
        raise ValueError(f"Order not found: {order_id}")

    # -- Line Item Operations (in-memory) ------------------------------------

    async def create_line_item(
        self,
        order_id: str,
        name: str,
        *,
        inventory_targeting: Optional[list[str]] = None,
        audience_targeting: Optional[list[str]] = None,
        budget_micros: int = 0,
        rate_micros: int = 0,
        impressions: int = 0,
    ) -> AdServerLineItem:
        """Create a line item (in-memory)."""
        li_id = f"LI-{uuid.uuid4().hex[:8].upper()}"
        return AdServerLineItem(
            id=li_id,
            name=name,
            order_id=order_id,
            status=LineItemStatus.DRAFT,
        )

    async def update_line_item(
        self,
        line_item_id: str,
        *,
        status: Optional[str] = None,
        budget_micros: Optional[int] = None,
    ) -> AdServerLineItem:
        """Update a line item."""
        return AdServerLineItem(
            id=line_item_id,
            name="",
            order_id="",
            status=LineItemStatus(status) if status else LineItemStatus.DRAFT,
        )

    # -- Booking (high-level) ------------------------------------------------

    async def book_deal(
        self,
        deal_id: str,
        advertiser_name: str,
        *,
        deal_type: str = "private_auction",
        floor_price_micros: int = 0,
        currency: str = "USD",
    ) -> BookingResult:
        """Book a deal — always succeeds for S3 adapter."""
        return BookingResult(
            success=True,
            deal_id=deal_id,
            message=f"Deal {deal_id} booked successfully",
        )

    # -- Static Helpers (shared with CSV adapter) ----------------------------

    @staticmethod
    def _parse_sizes(sizes_str: str) -> list[tuple[int, int]]:
        """Parse pipe-delimited size string."""
        if not sizes_str or not sizes_str.strip():
            return []
        result = []
        for part in sizes_str.split("|"):
            part = part.strip()
            if "x" in part:
                try:
                    w, h = part.split("x", 1)
                    result.append((int(w), int(h)))
                except (ValueError, TypeError):
                    pass
        return result

    @staticmethod
    def _split_pipe(value: str) -> list[str]:
        """Split pipe-delimited string."""
        if not value or not value.strip():
            return []
        return [v.strip() for v in value.split("|") if v.strip()]
