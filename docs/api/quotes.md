# Quotes

Quotes are non-binding price offers from the seller. They have a 24-hour TTL and can be converted into deals via the [Deal Booking](overview.md#deal-booking) endpoints.

## Create a Quote

**POST** `/api/v1/quotes`

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `idempotency_key` | string | **Yes** | Requester-minted opaque key (UUID recommended). A replay with the same key and body returns the original quote instead of minting a second one; the same key reused with a different body is an `idempotency_conflict` (HTTP 409). |
| `product_id` | string | Yes | Seller-issued product to quote, e.g. `prod-3f2a9c81`. Product IDs come from [`GET /products`](overview.md) — they are not fixed catalog slugs. |
| `deal_type` | string | Yes | `PG` (Programmatic Guaranteed), `PD` (Preferred Deal), or `PA` (Private Auction) |
| `impressions` | integer | No | Required for PG deals |
| `flight_start` | string | No | ISO date, defaults to today |
| `flight_end` | string | No | ISO date, defaults to today + 30 days |
| `target_cpm` | Money object | No | Buyer's desired CPM; accepted if above floor. A `Money` object: `{"amount_micros": <int>, "currency": "USD"}`, where `1,000,000` micros = 1 currency unit. Money is never a bare float on the wire. |
| `buyer_identity` | object | No | `seat_id`, `agency_id`, `advertiser_id`, `dsp_platform` |

### Deal Types

- **PG (Programmatic Guaranteed)** --- Fixed price, guaranteed impressions. Requires `impressions` field. Auction type `at=1` (first price).
- **PD (Preferred Deal)** --- Fixed price, non-guaranteed. Buyer gets first look. Auction type `at=1`.
- **PA (Private Auction)** --- Floor price, competitive. Multiple buyers can bid. Auction type `at=3` (private auction).

### Pricing Calculation

The seller evaluates the quote using the `PricingRulesEngine`:

1. Looks up the product's `base_cpm`
2. Applies tier discount based on buyer identity (public/seat/agency/advertiser)
3. Applies volume discount based on `impressions`
4. If `target_cpm` is provided and is above the product's `floor_cpm`, the target is accepted
5. Returns the final CPM with a rationale string

### Example: Create a PG Quote

```bash
curl -X POST http://localhost:8000/api/v1/quotes \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <api_key>" \
  -d '{
    "idempotency_key": "<idempotency_key>",
    "product_id": "prod-3f2a9c81",
    "deal_type": "PG",
    "impressions": 2000000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-06-30",
    "buyer_identity": {
      "agency_id": "agency-mega",
      "advertiser_id": "adv-widget-co"
    }
  }'
```

The response is a `{"quote": {...}}` envelope wrapping the shared `Quote` primitive. Every price is a `Money` object (integer micros, never a float):

```json
{
  "quote": {
    "quote_id": "qt-a1b2c3d4e5f6",
    "status": "available",
    "deal_type": "PG",
    "product": {
      "product_id": "prod-3f2a9c81",
      "name": "Premium Display",
      "inventory_type": "display"
    },
    "pricing": {
      "pricing_type": "fixed",
      "base_cpm": {"amount_micros": 12000000, "currency": "USD"},
      "tier_discount_pct": 10.0,
      "volume_discount_pct": 5.0,
      "final_cpm": {"amount_micros": 10260000, "currency": "USD"},
      "pricing_model": "cpm",
      "rationale": "Agency tier discount (10%) + volume discount (5%) applied",
      "base_cpp": null,
      "final_cpp": null
    },
    "terms": {
      "impressions": 2000000,
      "flight_start": "2026-04-01",
      "flight_end": "2026-06-30",
      "guaranteed": true,
      "grps": null,
      "guaranteed_grps": null,
      "target_demo": null
    },
    "availability": {
      "inventory_available": true,
      "estimated_fill_rate": null,
      "competing_demand": null
    },
    "buyer_tier": "advertiser",
    "rate_card_id": null,
    "expires_at": "2026-04-02T00:00:00Z",
    "seller_id": null,
    "created_at": "2026-04-01T00:00:00Z",
    "deal_id": null,
    "media_type": "digital",
    "linear_tv": null,
    "consent_context": null
  }
}
```

### Example: Create a PD Quote

```bash
curl -X POST http://localhost:8000/api/v1/quotes \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "<idempotency_key>",
    "product_id": "prod-7c1e4b02",
    "deal_type": "PD",
    "flight_start": "2026-05-01",
    "flight_end": "2026-05-31",
    "target_cpm": {"amount_micros": 18500000, "currency": "USD"}
  }'
```

## Retrieve a Quote

**GET** `/api/v1/quotes/{quote_id}`

Returns the quote if it exists and has not expired.

- **404** --- Quote not found
- **410 Gone** --- Quote has expired. Request a new quote.

```bash
curl http://localhost:8000/api/v1/quotes/qt-a1b2c3d4e5f6
```

## Quote Lifecycle

1. **available** --- Active, can be booked into a deal
2. **booked** --- Converted into a deal via `POST /api/v1/deals`
3. **expired** --- TTL elapsed (24 hours), returns 410 on retrieval
