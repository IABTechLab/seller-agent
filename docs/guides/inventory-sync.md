# Inventory Sync

The seller agent needs an inventory catalog to serve buyer queries, generate
pricing, and create deals. Inventory can come from a live ad server or from
built-in mock data for development.

---

## How It Works

The `ProductSetupFlow` runs at startup and performs these steps:

1. Initialize the seller organization (from `SELLER_ORGANIZATION_ID` / `SELLER_ORGANIZATION_NAME`)
2. Check for ad server credentials
3. **If GAM credentials exist** -- sync inventory via the GAM REST API
4. **If no credentials** -- create mock inventory packages for development
5. Create default product definitions with pricing and deal types

---

## Option A: Google Ad Manager (GAM)

### Prerequisites

- A GAM network code (the numeric ID for your GAM network)
- A service account JSON key with GAM API access
- The service account must have the `https://www.googleapis.com/auth/admanager` scope

### Environment Variables

```bash
AD_SERVER_TYPE=google_ad_manager
GAM_ENABLED=true
GAM_NETWORK_CODE=12345678
GAM_JSON_KEY_PATH=/path/to/service-account.json
GAM_APPLICATION_NAME=AdSellerSystem   # Optional, default: AdSellerSystem
GAM_API_VERSION=v202505               # Optional, default: v202505
```

### Sync Process

When GAM credentials are configured, the flow:

1. Creates a `GAMRestClient` and connects using the service account
2. Calls `list_inventory()` to fetch all ad units from GAM
3. Classifies each ad unit by inventory type (see table below)
4. Groups ad units by type and creates **Layer 1 (synced) packages**
5. Assigns estimated base CPMs per inventory type
6. Sets floor prices at 70% of base CPM
7. Stores packages in the storage backend

### Inventory Classification

Ad unit names are matched against keywords to determine inventory type:

| Inventory Type | Keyword Matches | Ad Formats | Device Types | Base CPM |
|---------------|----------------|------------|-------------|----------|
| `display` | *(default -- no other match)* | `banner` | PC, Phone, Tablet | **$12.00** |
| `video` | `video`, `preroll`, `midroll` | `video` | PC, Phone, Tablet | **$25.00** |
| `ctv` | `ctv`, `ott`, `connected` | `video` | CTV, Set Top Box | **$35.00** |
| `native` | `native`, `feed` | `native` | PC, Phone, Tablet | **$10.00** |
| `mobile_app` | `app`, `mobile` | `banner`, `video` | Phone, Tablet | **$18.00** |
| `linear_tv` | `linear`, `broadcast`, `tv `, `cable` | `video` | CTV, Set Top Box | **$40.00** |

Classification is case-insensitive and based on the ad unit's `name` field.

### Example

An ad unit named `"Premium CTV Streaming - Living Room"` would:

- Match keyword `ctv` -> classified as `ctv`
- Get ad format `video`, device types CTV + Set Top Box
- Base CPM of **$35.00**, floor price of **$24.50** (70% of base)

---

## Option B: Mock Inventory (Development)

When no ad server credentials are configured (`GAM_NETWORK_CODE` is unset and
`FREEWHEEL_API_URL` is unset), the flow creates 4 mock packages:

| Package | Type | Base CPM | Floor CPM | Device Types | Featured |
|---------|------|----------|-----------|-------------|----------|
| Display Network Bundle | display | $12.00 | $8.00 | PC, Phone, Tablet | No |
| Video Suite | video | $25.00 | $18.00 | PC, Phone, Tablet | No |
| CTV Premium Bundle | ctv | $35.00 | $28.00 | CTV, Set Top Box | Yes |
| NBCU Linear TV Broadcast Bundle | linear_tv | $40.00 | $28.00 | CTV, Set Top Box | Yes |

Each mock package includes realistic placements, IAB content categories, audience
segments, and geo targets (US).

The flow also creates default product definitions for finer-grained inventory:

| Product | Type | Base CPM | Floor CPM |
|---------|------|----------|-----------|
| Premium Display - Homepage | display | $15.00 | $10.00 |
| Standard Display - ROS | display | $8.00 | $5.00 |
| Pre-Roll Video | video | $25.00 | $18.00 |
| CTV Premium Streaming | ctv | $35.00 | $28.00 |
| Mobile App Rewarded Video | mobile_app | $20.00 | $15.00 |
| Native In-Feed | native | $12.00 | $8.00 |
| NBC Primetime :30 | linear_tv | $55.00 | $40.00 |
| NBCU Cable Network :30 | linear_tv | $22.00 | $15.00 |
| Telemundo Primetime :30 | linear_tv | $18.00 | $12.00 |
| Comcast Local Avails -- Top 10 DMAs | linear_tv | $15.00 | $8.00 |
| Comcast Addressable Linear -- National | linear_tv | $55.00 | $40.00 |
| Programmatic Linear Reach -- A25-54 | linear_tv | $30.00 | $20.00 |

---

## Option C: FreeWheel

Set `AD_SERVER_TYPE=freewheel` to sync inventory from FreeWheel Streaming Hub:

```env
AD_SERVER_TYPE=freewheel
FREEWHEEL_ENABLED=true
FREEWHEEL_SH_MCP_URL=https://shmcp.freewheel.com
FREEWHEEL_NETWORK_ID=your-network-id
FREEWHEEL_INVENTORY_MODE=deals_only  # or "full"
```

**Inventory mode:**

- `deals_only` (default) — only exposes pre-configured deals/packages the publisher set up for agentic selling
- `full` — exposes all available inventory to the agent

---

## Scheduled Periodic Sync

Enable background inventory sync at a configurable interval:

```env
INVENTORY_SYNC_ENABLED=true
INVENTORY_SYNC_INTERVAL_MINUTES=60
INVENTORY_SYNC_INCLUDE_ARCHIVED=false
```

The sync runs automatically when the server starts and repeats at the configured interval.

## Manual Sync Trigger

Trigger a sync at any time via API or MCP:

```bash
# REST API
curl -X POST http://localhost:8000/api/v1/inventory-sync/trigger

# With incremental mode (only changes since last sync)
curl -X POST "http://localhost:8000/api/v1/inventory-sync/trigger?incremental=true"
```

Or via Claude / ChatGPT: *"Sync my inventory"*

## Sync Status & Watermarks

```bash
# Check scheduler status
curl http://localhost:8000/api/v1/inventory-sync/status

# Check last sync watermark (for incremental sync)
curl http://localhost:8000/api/v1/inventory-sync/watermark
```

## Inventory Type Overrides

Publishers can manually override the auto-detected inventory type for any product:

```bash
# Set a product's inventory type
curl -X POST http://localhost:8000/api/v1/products/my-product-id/inventory-type \
  -H "Content-Type: application/json" \
  -d '{"product_id": "my-product-id", "inventory_type": "ctv", "reason": "Misclassified as display"}'

# Check current override
curl http://localhost:8000/api/v1/products/my-product-id/inventory-type

# Remove override (revert to auto-detected)
curl -X DELETE http://localhost:8000/api/v1/products/my-product-id/inventory-type
```

## Rate Card Integration

Set base CPMs by inventory type so the pricing engine starts with accurate floor prices:

```bash
# Get current rate card
curl http://localhost:8000/api/v1/rate-card

# Update rate card
curl -X PUT http://localhost:8000/api/v1/rate-card \
  -H "Content-Type: application/json" \
  -d '[{"inventory_type": "ctv", "base_cpm": 40.0}, {"inventory_type": "display", "base_cpm": 12.0}]'
```
