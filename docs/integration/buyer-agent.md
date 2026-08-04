# Buyer Agent Integration

This page describes how a buyer agent discovers, authenticates with, and transacts through the seller agent. For buyer-side implementation details, see the [Buyer Agent docs](https://iabtechlab.github.io/buyer-agent/integration/seller-agent/).

## Step 1: Discover the Seller

Buyer agents discover the seller by fetching the agent card:

```bash
curl https://seller.example.com/.well-known/agent.json
```

The agent card is A2A-protocol-compliant and includes:

- **name** and **description** of the seller agent
- **capabilities** --- supported protocols (`opendirect21`, `a2a`), streaming support
- **skills** --- discovery, pricing, proposals, negotiation, deals
- **authentication** --- supported schemes (`api_key`, `bearer`)
- **inventory_types** --- what the seller offers (display, video, ctv, native, mobile_app)
- **supported_deal_types** --- `pg`, `pmp`, `preferred_deal`, `private_auction`

The buyer agent uses this to determine if the seller matches its campaign needs.

## Step 2: Authenticate

### Option A: Get an API Key

The seller operator creates a **buyer** API key (requires an operator credential):

```bash
curl -X POST https://seller.example.com/auth/api-keys \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "seat_id": "seat-buyer-001",
    "agency_id": "agency-mega",
    "advertiser_id": "adv-widget-co",
    "label": "Widget Co buyer agent"
  }'
```

The buyer stores the returned key and uses it in all subsequent requests:

```
Authorization: Bearer <api_key>
```

Operators bootstrap their own first credential with `ad-seller create-operator-key` — see [Authentication](../api/authentication.md).

### Option B: Agent URL Discovery

The buyer provides its own agent URL in requests. The seller fetches the buyer's agent card, checks registries (AAMP), and assigns a trust level:

```json
{
  "agent_url": "https://buyer.example.com"
}
```

Both methods can be combined. The effective access tier is the minimum of the API key tier and the agent trust tier.

## Step 3: Browse the Catalog

```bash
# List all products
curl -H "Authorization: Bearer <key>" \
  https://seller.example.com/products

# Get pricing for a specific product
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/pricing \
  -d '{"product_id": "display", "volume": 1000000}'

# Search packages
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/media-kit/search \
  -d '{"query": "sports video pre-roll"}'
```

## Step 4: Transaction Flows

### Quote-Book Flow (Recommended)

The simplest transaction pattern:

```bash
# 1. Request a quote
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/api/v1/quotes \
  -d '{
    "product_id": "display",
    "deal_type": "PG",
    "impressions": 1000000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-04-30"
  }'

# 2. Book the deal (using quote_id from step 1)
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/api/v1/deals \
  -d '{"quote_id": "qt-a1b2c3d4e5f6"}'

# 3. Create an order for tracking
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/api/v1/orders \
  -d '{
    "deal_id": "DEMO-A1B2C3D4E5F6",
    "quote_id": "qt-a1b2c3d4e5f6"
  }'
```

### Proposal Flow

For custom terms or when negotiation is expected:

```bash
# 1. Submit a proposal
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/proposals \
  -d '{
    "product_id": "video",
    "deal_type": "preferred_deal",
    "price": 18.00,
    "impressions": 500000,
    "start_date": "2026-05-01",
    "end_date": "2026-05-31"
  }'

# 2. If counter-offered, negotiate
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/proposals/prop-a1b2c3d4/counter \
  -d '{"buyer_price": 20.00}'

# 3. Generate deal from accepted proposal
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/deals \
  -d '{"proposal_id": "prop-a1b2c3d4"}'
```

### Session-Based Conversational Flow

For interactive, multi-turn conversations:

```bash
# 1. Create a session
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/sessions \
  -d '{"agency_id": "agency-mega"}'

# 2. Send messages
curl -X POST -H "Content-Type: application/json" \
  https://seller.example.com/sessions/{session_id}/messages \
  -d '{"message": "What sports video inventory do you have for Q2?"}'

curl -X POST -H "Content-Type: application/json" \
  https://seller.example.com/sessions/{session_id}/messages \
  -d '{"message": "Can I get pricing for 2M impressions on premium pre-roll?"}'

# 3. Close the session when done
curl -X POST https://seller.example.com/sessions/{session_id}/close
```

## Step 5: Post-Deal Management

After a deal is booked, use order and change request endpoints:

```bash
# Transition order through the lifecycle
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/api/v1/orders/{order_id}/transition \
  -d '{"to_status": "submitted", "actor": "agent:buyer-001"}'

# Request a modification
curl -X POST -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  https://seller.example.com/api/v1/change-requests \
  -d '{
    "order_id": "ORD-A1B2C3D4E5F6",
    "change_type": "flight_dates",
    "diffs": [{"field": "flight_start", "old_value": "2026-04-01", "new_value": "2026-04-05"}],
    "reason": "Campaign delayed"
  }'
```

## Integration Checklist

1. Fetch `/.well-known/agent.json` to discover seller capabilities
2. Obtain an API key or register your agent URL
3. Use `Authorization: Bearer <key>` on all requests
4. Implement error handling for 401 (auth), 403 (blocked), 404 (not found), 409 (conflict), 410 (expired), 422 (validation)
5. Handle quote expiry (24h TTL) by requesting new quotes when needed
6. Track order status via the transition history endpoint
