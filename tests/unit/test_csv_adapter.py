# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Comprehensive unit tests for CSVAdServerClient."""

import csv
import shutil
from pathlib import Path

import pytest

from ad_seller.clients.ad_server_base import (
    AdServerAudienceSegment,
    AdServerInventoryItem,
    AdServerType,
    BookingResult,
    DealStatus,
    LineItemStatus,
    OrderStatus,
    get_ad_server_client,
)
from ad_seller.clients.csv_adapter import CSVAdServerClient

# ---------------------------------------------------------------------------
# Sample data paths
# ---------------------------------------------------------------------------

SAMPLE_DIR = Path(__file__).parents[2] / "data" / "csv" / "samples" / "ctv_streaming"
SAMPLE_INVENTORY = SAMPLE_DIR / "inventory.csv"
SAMPLE_AUDIENCES = SAMPLE_DIR / "audiences.csv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_inventory(data_dir: Path, rows: list[dict] | None = None) -> None:
    """Write a minimal inventory.csv to *data_dir*."""
    default_rows = [
        {
            "id": "inv-001",
            "name": "Test Slot",
            "parent_id": "",
            "status": "ACTIVE",
            "sizes": "728x90|300x250",
            "ad_formats": "banner",
            "device_types": "1",
            "inventory_type": "display",
            "content_categories": "",
            "floor_price_cpm": "10.00",
            "currency": "USD",
            "geo_targets": "US",
            "description": "Test inventory slot",
        }
    ]
    fieldnames = list(default_rows[0].keys())
    target = data_dir / "inventory.csv"
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows if rows is not None else default_rows)


def _make_minimal_audiences(data_dir: Path) -> None:
    """Write a minimal audiences.csv to *data_dir*."""
    rows = [
        {
            "id": "aud-001",
            "name": "Sports Fans",
            "description": "Sports audience",
            "size": "5000000",
            "segment_type": "FIRST_PARTY",
            "status": "ACTIVE",
            "iab_audience_taxonomy_id": "6.1.2",
        }
    ]
    fieldnames = list(rows[0].keys())
    target = data_dir / "audiences.csv"
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Isolated data directory with minimal inventory.csv."""
    _make_minimal_inventory(tmp_path)
    return tmp_path


@pytest.fixture
def data_dir_with_audiences(tmp_path: Path) -> Path:
    """Isolated data directory with inventory.csv and audiences.csv."""
    _make_minimal_inventory(tmp_path)
    _make_minimal_audiences(tmp_path)
    return tmp_path


@pytest.fixture
def sample_data_dir(tmp_path: Path) -> Path:
    """Data directory seeded with the real sample CSVs."""
    shutil.copy(SAMPLE_INVENTORY, tmp_path / "inventory.csv")
    shutil.copy(SAMPLE_AUDIENCES, tmp_path / "audiences.csv")
    return tmp_path


@pytest.fixture
async def client(data_dir: Path) -> CSVAdServerClient:
    """Connected CSVAdServerClient with minimal seed data."""
    c = CSVAdServerClient(data_dir=str(data_dir))
    await c.connect()
    return c


@pytest.fixture
async def sample_client(sample_data_dir: Path) -> CSVAdServerClient:
    """Connected CSVAdServerClient with full sample seed data."""
    c = CSVAdServerClient(data_dir=str(sample_data_dir))
    await c.connect()
    return c


# ===========================================================================
# 1. Factory integration
# ===========================================================================


class TestFactory:
    def test_factory_returns_csv_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_ad_server_client() returns CSVAdServerClient when ad_server_type='csv'."""
        monkeypatch.setenv("AD_SERVER_TYPE", "csv")
        monkeypatch.setenv("CSV_DATA_DIR", str(tmp_path))
        # Clear cached settings so env vars are picked up
        from ad_seller.config import settings as settings_module

        monkeypatch.setattr(settings_module, "_settings", None, raising=False)
        result = get_ad_server_client(ad_server_type="csv")
        assert isinstance(result, CSVAdServerClient)

    def test_factory_csv_client_has_correct_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The returned client reports AdServerType.CSV."""
        monkeypatch.setenv("CSV_DATA_DIR", str(tmp_path))
        result = get_ad_server_client(ad_server_type="csv")
        assert result.ad_server_type == AdServerType.CSV


# ===========================================================================
# 2. Connect / disconnect
# ===========================================================================


class TestLifecycle:
    async def test_connect_succeeds_with_valid_dir(self, data_dir: Path) -> None:
        """connect() succeeds when data_dir has inventory.csv."""
        c = CSVAdServerClient(data_dir=str(data_dir))
        await c.connect()  # should not raise

    async def test_connect_raises_for_missing_inventory(self, tmp_path: Path) -> None:
        """connect() raises FileNotFoundError if inventory.csv is absent."""
        c = CSVAdServerClient(data_dir=str(tmp_path))
        with pytest.raises(FileNotFoundError, match="inventory.csv"):
            await c.connect()

    async def test_connect_raises_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """connect() raises FileNotFoundError if data_dir doesn't exist."""
        bad_dir = tmp_path / "no_such_dir"
        c = CSVAdServerClient(data_dir=str(bad_dir))
        with pytest.raises(FileNotFoundError):
            await c.connect()

    async def test_disconnect_is_noop(self, client: CSVAdServerClient) -> None:
        """disconnect() never raises."""
        await client.disconnect()  # should not raise

    async def test_async_context_manager(self, data_dir: Path) -> None:
        """async with ... connects and disconnects without error."""
        async with CSVAdServerClient(data_dir=str(data_dir)) as c:
            assert c.ad_server_type == AdServerType.CSV


# ===========================================================================
# 3. list_inventory()
# ===========================================================================


class TestListInventory:
    async def test_returns_all_items(self, sample_client: CSVAdServerClient) -> None:
        """Returns all 10 rows from the sample inventory.csv."""
        items = await sample_client.list_inventory()
        assert len(items) == 10

    async def test_items_are_inventory_item_type(self, client: CSVAdServerClient) -> None:
        """Each returned object is an AdServerInventoryItem."""
        items = await client.list_inventory()
        for item in items:
            assert isinstance(item, AdServerInventoryItem)

    async def test_ad_server_type_is_csv(self, client: CSVAdServerClient) -> None:
        """ad_server_type on every item is AdServerType.CSV."""
        items = await client.list_inventory()
        for item in items:
            assert item.ad_server_type == AdServerType.CSV

    async def test_parses_sizes_correctly(self, tmp_path: Path) -> None:
        """'728x90|300x250' parses to [(728, 90), (300, 250)]."""
        _make_minimal_inventory(
            tmp_path,
            rows=[
                {
                    "id": "inv-sz",
                    "name": "Size Test",
                    "parent_id": "",
                    "status": "ACTIVE",
                    "sizes": "728x90|300x250",
                    "ad_formats": "",
                    "device_types": "",
                    "inventory_type": "display",
                    "content_categories": "",
                    "floor_price_cpm": "",
                    "currency": "USD",
                    "geo_targets": "",
                    "description": "",
                }
            ],
        )
        c = CSVAdServerClient(data_dir=str(tmp_path))
        await c.connect()
        items = await c.list_inventory()
        assert len(items) == 1
        assert items[0].sizes == [(728, 90), (300, 250)]

    async def test_applies_limit(self, sample_client: CSVAdServerClient) -> None:
        """limit parameter caps the result count."""
        items = await sample_client.list_inventory(limit=3)
        assert len(items) == 3

    async def test_applies_filter_str(self, sample_client: CSVAdServerClient) -> None:
        """filter_str filters by substring match on name (case-insensitive)."""
        items = await sample_client.list_inventory(filter_str="sports")
        assert len(items) > 0
        for item in items:
            assert "sports" in item.name.lower()

    async def test_filter_str_no_match_returns_empty(
        self, sample_client: CSVAdServerClient
    ) -> None:
        """filter_str that matches nothing returns empty list."""
        items = await sample_client.list_inventory(filter_str="zzznotaname")
        assert items == []

    async def test_empty_inventory_returns_empty_list(self, tmp_path: Path) -> None:
        """Inventory file with only a header row returns empty list."""
        _make_minimal_inventory(tmp_path, rows=[])
        c = CSVAdServerClient(data_dir=str(tmp_path))
        # An empty inventory.csv has no data rows — connect() schema validation
        # only validates when rows exist, so this should be fine.
        await c.connect()
        items = await c.list_inventory()
        assert items == []

    async def test_sizes_empty_string_returns_empty_list(self, tmp_path: Path) -> None:
        """Empty sizes field returns empty sizes list."""
        _make_minimal_inventory(
            tmp_path,
            rows=[
                {
                    "id": "inv-ns",
                    "name": "No Size",
                    "parent_id": "",
                    "status": "ACTIVE",
                    "sizes": "",
                    "ad_formats": "",
                    "device_types": "",
                    "inventory_type": "display",
                    "content_categories": "",
                    "floor_price_cpm": "",
                    "currency": "USD",
                    "geo_targets": "",
                    "description": "",
                }
            ],
        )
        c = CSVAdServerClient(data_dir=str(tmp_path))
        await c.connect()
        items = await c.list_inventory()
        assert items[0].sizes == []


# ===========================================================================
# 4. list_audience_segments()
# ===========================================================================


class TestListAudienceSegments:
    async def test_returns_segments_from_sample(self, sample_client: CSVAdServerClient) -> None:
        """Returns audience segments from sample audiences.csv."""
        segments = await sample_client.list_audience_segments()
        assert len(segments) == 4

    async def test_segments_are_correct_type(self, sample_client: CSVAdServerClient) -> None:
        """Each item is an AdServerAudienceSegment."""
        segments = await sample_client.list_audience_segments()
        for seg in segments:
            assert isinstance(seg, AdServerAudienceSegment)

    async def test_segment_ad_server_type(self, sample_client: CSVAdServerClient) -> None:
        """Each segment reports AdServerType.CSV."""
        segments = await sample_client.list_audience_segments()
        for seg in segments:
            assert seg.ad_server_type == AdServerType.CSV

    async def test_segment_size_parsed(self, sample_client: CSVAdServerClient) -> None:
        """Segment size field is parsed as int."""
        segments = await sample_client.list_audience_segments()
        for seg in segments:
            assert isinstance(seg.size, int)
            assert seg.size > 0

    async def test_no_audiences_file_returns_empty(self, client: CSVAdServerClient) -> None:
        """If audiences.csv is absent, returns empty list without error."""
        segments = await client.list_audience_segments()
        assert segments == []

    async def test_filter_str_on_audiences(self, sample_client: CSVAdServerClient) -> None:
        """filter_str filters audience segments by name."""
        segments = await sample_client.list_audience_segments(filter_str="Sports")
        assert len(segments) >= 1
        for seg in segments:
            assert "sports" in seg.name.lower()


# ===========================================================================
# 5. Order CRUD
# ===========================================================================


class TestOrderCRUD:
    async def test_create_order_returns_order(self, client: CSVAdServerClient) -> None:
        """create_order() returns an AdServerOrder."""
        from ad_seller.clients.ad_server_base import AdServerOrder

        order = await client.create_order("Test Order", "adv-001")
        assert isinstance(order, AdServerOrder)

    async def test_create_order_id_has_ord_prefix(self, client: CSVAdServerClient) -> None:
        """Generated order ID starts with 'ord-'."""
        order = await client.create_order("Test Order", "adv-001")
        assert order.id.startswith("ord-")

    async def test_create_order_writes_to_csv(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """After create_order, orders.csv exists and has one row."""
        await client.create_order("CSV Write Test", "adv-002")
        orders_csv = data_dir / "orders.csv"
        assert orders_csv.exists()
        rows = list(csv.DictReader(open(orders_csv, newline="", encoding="utf-8")))
        assert len(rows) == 1

    async def test_create_order_default_status_is_draft(self, client: CSVAdServerClient) -> None:
        """Newly created orders have DRAFT status."""
        order = await client.create_order("Draft Order", "adv-001")
        assert order.status == OrderStatus.DRAFT

    async def test_get_order_retrieves_by_id(self, client: CSVAdServerClient) -> None:
        """get_order() returns the same order that was created."""
        created = await client.create_order("Get Me", "adv-001")
        fetched = await client.get_order(created.id)
        assert fetched.id == created.id
        assert fetched.name == "Get Me"

    async def test_get_order_raises_for_missing_id(self, client: CSVAdServerClient) -> None:
        """get_order() raises ValueError for unknown ID."""
        with pytest.raises(ValueError, match="Order not found"):
            await client.get_order("ord-does-not-exist")

    async def test_approve_order_changes_status(self, client: CSVAdServerClient) -> None:
        """approve_order() transitions status from DRAFT to APPROVED."""
        order = await client.create_order("Approve Me", "adv-001")
        approved = await client.approve_order(order.id)
        assert approved.status == OrderStatus.APPROVED

    async def test_order_persists_across_reads(self, client: CSVAdServerClient) -> None:
        """Order written then read back has same id and name."""
        original = await client.create_order("Persist Test", "adv-persist")
        read_back = await client.get_order(original.id)
        assert read_back.id == original.id
        assert read_back.name == original.name
        assert read_back.advertiser_id == "adv-persist"

    async def test_create_order_optional_fields(self, client: CSVAdServerClient) -> None:
        """Optional fields (advertiser_name, notes, external_id) round-trip correctly."""
        order = await client.create_order(
            "Full Order",
            "adv-full",
            advertiser_name="ACME Inc.",
            agency_id="ag-001",
            notes="some notes",
            external_id="ext-abc",
        )
        fetched = await client.get_order(order.id)
        assert fetched.advertiser_name == "ACME Inc."
        assert fetched.notes == "some notes"
        assert fetched.external_id == "ext-abc"


# ===========================================================================
# 6. Line item CRUD
# ===========================================================================


class TestLineItemCRUD:
    async def test_create_line_item_id_has_li_prefix(self, client: CSVAdServerClient) -> None:
        """create_line_item() generates ID with 'li-' prefix."""
        order = await client.create_order("Order for LI", "adv-001")
        li = await client.create_line_item(order.id, "Test LI", cost_micros=5_000_000)
        assert li.id.startswith("li-")

    async def test_create_line_item_writes_to_csv(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """line_items.csv is created and contains one row after create."""
        order = await client.create_order("Order", "adv-001")
        await client.create_line_item(order.id, "LI Write", cost_micros=1_000_000)
        li_csv = data_dir / "line_items.csv"
        assert li_csv.exists()
        rows = list(csv.DictReader(open(li_csv, newline="", encoding="utf-8")))
        assert len(rows) == 1

    async def test_create_line_item_default_status(self, client: CSVAdServerClient) -> None:
        """New line items have DRAFT status."""
        order = await client.create_order("Order", "adv-001")
        li = await client.create_line_item(order.id, "LI", cost_micros=0)
        assert li.status == LineItemStatus.DRAFT

    async def test_create_line_item_cost_micros_stored(self, client: CSVAdServerClient) -> None:
        """cost_micros round-trips through CSV correctly."""
        order = await client.create_order("Order", "adv-001")
        li = await client.create_line_item(order.id, "CPM LI", cost_micros=12_500_000)
        assert li.cost_micros == 12_500_000

    async def test_update_line_item_modifies_field(self, client: CSVAdServerClient) -> None:
        """update_line_item() modifies a stored field."""
        order = await client.create_order("Order", "adv-001")
        li = await client.create_line_item(order.id, "Original Name", cost_micros=1_000_000)
        updated = await client.update_line_item(li.id, {"name": "Updated Name"})
        assert updated.name == "Updated Name"

    async def test_update_line_item_raises_for_missing(self, client: CSVAdServerClient) -> None:
        """update_line_item() raises ValueError for unknown ID."""
        with pytest.raises(ValueError, match="Line item not found"):
            await client.update_line_item("li-does-not-exist", {"name": "x"})

    async def test_create_line_item_with_creative_sizes(self, client: CSVAdServerClient) -> None:
        """creative_sizes round-trips through CSV correctly."""
        order = await client.create_order("Order", "adv-001")
        sizes = [(1920, 1080), (1280, 720)]
        await client.create_line_item(order.id, "Video LI", cost_micros=0, creative_sizes=sizes)
        # creative_sizes is stored in CSV but not surfaced on the model directly;
        # verify the CSV row has the correct pipe-delimited string.
        li_csv = client._csv_path("line_items.csv")
        rows = list(csv.DictReader(open(li_csv, newline="", encoding="utf-8")))
        assert rows[0]["creative_sizes"] == "1920x1080|1280x720"


# ===========================================================================
# 7. Deal CRUD
# ===========================================================================


class TestDealCRUD:
    async def test_create_deal_normalizes_deal_type(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """Input 'private_auction' → stored as 'PA' in CSV → returned as 'private_auction'."""
        deal = await client.create_deal("DEAL-001", deal_type="private_auction")
        # Check CSV value
        deals_csv = data_dir / "deals.csv"
        rows = list(csv.DictReader(open(deals_csv, newline="", encoding="utf-8")))
        assert rows[0]["deal_type"] == "PA"
        # Check returned model value
        assert deal.deal_type == "private_auction"

    async def test_create_deal_programmatic_guaranteed_normalizes(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """'programmatic_guaranteed' → 'PG' in CSV."""
        await client.create_deal("DEAL-PG", deal_type="programmatic_guaranteed")
        deals_csv = data_dir / "deals.csv"
        rows = list(csv.DictReader(open(deals_csv, newline="", encoding="utf-8")))
        assert rows[0]["deal_type"] == "PG"

    async def test_create_deal_preferred_deal_normalizes(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """'preferred_deal' → 'PD' in CSV."""
        await client.create_deal("DEAL-PD", deal_type="preferred_deal")
        deals_csv = data_dir / "deals.csv"
        rows = list(csv.DictReader(open(deals_csv, newline="", encoding="utf-8")))
        assert rows[0]["deal_type"] == "PD"

    async def test_create_deal_initial_status_active(self, client: CSVAdServerClient) -> None:
        """Newly created deals have ACTIVE status."""
        deal = await client.create_deal("DEAL-STATUS")
        assert deal.status == DealStatus.ACTIVE

    async def test_update_deal_modifies_status(self, client: CSVAdServerClient) -> None:
        """update_deal() can change deal status."""
        deal = await client.create_deal("DEAL-UPDATE")
        updated = await client.update_deal(deal.deal_id, {"status": "paused"})
        assert updated.status == DealStatus.PAUSED

    async def test_update_deal_raises_for_missing(self, client: CSVAdServerClient) -> None:
        """update_deal() raises ValueError for unknown deal_id."""
        with pytest.raises(ValueError, match="Deal not found"):
            await client.update_deal("DEAL-MISSING", {"status": "paused"})

    async def test_create_deal_with_buyer_seat_ids(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """buyer_seat_ids are stored pipe-delimited and returned as list."""
        seats = ["seat-a", "seat-b"]
        deal = await client.create_deal("DEAL-SEATS", buyer_seat_ids=seats)
        assert deal.buyer_seat_ids == seats
        # Verify CSV storage
        deals_csv = data_dir / "deals.csv"
        rows = list(csv.DictReader(open(deals_csv, newline="", encoding="utf-8")))
        assert rows[0]["buyer_seat_ids"] == "seat-a|seat-b"

    async def test_create_deal_floor_price(self, client: CSVAdServerClient) -> None:
        """floor_price_micros is stored and returned correctly."""
        deal = await client.create_deal("DEAL-FLOOR", floor_price_micros=5_000_000)
        assert deal.floor_price_micros == 5_000_000

    async def test_create_deal_id_has_deal_prefix(self, client: CSVAdServerClient) -> None:
        """Internal record ID starts with 'deal-'."""
        deal = await client.create_deal("DEAL-PREFIX")
        assert deal.id.startswith("deal-")


# ===========================================================================
# 8. book_deal()
# ===========================================================================


class TestBookDeal:
    async def test_book_deal_returns_booking_result(self, client: CSVAdServerClient) -> None:
        """book_deal() returns a BookingResult instance."""
        result = await client.book_deal("BOOK-001", "Test Advertiser")
        assert isinstance(result, BookingResult)

    async def test_book_deal_success_flag(self, client: CSVAdServerClient) -> None:
        """BookingResult.success is True."""
        result = await client.book_deal("BOOK-002", "ACME")
        assert result.success is True

    async def test_book_deal_has_order(self, client: CSVAdServerClient) -> None:
        """BookingResult.order is populated."""
        result = await client.book_deal("BOOK-003", "ACME")
        assert result.order is not None
        assert result.order.id.startswith("ord-")

    async def test_book_deal_has_line_items(self, client: CSVAdServerClient) -> None:
        """BookingResult.line_items is a non-empty list."""
        result = await client.book_deal("BOOK-004", "ACME")
        assert len(result.line_items) == 1
        assert result.line_items[0].id.startswith("li-")

    async def test_book_deal_has_deal(self, client: CSVAdServerClient) -> None:
        """BookingResult.deal is populated."""
        result = await client.book_deal("BOOK-005", "ACME")
        assert result.deal is not None
        assert result.deal.id.startswith("deal-")
        assert result.deal.deal_id == "BOOK-005"

    async def test_book_deal_all_csvs_have_new_rows(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """After book_deal, orders.csv, line_items.csv, and deals.csv each have one row."""
        await client.book_deal("BOOK-ALL", "ACME Corp")
        for filename in ("orders.csv", "line_items.csv", "deals.csv"):
            csv_file = data_dir / filename
            assert csv_file.exists(), f"{filename} not created"
            rows = list(csv.DictReader(open(csv_file, newline="", encoding="utf-8")))
            assert len(rows) == 1, f"{filename} should have 1 row, got {len(rows)}"

    async def test_book_deal_order_linked_to_line_item(self, client: CSVAdServerClient) -> None:
        """Line item's order_id matches the returned order's id."""
        result = await client.book_deal("BOOK-LINK", "Linked Corp")
        assert result.line_items[0].order_id == result.order.id

    async def test_book_deal_deal_type_preserved(self, client: CSVAdServerClient) -> None:
        """deal_type passed to book_deal is reflected in the returned deal."""
        result = await client.book_deal(
            "BOOK-PG", "PG Advertiser", deal_type="programmatic_guaranteed"
        )
        assert result.deal.deal_type == "programmatic_guaranteed"

    async def test_book_deal_floor_price_in_line_item(self, client: CSVAdServerClient) -> None:
        """When only floor_price_micros is set, line item cost_micros equals floor price."""
        result = await client.book_deal("BOOK-FLOOR", "ACME", floor_price_micros=3_000_000)
        assert result.line_items[0].cost_micros == 3_000_000

    async def test_book_deal_fixed_price_overrides_floor(self, client: CSVAdServerClient) -> None:
        """When fixed_price_micros is set, line item cost_micros equals fixed price."""
        result = await client.book_deal(
            "BOOK-FIXED",
            "ACME",
            floor_price_micros=1_000_000,
            fixed_price_micros=7_000_000,
        )
        assert result.line_items[0].cost_micros == 7_000_000


# ===========================================================================
# 9. Atomic writes
# ===========================================================================


class TestAtomicWrites:
    async def test_write_then_read_is_consistent(self, client: CSVAdServerClient) -> None:
        """After a write, the file is immediately readable and correct."""
        order = await client.create_order("Atomic Order", "adv-atomic")
        fetched = await client.get_order(order.id)
        assert fetched.id == order.id
        assert fetched.name == "Atomic Order"

    async def test_no_tmp_file_left_after_write(
        self, client: CSVAdServerClient, data_dir: Path
    ) -> None:
        """No .tmp file is left behind after a successful write."""
        await client.create_order("Tmp Check", "adv-tmp")
        tmp_files = list(data_dir.glob("*.tmp"))
        assert tmp_files == [], f"Unexpected .tmp files: {tmp_files}"

    async def test_multiple_writes_accumulate(self, client: CSVAdServerClient) -> None:
        """Multiple orders accumulate in orders.csv, each retrievable by ID."""
        ids = []
        for i in range(5):
            order = await client.create_order(f"Order {i}", "adv-multi")
            ids.append(order.id)

        for oid in ids:
            fetched = await client.get_order(oid)
            assert fetched.id == oid

    async def test_deal_write_then_update_readable(self, client: CSVAdServerClient) -> None:
        """A deal written then updated is readable with new status."""
        deal = await client.create_deal("ATOMIC-DEAL")
        await client.update_deal(deal.deal_id, {"status": "paused"})
        # Verify by reading deals.csv directly
        deals_csv = client._csv_path("deals.csv")
        rows = list(csv.DictReader(open(deals_csv, newline="", encoding="utf-8")))
        assert len(rows) == 1
        assert rows[0]["status"] == "paused"
