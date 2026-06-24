# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CSV-backed ad server client for testing and demo environments.

Implements AdServerClient using CSV files as the backing store.
Supports full CRUD with atomic writes and cross-platform file locking.

Usage:
    Set AD_SERVER_TYPE=csv and CSV_DATA_DIR=./data/csv/samples/ctv_streaming
"""

import csv
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

# ============================================================================
# Constants
# ============================================================================

DEAL_TYPE_CSV_TO_MODEL: dict[str, str] = {
    "PG": "programmatic_guaranteed",
    "PD": "preferred_deal",
    "PA": "private_auction",
}

DEAL_TYPE_MODEL_TO_CSV: dict[str, str] = {v: k for k, v in DEAL_TYPE_CSV_TO_MODEL.items()}

INVENTORY_REQUIRED_COLUMNS = {"id", "name", "status"}
AUDIENCES_REQUIRED_COLUMNS = {"id", "name", "status"}

ORDERS_COLUMNS = [
    "id",
    "name",
    "advertiser_id",
    "advertiser_name",
    "agency_id",
    "status",
    "external_id",
    "notes",
    "created_at",
    "updated_at",
]

LINE_ITEMS_COLUMNS = [
    "id",
    "order_id",
    "name",
    "status",
    "cost_type",
    "cost_micros",
    "currency",
    "impressions_goal",
    "start_time",
    "end_time",
    "targeting_json",
    "creative_sizes",
    "external_id",
    "created_at",
]

DEALS_COLUMNS = [
    "id",
    "deal_id",
    "name",
    "deal_type",
    "floor_price_micros",
    "fixed_price_micros",
    "currency",
    "buyer_seat_ids",
    "status",
    "auction_type",
    "start_time",
    "end_time",
    "external_id",
    "created_at",
    "updated_at",
]


# ============================================================================
# CSV Ad Server Client
# ============================================================================


class CSVAdServerClient(AdServerClient):
    """Ad server client backed by CSV files.

    Reads inventory and audiences from seed CSV files. Supports full CRUD
    for orders, line items, and deals with atomic writes and thread-safe
    file locking.
    """

    ad_server_type = AdServerType.CSV

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._lock = threading.Lock()

    # -- Helpers ---------------------------------------------------------------

    @staticmethod
    def _generate_id(prefix: str) -> str:
        """Generate a unique ID with the given prefix."""
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _now_iso() -> str:
        """Return current UTC time as ISO 8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_sizes(sizes_str: str) -> list[tuple[int, int]]:
        """Parse pipe-delimited size string like '728x90|300x250' into tuples."""
        if not sizes_str or not sizes_str.strip():
            return []
        result: list[tuple[int, int]] = []
        for part in sizes_str.split("|"):
            part = part.strip()
            if "x" in part:
                try:
                    w, h = part.split("x", 1)
                    result.append((int(w), int(h)))
                except (ValueError, TypeError):
                    logger.warning("Could not parse size: %s", part)
        return result

    @staticmethod
    def _sizes_to_str(sizes: list[tuple[int, int]]) -> str:
        """Convert list of size tuples to pipe-delimited string."""
        return "|".join(f"{w}x{h}" for w, h in sizes)

    @staticmethod
    def _split_pipe(value: str) -> list[str]:
        """Split a pipe-delimited string into a list, filtering empty strings."""
        if not value or not value.strip():
            return []
        return [v.strip() for v in value.split("|") if v.strip()]

    def _csv_path(self, filename: str) -> Path:
        """Return full path to a CSV file in the data directory."""
        return self._data_dir / filename

    def _read_csv(self, filename: str) -> list[dict[str, str]]:
        """Read a CSV file and any overlay files matching the pattern.

        Convention: _read_csv("inventory.csv") reads inventory.csv plus
        any inventory_*.csv files in the same directory (additive merge).
        """
        import glob as glob_module

        path = self._csv_path(filename)
        stem = path.stem  # e.g. "inventory"

        # Find base file + overlays
        matched_files = []
        if path.exists():
            matched_files.append(path)

        # Glob for overlay files: inventory_*.csv
        overlay_pattern = str(self._data_dir / f"{stem}_*.csv")
        matched_files.extend(sorted(Path(p) for p in glob_module.glob(overlay_pattern)))

        if not matched_files:
            return []

        # Merge all matched files
        all_rows = []
        for csv_path in matched_files:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                all_rows.extend(list(reader))

        return all_rows

    def _write_csv(
        self,
        filename: str,
        rows: list[dict[str, str]],
        fieldnames: list[str],
    ) -> None:
        """Write rows to CSV atomically (write to .tmp then rename)."""
        path = self._csv_path(filename)
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, path)

    def _ensure_csv(self, filename: str, fieldnames: list[str]) -> None:
        """Create a CSV file with header row if it doesn't exist."""
        path = self._csv_path(filename)
        if not path.exists():
            self._write_csv(filename, [], fieldnames)

    # -- Lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        """Validate data_dir exists and has required seed files with correct schema."""
        if not self._data_dir.exists():
            raise FileNotFoundError(f"Data directory does not exist: {self._data_dir}")

        inv_path = self._csv_path("inventory.csv")
        if not inv_path.exists():
            raise FileNotFoundError(f"No inventory.csv in {self._data_dir}")

        # Schema validation for inventory.csv
        rows = self._read_csv("inventory.csv")
        if rows:
            actual_columns = set(rows[0].keys())
            missing = INVENTORY_REQUIRED_COLUMNS - actual_columns
            if missing:
                raise ValueError(f"inventory.csv missing required columns: {sorted(missing)}")
            # Validate required fields are non-empty
            for i, row in enumerate(rows):
                for col in INVENTORY_REQUIRED_COLUMNS:
                    if not row.get(col, "").strip():
                        raise ValueError(
                            f"inventory.csv row {i + 1}: required field '{col}' is empty"
                        )

        # Schema validation for audiences.csv if present
        aud_path = self._csv_path("audiences.csv")
        if aud_path.exists():
            aud_rows = self._read_csv("audiences.csv")
            if aud_rows:
                actual_columns = set(aud_rows[0].keys())
                missing = AUDIENCES_REQUIRED_COLUMNS - actual_columns
                if missing:
                    raise ValueError(f"audiences.csv missing required columns: {sorted(missing)}")

        logger.info("CSV ad server connected: %s", self._data_dir)

    async def disconnect(self) -> None:
        """No-op — no connection to close."""

    # -- Inventory Operations --------------------------------------------------

    async def list_inventory(
        self,
        *,
        limit: int = 100,
        filter_str: Optional[str] = None,
    ) -> list[AdServerInventoryItem]:
        """Read inventory.csv and return AdServerInventoryItem list."""
        rows = self._read_csv("inventory.csv")
        results: list[AdServerInventoryItem] = []

        for row in rows:
            name = row.get("name", "")
            if filter_str and filter_str.lower() not in name.lower():
                continue

            # Base model fields
            sizes = self._parse_sizes(row.get("sizes", ""))

            # Extra columns go into raw dict
            raw: dict[str, Any] = {}
            base_fields = {"id", "name", "parent_id", "status", "sizes"}
            for key, value in row.items():
                if key not in base_fields and value:
                    if key in ("ad_formats", "device_types", "content_categories", "geo_targets"):
                        raw[key] = self._split_pipe(value)
                    elif key == "floor_price_cpm":
                        try:
                            raw[key] = float(value)
                        except (ValueError, TypeError):
                            logger.warning("Could not parse floor_price_cpm: %s", value)
                            raw[key] = 0.0
                    else:
                        raw[key] = value

            item = AdServerInventoryItem(
                id=row.get("id", ""),
                name=name,
                parent_id=row.get("parent_id") or None,
                status=row.get("status", "ACTIVE"),
                sizes=sizes,
                ad_server_type=AdServerType.CSV,
            )
            # Store raw data for downstream use — attach after construction
            # since raw is not on AdServerInventoryItem
            item.__dict__["raw"] = raw

            results.append(item)
            if len(results) >= limit:
                break

        return results

    # -- Audience Operations ---------------------------------------------------

    async def list_audience_segments(
        self,
        *,
        limit: int = 500,
        filter_str: Optional[str] = None,
    ) -> list[AdServerAudienceSegment]:
        """Read audiences.csv and return AdServerAudienceSegment list."""
        rows = self._read_csv("audiences.csv")
        results: list[AdServerAudienceSegment] = []

        for row in rows:
            name = row.get("name", "")
            if filter_str and filter_str.lower() not in name.lower():
                continue

            size: Optional[int] = None
            if row.get("size"):
                try:
                    size = int(row["size"])
                except (ValueError, TypeError):
                    logger.warning("Could not parse audience size: %s", row.get("size"))
                    size = 0

            segment = AdServerAudienceSegment(
                id=row.get("id", ""),
                name=name,
                description=row.get("description") or None,
                size=size,
                status=row.get("status", "ACTIVE"),
                ad_server_type=AdServerType.CSV,
            )
            results.append(segment)
            if len(results) >= limit:
                break

        return results

    # -- Order Operations ------------------------------------------------------

    async def create_order(
        self,
        name: str,
        advertiser_id: str,
        *,
        advertiser_name: Optional[str] = None,
        agency_id: Optional[str] = None,
        notes: Optional[str] = None,
        external_id: Optional[str] = None,
    ) -> AdServerOrder:
        """Create an order by appending to orders.csv."""
        order_id = self._generate_id("ord")
        now = self._now_iso()

        row = {
            "id": order_id,
            "name": name,
            "advertiser_id": advertiser_id,
            "advertiser_name": advertiser_name or "",
            "agency_id": agency_id or "",
            "status": "DRAFT",
            "external_id": external_id or "",
            "notes": notes or "",
            "created_at": now,
            "updated_at": now,
        }

        with self._lock:
            self._ensure_csv("orders.csv", ORDERS_COLUMNS)
            rows = self._read_csv("orders.csv")
            rows.append(row)
            self._write_csv("orders.csv", rows, ORDERS_COLUMNS)

        return AdServerOrder(
            id=order_id,
            name=name,
            advertiser_id=advertiser_id,
            advertiser_name=advertiser_name,
            status=OrderStatus.DRAFT,
            external_id=external_id,
            notes=notes,
            ad_server_type=AdServerType.CSV,
        )

    async def get_order(self, order_id: str) -> AdServerOrder:
        """Find an order by ID in orders.csv."""
        rows = self._read_csv("orders.csv")
        for row in rows:
            if row.get("id") == order_id:
                return self._row_to_order(row)
        raise ValueError(f"Order not found: {order_id}")

    async def approve_order(self, order_id: str) -> AdServerOrder:
        """Update order status to APPROVED in orders.csv."""
        with self._lock:
            rows = self._read_csv("orders.csv")
            for row in rows:
                if row.get("id") == order_id:
                    row["status"] = "APPROVED"
                    row["updated_at"] = self._now_iso()
                    self._write_csv("orders.csv", rows, ORDERS_COLUMNS)
                    return self._row_to_order(row)
        raise ValueError(f"Order not found: {order_id}")

    @staticmethod
    def _row_to_order(row: dict[str, str]) -> AdServerOrder:
        """Convert a CSV row dict to an AdServerOrder."""
        status_str = row.get("status", "DRAFT").lower()
        try:
            status = OrderStatus(status_str)
        except ValueError:
            status = OrderStatus.DRAFT

        return AdServerOrder(
            id=row.get("id", ""),
            name=row.get("name", ""),
            advertiser_id=row.get("advertiser_id", ""),
            advertiser_name=row.get("advertiser_name") or None,
            status=status,
            external_id=row.get("external_id") or None,
            notes=row.get("notes") or None,
            ad_server_type=AdServerType.CSV,
        )

    # -- Line Item Operations --------------------------------------------------

    async def create_line_item(
        self,
        order_id: str,
        name: str,
        *,
        cost_micros: int,
        currency: str = "USD",
        cost_type: str = "CPM",
        impressions_goal: int = -1,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        targeting: Optional[dict[str, Any]] = None,
        creative_sizes: Optional[list[tuple[int, int]]] = None,
        external_id: Optional[str] = None,
    ) -> AdServerLineItem:
        """Create a line item by appending to line_items.csv."""
        li_id = self._generate_id("li")
        now = self._now_iso()

        row = {
            "id": li_id,
            "order_id": order_id,
            "name": name,
            "status": "DRAFT",
            "cost_type": cost_type,
            "cost_micros": str(cost_micros),
            "currency": currency,
            "impressions_goal": str(impressions_goal),
            "start_time": start_time.isoformat() if start_time else "",
            "end_time": end_time.isoformat() if end_time else "",
            "targeting_json": json.dumps(targeting) if targeting else "",
            "creative_sizes": self._sizes_to_str(creative_sizes) if creative_sizes else "",
            "external_id": external_id or "",
            "created_at": now,
        }

        with self._lock:
            self._ensure_csv("line_items.csv", LINE_ITEMS_COLUMNS)
            rows = self._read_csv("line_items.csv")
            rows.append(row)
            self._write_csv("line_items.csv", rows, LINE_ITEMS_COLUMNS)

        return AdServerLineItem(
            id=li_id,
            order_id=order_id,
            name=name,
            status=LineItemStatus.DRAFT,
            cost_type=cost_type,
            cost_micros=cost_micros,
            currency=currency,
            impressions_goal=impressions_goal,
            start_time=start_time,
            end_time=end_time,
            external_id=external_id,
            ad_server_type=AdServerType.CSV,
        )

    async def update_line_item(
        self,
        line_item_id: str,
        updates: dict[str, Any],
    ) -> AdServerLineItem:
        """Find and update a line item in line_items.csv."""
        with self._lock:
            rows = self._read_csv("line_items.csv")
            for row in rows:
                if row.get("id") == line_item_id:
                    for key, value in updates.items():
                        if key in row:
                            row[key] = str(value) if value is not None else ""
                    self._write_csv("line_items.csv", rows, LINE_ITEMS_COLUMNS)
                    return self._row_to_line_item(row)
        raise ValueError(f"Line item not found: {line_item_id}")

    @staticmethod
    def _row_to_line_item(row: dict[str, str]) -> AdServerLineItem:
        """Convert a CSV row dict to an AdServerLineItem."""
        status_str = row.get("status", "DRAFT").lower()
        try:
            status = LineItemStatus(status_str)
        except ValueError:
            status = LineItemStatus.DRAFT

        cost_micros = 0
        if row.get("cost_micros"):
            try:
                cost_micros = int(row["cost_micros"])
            except (ValueError, TypeError):
                pass

        impressions_goal = -1
        if row.get("impressions_goal"):
            try:
                impressions_goal = int(row["impressions_goal"])
            except (ValueError, TypeError):
                pass

        start_time = None
        if row.get("start_time"):
            try:
                start_time = datetime.fromisoformat(row["start_time"])
            except (ValueError, TypeError):
                pass

        end_time = None
        if row.get("end_time"):
            try:
                end_time = datetime.fromisoformat(row["end_time"])
            except (ValueError, TypeError):
                pass

        return AdServerLineItem(
            id=row.get("id", ""),
            order_id=row.get("order_id", ""),
            name=row.get("name", ""),
            status=status,
            cost_type=row.get("cost_type", "CPM"),
            cost_micros=cost_micros,
            currency=row.get("currency", "USD"),
            impressions_goal=impressions_goal,
            start_time=start_time,
            end_time=end_time,
            external_id=row.get("external_id") or None,
            ad_server_type=AdServerType.CSV,
        )

    # -- Deal Operations -------------------------------------------------------

    async def create_deal(
        self,
        deal_id: str,
        *,
        name: Optional[str] = None,
        deal_type: str = "private_auction",
        floor_price_micros: int = 0,
        fixed_price_micros: int = 0,
        currency: str = "USD",
        buyer_seat_ids: Optional[list[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        targeting: Optional[dict[str, Any]] = None,
    ) -> AdServerDeal:
        """Create a deal by appending to deals.csv."""
        internal_id = self._generate_id("deal")
        now = self._now_iso()

        # Normalize deal_type to CSV abbreviation
        csv_deal_type = DEAL_TYPE_MODEL_TO_CSV.get(deal_type, deal_type.upper())

        # Determine auction type based on deal type
        auction_type = "1"  # first-price default
        if deal_type == "programmatic_guaranteed":
            auction_type = "3"  # fixed

        row = {
            "id": internal_id,
            "deal_id": deal_id,
            "name": name or "",
            "deal_type": csv_deal_type,
            "floor_price_micros": str(floor_price_micros),
            "fixed_price_micros": str(fixed_price_micros),
            "currency": currency,
            "buyer_seat_ids": "|".join(buyer_seat_ids) if buyer_seat_ids else "",
            "status": "ACTIVE",
            "auction_type": auction_type,
            "start_time": start_time.isoformat() if start_time else "",
            "end_time": end_time.isoformat() if end_time else "",
            "external_id": "",
            "created_at": now,
            "updated_at": now,
        }

        with self._lock:
            self._ensure_csv("deals.csv", DEALS_COLUMNS)
            rows = self._read_csv("deals.csv")
            rows.append(row)
            self._write_csv("deals.csv", rows, DEALS_COLUMNS)

        return AdServerDeal(
            id=internal_id,
            deal_id=deal_id,
            name=name,
            deal_type=deal_type,
            floor_price_micros=floor_price_micros,
            fixed_price_micros=fixed_price_micros,
            currency=currency,
            buyer_seat_ids=buyer_seat_ids or [],
            status=DealStatus.ACTIVE,
            ad_server_type=AdServerType.CSV,
        )

    async def update_deal(
        self,
        deal_id: str,
        updates: dict[str, Any],
    ) -> AdServerDeal:
        """Find and update a deal in deals.csv by deal_id."""
        with self._lock:
            rows = self._read_csv("deals.csv")
            for row in rows:
                if row.get("deal_id") == deal_id:
                    for key, value in updates.items():
                        if key == "deal_type" and value in DEAL_TYPE_MODEL_TO_CSV:
                            row["deal_type"] = DEAL_TYPE_MODEL_TO_CSV[value]
                        elif key == "buyer_seat_ids" and isinstance(value, list):
                            row["buyer_seat_ids"] = "|".join(value)
                        elif key in row:
                            row[key] = str(value) if value is not None else ""
                    row["updated_at"] = self._now_iso()
                    self._write_csv("deals.csv", rows, DEALS_COLUMNS)
                    return self._row_to_deal(row)
        raise ValueError(f"Deal not found: {deal_id}")

    @staticmethod
    def _row_to_deal(row: dict[str, str]) -> AdServerDeal:
        """Convert a CSV row dict to an AdServerDeal."""
        # Normalize deal type from CSV abbreviation to model string
        csv_deal_type = row.get("deal_type", "PA")
        deal_type = DEAL_TYPE_CSV_TO_MODEL.get(csv_deal_type, csv_deal_type.lower())

        status_str = row.get("status", "ACTIVE").lower()
        try:
            status = DealStatus(status_str)
        except ValueError:
            status = DealStatus.ACTIVE

        floor_price_micros = 0
        if row.get("floor_price_micros"):
            try:
                floor_price_micros = int(row["floor_price_micros"])
            except (ValueError, TypeError):
                pass

        fixed_price_micros = 0
        if row.get("fixed_price_micros"):
            try:
                fixed_price_micros = int(row["fixed_price_micros"])
            except (ValueError, TypeError):
                pass

        buyer_seat_ids: list[str] = []
        if row.get("buyer_seat_ids"):
            buyer_seat_ids = [s.strip() for s in row["buyer_seat_ids"].split("|") if s.strip()]

        return AdServerDeal(
            id=row.get("id", ""),
            deal_id=row.get("deal_id", ""),
            name=row.get("name") or None,
            deal_type=deal_type,
            floor_price_micros=floor_price_micros,
            fixed_price_micros=fixed_price_micros,
            currency=row.get("currency", "USD"),
            buyer_seat_ids=buyer_seat_ids,
            status=status,
            external_id=row.get("external_id") or None,
            ad_server_type=AdServerType.CSV,
        )

    # -- High-Level Booking ----------------------------------------------------

    async def book_deal(
        self,
        deal_id: str,
        advertiser_name: str,
        *,
        deal_type: str = "private_auction",
        floor_price_micros: int = 0,
        fixed_price_micros: int = 0,
        currency: str = "USD",
        impressions_goal: int = -1,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        targeting: Optional[dict[str, Any]] = None,
        creative_sizes: Optional[list[tuple[int, int]]] = None,
    ) -> BookingResult:
        """Full deal booking: create order + line item + deal in one batch.

        All three CSV writes happen under a single lock to avoid partial state.
        """
        order_id = self._generate_id("ord")
        li_id = self._generate_id("li")
        internal_deal_id = self._generate_id("deal")
        now = self._now_iso()

        # Determine cost based on deal type
        cost_micros = fixed_price_micros if fixed_price_micros else floor_price_micros

        # Build rows in memory
        order_row = {
            "id": order_id,
            "name": f"Order for {advertiser_name} - {deal_id}",
            "advertiser_id": advertiser_name.lower().replace(" ", "-"),
            "advertiser_name": advertiser_name,
            "agency_id": "",
            "status": "DRAFT",
            "external_id": "",
            "notes": f"Auto-created by book_deal for deal {deal_id}",
            "created_at": now,
            "updated_at": now,
        }

        csv_deal_type = DEAL_TYPE_MODEL_TO_CSV.get(deal_type, deal_type.upper())
        auction_type = "3" if deal_type == "programmatic_guaranteed" else "1"

        li_row = {
            "id": li_id,
            "order_id": order_id,
            "name": f"Line Item for {deal_id}",
            "status": "DRAFT",
            "cost_type": "CPM",
            "cost_micros": str(cost_micros),
            "currency": currency,
            "impressions_goal": str(impressions_goal),
            "start_time": start_time.isoformat() if start_time else "",
            "end_time": end_time.isoformat() if end_time else "",
            "targeting_json": json.dumps(targeting) if targeting else "",
            "creative_sizes": self._sizes_to_str(creative_sizes) if creative_sizes else "",
            "external_id": "",
            "created_at": now,
        }

        deal_row = {
            "id": internal_deal_id,
            "deal_id": deal_id,
            "name": f"Deal for {advertiser_name}",
            "deal_type": csv_deal_type,
            "floor_price_micros": str(floor_price_micros),
            "fixed_price_micros": str(fixed_price_micros),
            "currency": currency,
            "buyer_seat_ids": "",
            "status": "ACTIVE",
            "auction_type": auction_type,
            "start_time": start_time.isoformat() if start_time else "",
            "end_time": end_time.isoformat() if end_time else "",
            "external_id": "",
            "created_at": now,
            "updated_at": now,
        }

        # Batch all writes under a single lock
        with self._lock:
            # Orders
            self._ensure_csv("orders.csv", ORDERS_COLUMNS)
            order_rows = self._read_csv("orders.csv")
            order_rows.append(order_row)
            self._write_csv("orders.csv", order_rows, ORDERS_COLUMNS)

            # Line Items
            self._ensure_csv("line_items.csv", LINE_ITEMS_COLUMNS)
            li_rows = self._read_csv("line_items.csv")
            li_rows.append(li_row)
            self._write_csv("line_items.csv", li_rows, LINE_ITEMS_COLUMNS)

            # Deals
            self._ensure_csv("deals.csv", DEALS_COLUMNS)
            deal_rows = self._read_csv("deals.csv")
            deal_rows.append(deal_row)
            self._write_csv("deals.csv", deal_rows, DEALS_COLUMNS)

        order = AdServerOrder(
            id=order_id,
            name=order_row["name"],
            advertiser_id=order_row["advertiser_id"],
            advertiser_name=advertiser_name,
            status=OrderStatus.DRAFT,
            notes=order_row["notes"],
            ad_server_type=AdServerType.CSV,
        )

        line_item = AdServerLineItem(
            id=li_id,
            order_id=order_id,
            name=li_row["name"],
            status=LineItemStatus.DRAFT,
            cost_type="CPM",
            cost_micros=cost_micros,
            currency=currency,
            impressions_goal=impressions_goal,
            start_time=start_time,
            end_time=end_time,
            ad_server_type=AdServerType.CSV,
        )

        deal = AdServerDeal(
            id=internal_deal_id,
            deal_id=deal_id,
            name=deal_row["name"],
            deal_type=deal_type,
            floor_price_micros=floor_price_micros,
            fixed_price_micros=fixed_price_micros,
            currency=currency,
            buyer_seat_ids=[],
            status=DealStatus.ACTIVE,
            ad_server_type=AdServerType.CSV,
        )

        return BookingResult(
            order=order,
            line_items=[line_item],
            deal=deal,
            ad_server_type=AdServerType.CSV,
            success=True,
        )
