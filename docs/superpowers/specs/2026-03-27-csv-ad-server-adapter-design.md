# CSV Ad Server Adapter

**Date:** 2026-03-27
**Status:** Approved
**Scope:** New `CSVAdServerClient` + sample data + config

## Problem

The seller agent requires GAM or FreeWheel credentials to function. First-time testers, demo environments, and publishers evaluating the system have no way to run it with real data flows. The existing test fixtures are mocks that bypass the ad server abstraction layer entirely.

## Solution

A third `AdServerClient` implementation backed by CSV files. Set `AD_SERVER_TYPE=csv` and point to a directory of CSV files. All existing flows (inventory sync, deal creation, booking, SSP distribution) work unchanged — the CSV adapter is just another ad server backend.

Ship two sample datasets (CTV/streaming publisher and web/display publisher) so first-time users can test everything immediately. Publishers can also drop their own CSV exports.

## Architecture

```
get_ad_server_client()
  ├─ "google_ad_manager" → GAMAdServerClient
  ├─ "freewheel"         → FreeWheelAdServerClient
  └─ "csv"               → CSVAdServerClient  ← NEW
```

### Files Changed/Created

| File | Change |
|------|--------|
| `src/ad_seller/clients/csv_adapter.py` | **New** — CSVAdServerClient implementation |
| `src/ad_seller/clients/ad_server_base.py` | Add `CSV` to `AdServerType` enum, update factory |
| `src/ad_seller/clients/__init__.py` | Add `CSVAdServerClient` to exports |
| `src/ad_seller/config/settings.py` | Add `csv_data_dir` setting |
| `data/csv/samples/ctv_streaming/inventory.csv` | **New** — CTV sample data |
| `data/csv/samples/ctv_streaming/audiences.csv` | **New** — CTV audience segments |
| `data/csv/samples/web_display/inventory.csv` | **New** — Display sample data |
| `data/csv/samples/web_display/audiences.csv` | **New** — Display audience segments |
| `.env.example` | Add `CSV_DATA_DIR` |
| `tests/unit/test_csv_adapter.py` | **New** — Tests |

## CSVAdServerClient Implementation

### Config

```python
# settings.py
ad_server_type: str = "csv"  # new valid value
csv_data_dir: str = "./data/csv/samples/ctv_streaming"  # path to CSV directory
```

### Lifecycle

```python
class CSVAdServerClient(AdServerClient):
    ad_server_type = AdServerType.CSV

    def __init__(self, data_dir: str):
        self._data_dir = Path(data_dir)

    async def connect(self) -> None:
        """Validate data_dir exists and has inventory.csv."""
        if not (self._data_dir / "inventory.csv").exists():
            raise FileNotFoundError(f"No inventory.csv in {self._data_dir}")

    async def disconnect(self) -> None:
        pass  # No connection to close
```

### Read Methods

`list_inventory()` and `list_audience_segments()` read from CSV using Python's `csv.DictReader`. Apply `filter_str` (substring match on `name` field) and `limit` parameters.

**Parsing rules:**
- `sizes` column `"728x90|300x250"` → `[(728, 90), (300, 250)]` tuples for `AdServerInventoryItem.sizes`
- Pipe-delimited columns (`ad_formats`, `device_types`, `content_categories`, `geo_targets`) split on `|`
- Extra CSV columns not on `AdServerInventoryItem` (e.g., `ad_formats`, `device_types`, `inventory_type`, `content_categories`, `floor_price_cpm`, `geo_targets`, `description`) are preserved in `raw` dict for downstream use by `ProductSetupFlow` and filtering, but not added to the base model

### All Implemented Methods

Every `@abstractmethod` on `AdServerClient` is implemented:

| Method | Behavior |
|--------|----------|
| `connect()` | Validate `data_dir` exists, `inventory.csv` present, schema check |
| `disconnect()` | No-op |
| `list_inventory()` | Read `inventory.csv`, parse, filter, return `AdServerInventoryItem` list |
| `list_audience_segments()` | Read `audiences.csv`, return `AdServerAudienceSegment` list |
| `create_order()` | Append to `orders.csv`, return `AdServerOrder` |
| `get_order()` | Read `orders.csv`, find by ID, return `AdServerOrder` |
| `approve_order()` | Update status to APPROVED in `orders.csv`, return `AdServerOrder` |
| `create_line_item()` | Append to `line_items.csv`, return `AdServerLineItem` |
| `update_line_item()` | Find by ID in `line_items.csv`, apply updates, return `AdServerLineItem` |
| `create_deal()` | Append to `deals.csv`, return `AdServerDeal` |
| `update_deal()` | Find by ID in `deals.csv`, apply updates, return `AdServerDeal` |
| `book_deal()` | Compose order + line item + deal (batched write), return `BookingResult` |

All returned models set `ad_server_type=AdServerType.CSV`.

### Write Methods (Full CRUD)

Read the CSV, append/update rows, write back. Use `threading.Lock` for in-process concurrency (cross-platform, no `fcntl` dependency).

Each write method:
1. Read existing CSV into list of dicts (create file with header row if it doesn't exist)
2. Generate `id` if creating (UUID-based)
3. Append or update the matching row
4. Write back entire CSV atomically (write to `.tmp`, then rename)
5. Return the appropriate `AdServer*` model

**Empty/missing file handling:** If a write-target CSV (orders, line_items, deals) does not exist, the adapter creates it with the appropriate header row on the first write. `inventory.csv` and `audiences.csv` must exist at `connect()` time.

**Malformed data handling:** `connect()` performs a schema validation pass — checks that required columns exist and required fields are non-empty. Raises `ValueError` with a clear message listing the problem rows/columns. Numeric fields that fail parsing are logged as warnings and defaulted to 0.

### Deal Type Normalization

CSV stores abbreviated forms (`PG`, `PD`, `PA`) for human readability. The adapter normalizes to full `AdServerDeal.deal_type` strings:
- `PG` → `"programmatic_guaranteed"`
- `PD` → `"preferred_deal"`
- `PA` → `"private_auction"`

Reverse mapping on write (full string → abbreviation in CSV).

### ID Generation

```python
def _generate_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
# Examples: "ord-a1b2c3d4", "li-e5f6g7h8", "deal-i9j0k1l2"
```

### book_deal() Composition

`book_deal()` composes the three write operations (matching GAM's behavior). All three writes are batched in memory and flushed together to avoid partial state on crash:
1. Build order row, line item row, deal row in memory
2. Write all three CSVs in sequence (orders → line_items → deals)
3. Returns `BookingResult(order=..., line_items=[...], deal=...)`

## CSV Schemas

### `inventory.csv` — Seed Data (read + write)

| Column | Type | Required | IAB Spec | Notes |
|--------|------|----------|----------|-------|
| `id` | str | yes | OpenDirect `inventorysegmentid` | Unique placement ID |
| `name` | str | yes | OpenDirect `name` | Human-readable name |
| `parent_id` | str | no | GAM `parentId` | Parent site/section |
| `status` | str | yes | OpenDirect `status` | ACTIVE, INACTIVE, ARCHIVED |
| `sizes` | str | no | OpenRTB `banner.w`x`banner.h` | Pipe-delimited: `728x90\|300x250` |
| `ad_formats` | str | no | AdCOM `MediaType` | Pipe-delimited: `video\|banner` |
| `device_types` | str | no | AdCOM `DeviceType` | Pipe-delimited ints: `3\|7` |
| `inventory_type` | str | no | custom | `ctv\|display\|video\|mobile_app\|native` |
| `content_categories` | str | no | AdCOM `cattax` IAB CT v2 | Pipe-delimited: `IAB17\|IAB17-12` |
| `floor_price_cpm` | float | no | OpenRTB `bidfloor` | Dollars (not micros) |
| `currency` | str | no | ISO 4217 / OpenRTB `bidfloorcur` | Default: USD |
| `geo_targets` | str | no | ISO 3166-2 | Pipe-delimited: `US\|US-NY` |
| `description` | str | no | OpenDirect | Freeform description |

### `audiences.csv` — Seed Data (read + write)

| Column | Type | Required | IAB Spec | Notes |
|--------|------|----------|----------|-------|
| `id` | str | yes | IAB Audience Taxonomy 1.1 | Segment ID |
| `name` | str | yes | | Human-readable name |
| `description` | str | no | | |
| `size` | int | no | | Estimated reach |
| `segment_type` | str | no | GAM `AudienceSegmentType` | FIRST_PARTY, THIRD_PARTY |
| `status` | str | yes | | ACTIVE, INACTIVE |
| `iab_audience_taxonomy_id` | str | no | IAB Audience Taxonomy | e.g., `6.1.2` |

### `orders.csv` — Starts Empty, CRUD Populates

| Column | Type | Required | IAB Spec | Notes |
|--------|------|----------|----------|-------|
| `id` | str | yes | OpenDirect `orderid` | Auto-generated |
| `name` | str | yes | OpenDirect `name` | |
| `advertiser_id` | str | yes | OpenDirect `buyerorganizationid` | |
| `advertiser_name` | str | no | | |
| `agency_id` | str | no | | |
| `status` | str | yes | OpenDirect `status` | DRAFT → APPROVED → COMPLETED |
| `external_id` | str | no | GAM `externalOrderId` | |
| `notes` | str | no | | |
| `created_at` | str | yes | | ISO 8601 |
| `updated_at` | str | no | | ISO 8601 |

### `line_items.csv` — Starts Empty, CRUD Populates

| Column | Type | Required | IAB Spec | Notes |
|--------|------|----------|----------|-------|
| `id` | str | yes | OpenDirect `lineid` | Auto-generated |
| `order_id` | str | yes | OpenDirect `orderid` | FK to orders.csv |
| `name` | str | yes | | |
| `status` | str | yes | | DRAFT, READY, DELIVERING, ... |
| `cost_type` | str | yes | OpenDirect `ratetype` | CPM, CPC, CPCV, CPD |
| `cost_micros` | int | yes | OpenDirect `rate` (micros) | Price in microcurrency |
| `currency` | str | yes | ISO 4217 | USD |
| `impressions_goal` | int | no | OpenDirect `quantity` | -1 = unlimited |
| `start_time` | str | no | OpenDirect `startdate` | ISO 8601 |
| `end_time` | str | no | OpenDirect `enddate` | ISO 8601 |
| `targeting_json` | str | no | OpenRTB | JSON-serialized targeting |
| `creative_sizes` | str | no | | Pipe-delimited: `728x90\|300x250` |
| `external_id` | str | no | | |
| `created_at` | str | yes | | ISO 8601 |

### `deals.csv` — Starts Empty, CRUD Populates

| Column | Type | Required | IAB Spec | Notes |
|--------|------|----------|----------|-------|
| `id` | str | yes | internal | Auto-generated |
| `deal_id` | str | yes | OpenRTB `dealid` / Deals API | External deal ID |
| `name` | str | no | | |
| `deal_type` | str | yes | OpenDirect `DealType` | PG, PD, PA |
| `floor_price_micros` | int | no | OpenRTB `bidfloor` (micros) | For PA deals |
| `fixed_price_micros` | int | no | Deals API `fixed_price_cpm` (micros) | For PG/PD |
| `currency` | str | yes | ISO 4217 / OpenRTB `bidfloorcur` | USD |
| `buyer_seat_ids` | str | no | OpenRTB `wseat` | Pipe-delimited |
| `status` | str | yes | Deals API `DealSyncStatus` | DRAFT, ACTIVE, PAUSED, ARCHIVED |
| `auction_type` | int | no | OpenRTB `at` | 1=first-price, 3=fixed |
| `start_time` | str | no | | ISO 8601 |
| `end_time` | str | no | | ISO 8601 |
| `external_id` | str | no | | |
| `created_at` | str | yes | | ISO 8601 |
| `updated_at` | str | no | | ISO 8601 |

## Sample Data

### CTV/Streaming Publisher (`data/csv/samples/ctv_streaming/`)

**inventory.csv** — 10 placements:

| id | name | inventory_type | ad_formats | sizes | device_types | floor_price_cpm |
|----|------|---------------|------------|-------|-------------|----------------|
| inv-ctv-sports-preroll | Sports Pre-Roll :15/:30 | ctv | video | 1920x1080 | 3\|7 | 28.00 |
| inv-ctv-sports-midroll | Sports Mid-Roll :15/:30 | ctv | video | 1920x1080 | 3\|7 | 32.00 |
| inv-ctv-news-preroll | News Pre-Roll :15/:30 | ctv | video | 1920x1080 | 3\|7 | 22.00 |
| inv-ctv-entertainment-preroll | Entertainment Pre-Roll :30 | ctv | video | 1920x1080 | 3\|7 | 25.00 |
| inv-ctv-entertainment-midroll | Entertainment Mid-Roll :15 | ctv | video | 1920x1080 | 3\|7 | 30.00 |
| inv-ctv-kids-preroll | Kids & Family Pre-Roll :15 | ctv | video | 1920x1080 | 3\|7 | 18.00 |
| inv-ctv-sports-pause | Sports Pause Ad | display | banner | 1920x1080 | 3\|7 | 35.00 |
| inv-ctv-homepage-hero | Homepage Hero Banner | display | banner | 1920x1080\|1280x720 | 3\|7 | 40.00 |
| inv-video-web-preroll | Web Player Pre-Roll :15/:30 | video | video | 640x360\|1280x720 | 1\|2 | 15.00 |
| inv-mobile-app-interstitial | Mobile App Interstitial | mobile_app | banner\|video | 320x480\|1080x1920 | 1 | 20.00 |

**audiences.csv** — 4 segments:

| id | name | size | segment_type |
|----|------|------|-------------|
| aud-sports-enthusiasts | Sports Enthusiasts | 12500000 | FIRST_PARTY |
| aud-cord-cutters | Cord Cutters 18-34 | 8200000 | FIRST_PARTY |
| aud-news-junkies | News & Current Events | 6100000 | FIRST_PARTY |
| aud-family-viewers | Family & Kids Viewers | 4500000 | FIRST_PARTY |

### Web/Display Publisher (`data/csv/samples/web_display/`)

**inventory.csv** — 10 placements:

| id | name | inventory_type | ad_formats | sizes | device_types | floor_price_cpm |
|----|------|---------------|------------|-------|-------------|----------------|
| inv-web-homepage-lb | Homepage Leaderboard | display | banner | 728x90\|970x250 | 1\|2 | 12.00 |
| inv-web-homepage-mpu | Homepage MPU | display | banner | 300x250\|336x280 | 1\|2 | 10.00 |
| inv-web-article-sidebar | Article Sidebar | display | banner | 300x250\|300x600 | 2 | 8.00 |
| inv-web-article-inline | Article Inline | native | native | 0x0 | 1\|2 | 9.00 |
| inv-web-article-video | Article Video Player | video | video | 640x360 | 1\|2 | 18.00 |
| inv-web-interstitial | Interstitial Overlay | display | banner | 320x480\|1024x768 | 1\|2 | 15.00 |
| inv-web-sticky-footer | Sticky Footer Banner | display | banner | 320x50\|728x90 | 1\|2 | 6.00 |
| inv-web-sponsored-content | Sponsored Content Feed | native | native | 0x0 | 1\|2 | 14.00 |
| inv-web-newsletter | Newsletter Sponsorship | display | banner | 600x200 | 2 | 20.00 |
| inv-mobile-web-banner | Mobile Web Banner | display | banner | 320x50\|320x100 | 1 | 5.00 |

**audiences.csv** — 4 segments:

| id | name | size | segment_type |
|----|------|------|-------------|
| aud-tech-readers | Technology Enthusiasts | 9800000 | FIRST_PARTY |
| aud-auto-intenders | Auto Purchase Intenders | 3200000 | THIRD_PARTY |
| aud-finance-pros | Finance Professionals | 2100000 | FIRST_PARTY |
| aud-travel-planners | Active Travel Planners | 4700000 | THIRD_PARTY |

## Configuration

```env
# .env
AD_SERVER_TYPE=csv
CSV_DATA_DIR=./data/csv/samples/ctv_streaming

# Or for web/display:
# CSV_DATA_DIR=./data/csv/samples/web_display

# Or for publisher's own data:
# CSV_DATA_DIR=./data/csv/my_publisher
```

## Testing

- Unit tests for all CRUD operations on CSVAdServerClient
- Test that `get_ad_server_client()` returns CSVAdServerClient when `ad_server_type=csv`
- Test inventory sync flow with CSV adapter end-to-end
- Test book_deal() composes order + line item + deal correctly
- Test concurrent write safety (file locking)
- Test with both sample datasets
