# Buyer Agent Compatibility Report

**Date:** 2026-03-26
**Seller:** `ad_seller_system`
**Buyer:** `ad_buyer_system`
**Task:** 0C — Cross-system compatibility audit

---

## Aligned

### 1. Quote-then-book API contract
Both systems agree on the two-phase flow and endpoint paths:
- `POST /api/v1/quotes` — request a quote
- `GET  /api/v1/quotes/{id}` — retrieve a quote
- `POST /api/v1/deals` — book a deal from a quote
- `GET  /api/v1/deals/{id}` — retrieve a deal

The buyer's `DealsClient` (`ad_buyer_system/src/ad_buyer/clients/deals_client.py`) calls exactly these paths against the seller.

### 2. QuoteRequest field names
Both sides define identical field names for the quote request body: `product_id`, `deal_type`, `impressions`, `flight_start`, `flight_end`, `target_cpm`, `buyer_identity`.

### 3. Deal type short codes
Both systems use the `"PG"`, `"PD"`, `"PA"` shorthand in the Deals API layer:
- Seller: `QuoteRequest.deal_type` accepts `"PG"`, `"PD"`, `"PA"` (quotes.py:55)
- Buyer: `QuoteRequest.deal_type` defaults to `"PD"` (deals.py:119), `DealType` enum uses `"PG"`, `"PD"`, `"PA"` (buyer_identity.py:22-26)

### 4. BuyerIdentity / QuoteBuyerIdentity field alignment
Both sides share `seat_id`, `agency_id`, `advertiser_id`, `dsp_platform` in the quote request's `buyer_identity` nested object.

### 5. Pricing sub-model fields
`PricingInfo` (buyer) and `QuotePricing` (seller) share: `base_cpm`, `tier_discount_pct`, `volume_discount_pct`, `final_cpm`, `currency`, `pricing_model`, `rationale`.

### 6. Terms sub-model fields
`TermsInfo` (buyer) and `QuoteTerms` (seller) share: `impressions`, `flight_start`, `flight_end`, `guaranteed`.

### 7. DealBookingRequest
Both sides: `quote_id`, `buyer_identity` (optional), `notes` (optional).

### 8. Authentication mechanism
Both use `X-Api-Key` header and `Authorization: Bearer <token>` as alternatives. The seller's `dependencies.py` checks `X-Api-Key` first, then `Authorization: Bearer`, matching the buyer's `DealsClient` which sends one or the other.

### 9. API key prefix format
The buyer's auth test files (`tests/unit/test_auth.py`) use `ask_live_` prefixed keys, matching the seller's `API_KEY_PREFIX = "ask_live_"` in `models/api_key.py:24`.

### 10. AccessTier enum values
Both systems define identical `AccessTier` values: `"public"`, `"seat"`, `"agency"`, `"advertiser"`.

### 11. Negotiation event types
Both define `NEGOTIATION_STARTED`, `NEGOTIATION_ROUND`, `NEGOTIATION_CONCLUDED` event types with the same string values (`negotiation.started`, `negotiation.round`, `negotiation.concluded`).

### 12. Linear TV extensions
Buyer's `QuoteRequest` includes `media_type` and `linear_tv` fields; buyer's `DealsClient` has `request_makegood()` and `request_cancellation()` methods for `POST /api/v1/deals/{id}/makegoods` and `POST /api/v1/deals/{id}/cancel`.

---

## Mismatches

### M1. X-Api-Key header casing inconsistency (LOW risk, works in practice)
The buyer uses two different casings across clients:
- `DealsClient` (deals_client.py:103): `"X-Api-Key"` (lowercase k)
- `OpenDirectClient` (opendirect_client.py:55): `"X-API-Key"` (uppercase K)
- `NegotiationClient` (negotiation/client.py:63): `"X-API-Key"`
- `MediaKitClient` (media_kit/client.py:59): `"X-API-Key"`

The seller's `dependencies.py:36` declares `Header(None, alias="X-Api-Key")` with lowercase k.

**Impact:** HTTP headers are case-insensitive per RFC 7230, so this works at the transport layer. However, FastAPI's `Header()` with an explicit alias may behave inconsistently depending on the ASGI server. In practice, uvicorn normalizes headers to lowercase, so this is low risk but messy.

**Files:**
- `ad_buyer_system/src/ad_buyer/clients/deals_client.py:103`
- `ad_buyer_system/src/ad_buyer/clients/opendirect_client.py:55`
- `ad_seller_system/src/ad_seller/auth/dependencies.py:36`

### M2. DealType enum value mismatch between OpenDirect models and Deals API
The seller's `core.py` `DealType` enum uses long-form values (`"programmaticguaranteed"`, `"preferreddeal"`, `"privateauction"`) while the Deals API layer uses short codes (`"PG"`, `"PD"`, `"PA"`). These are in separate API surfaces (OpenDirect vs. Deals API) so they do not conflict today, but any future unification would hit this.

**Files:**
- `ad_seller_system/src/ad_seller/models/core.py:43-48` (long form)
- `ad_seller_system/src/ad_seller/models/quotes.py:55` (short codes)
- `ad_buyer_system/src/ad_buyer/models/buyer_identity.py:22-26` (short codes)

### M3. DealBookingResponse vs. DealResponse status values
The seller's `DealBookingStatus` (quotes.py:29-34) has: `proposed`, `active`, `expired`, `cancelled`.
The buyer's `DealResponse.status` comment (deals.py:183) lists: `proposed`, `active`, `rejected`, `expired`, `completed`.

The buyer expects `rejected` and `completed` which the seller does not define. The seller has `cancelled` which the buyer omits from the comment (though the buyer does define `DEAL_CANCELLED` as an event type).

**Files:**
- `ad_seller_system/src/ad_seller/models/quotes.py:29-34`
- `ad_buyer_system/src/ad_buyer/models/deals.py:183`

### M4. Deal ID format mismatch
The seller generates deal IDs with format `DEMO-{uuid_hex[:12].upper()}` (main.py:2035, 2895) and `CUR-{uuid_hex[:12].upper()}` (main.py:4192) for curator deals.
The buyer's `UnifiedClient.request_deal()` (unified_client.py:619) generates `DEAL-{md5_hex[:8].upper()}` locally.

These are independent ID spaces. The buyer's locally-generated deal IDs would never match seller-issued ones. The `DealsClient` (the proper integration path) correctly consumes seller-issued IDs from response payloads without generating its own.

**Impact:** The `UnifiedClient.request_deal()` path bypasses the seller entirely and fabricates deal IDs client-side. This is likely demo/fallback code, but if used in production it would create phantom deals.

**Files:**
- `ad_seller_system/src/ad_seller/interfaces/api/main.py:2035`
- `ad_buyer_system/src/ad_buyer/clients/unified_client.py:613-619`

### M5. openrtb_params structure mismatch
The seller returns `openrtb_params` as a plain `dict[str, Any]` (quotes.py:143).
The buyer defines a structured `OpenRTBParams` model with typed fields: `id`, `bidfloor`, `bidfloorcur`, `at`, `wseat`, `wadomain` (deals.py:96-103).

The buyer's `DealResponse.openrtb_params` is `Optional[OpenRTBParams]` so Pydantic will attempt validation. If the seller returns keys not matching these field names, or different types, deserialization will fail silently (extra fields ignored) or noisily (missing required fields).

**Files:**
- `ad_seller_system/src/ad_seller/models/quotes.py:143`
- `ad_buyer_system/src/ad_buyer/models/deals.py:96-103`

---

## Missing Integration Points

### I1. Buyer has no client for `/api/v1/deals/from-template`
The seller exposes `POST /api/v1/deals/from-template` as an MCP tool (`create_deal_from_template`) and REST endpoint. The buyer's `DealsClient` only covers the quote-then-book flow. There is no buyer-side client for the one-step template-based deal creation path.

**Files:**
- `ad_seller_system/src/ad_seller/interfaces/api/main.py:2766`
- `ad_seller_system/src/ad_seller/tools/deal_library/create_from_template.py`

### I2. Buyer has no proposal lifecycle events
The seller emits `proposal.received`, `proposal.evaluated`, `proposal.accepted`, `proposal.rejected`, `proposal.countered`. The buyer has no corresponding event types or handlers for these. The buyer's event bus has `quote.requested`, `quote.received`, `deal.booked`, `deal.cancelled` but nothing in the proposal namespace.

This means the buyer cannot participate in the seller's OpenDirect proposal-revision negotiation flow.

**Files:**
- `ad_seller_system/src/ad_seller/events/models.py:18-22`
- `ad_buyer_system/src/ad_buyer/events/models.py` (no proposal events)

### I3. Buyer MCP client expects OpenDirect tools the seller may not expose
The buyer's `SimpleMCPClient` fallback (mcp_client.py:88-94) assumes standard OpenDirect tools: `list_products`, `get_product`, `list_accounts`, `create_account`, `list_orders`, `create_order`, `list_lines`, `create_line`, `get_pricing`, `book_programmatic_guaranteed`, `create_pmp_deal`.

The seller's MCP server likely exposes a different tool set (Deal Library tools like `create_deal_from_template`, `push_deal_to_buyers`, `distribute_deal_via_ssp`, etc.). The buyer hardcodes `book_programmatic_guaranteed` and `create_pmp_deal` which are not in the seller's documented tool list.

**Files:**
- `ad_buyer_system/src/ad_buyer/clients/mcp_client.py:88-94`
- `ad_seller_system/README.md:130` (lists actual MCP tools)

### I4. No buyer-side support for deal push/distribution
The seller has `push_deal_to_buyers` and `distribute_deal_via_ssp` MCP tools. The buyer has no inbound webhook or polling mechanism to receive pushed deals.

### I5. No buyer-side support for deal migration/deprecation
The seller exposes `POST /api/v1/deals/{id}/migrate` and `POST /api/v1/deals/{id}/deprecate`. The buyer has no client methods for these lifecycle transitions.

### I6. Seller's BuyerIdentity has extra fields not sent by buyer
The seller's `BuyerIdentity` (buyer_identity.py) includes `campaign_id` and `campaign_name` fields. The buyer's `BuyerIdentity` does not have these, and the buyer's `DealRequest`/`QuoteRequest` do not send them. Not a bug, but a missed opportunity for campaign-level deal scoping.

**Files:**
- `ad_seller_system/src/ad_seller/models/buyer_identity.py:61-62`
- `ad_buyer_system/src/ad_buyer/models/buyer_identity.py`

### I7. Seller's BuyerContext has agent registry trust ceiling; buyer is unaware
The seller's `BuyerContext` includes `agent_url`, `agent_trust_status`, and `max_access_tier` from the agent registry. The buyer does not send `agent_url` in its API key or buyer identity payloads (except in `QuoteRequest.agent_url` added for the Deals API). If the buyer does not register as an agent, the trust ceiling logic is bypassed.

**Files:**
- `ad_seller_system/src/ad_seller/models/buyer_identity.py:136-138`
- `ad_buyer_system/src/ad_buyer/models/deals.py:125` (`agent_url` field)

---

## Naming: "Deal Jockey" References in Buyer

The seller renamed "Deal Jockey" to "Deal Library" in Task 0A. The buyer still has extensive "Deal Jockey" references:

| Location | Count | Nature |
|----------|-------|--------|
| `src/ad_buyer/agents/level2/deal_jockey_agent.py` | file | Agent module named after old branding |
| `src/ad_buyer/demo/dealjockey_dashboard.py` | file | Dashboard module |
| `src/ad_buyer/tools/deal_jockey/` | directory | Entire tool directory |
| `src/ad_buyer/agents/level2/__init__.py:12` | import | `create_deal_jockey_agent` |
| `src/ad_buyer/events/models.py:59-63` | enums | `DEAL_IMPORTED`, `DEAL_TEMPLATE_CREATED`, `PORTFOLIO_INSPECTED` under "DealJockey - Phase 1" comment |

**Impact:** These are buyer-internal names and do not affect wire-level compatibility. However, they will cause confusion in documentation, logs, and cross-team communication. The buyer's "Deal Jockey" agent is the buyer-side counterpart to the seller's "Deal Library" — having different names for the same concept across systems is a maintainability risk.

---

## Recommendations (Prioritized)

### P0 — Must fix before integration testing

1. **Align deal status values (M3).** Add `rejected` and `completed` to the seller's `DealBookingStatus` enum, or document that the buyer should treat `cancelled` as the terminal failure state. Both sides need the same status vocabulary.

2. **Remove or gate `UnifiedClient.request_deal()` (M4).** This method fabricates deal IDs client-side without contacting the seller. Either remove it, mark it as demo-only, or refactor it to call the seller's Deals API.

### P1 — Should fix before production

3. **Add buyer client for `/api/v1/deals/from-template` (I1).** This is the seller's primary one-step deal creation path. The buyer should have a `DealsClient` method for it.

4. **Formalize `openrtb_params` contract (M5).** Either make the seller return a structured object matching the buyer's `OpenRTBParams` schema, or make the buyer's field a plain `dict`.

5. **Update buyer MCP fallback tool list (I3).** Replace hardcoded `book_programmatic_guaranteed` / `create_pmp_deal` with actual seller tools like `create_deal_from_template`.

6. **Add buyer inbound deal push endpoint (I4).** If the seller's `push_deal_to_buyers` is meant for real-time deal distribution, the buyer needs a webhook or polling endpoint.

### P2 — Should fix for consistency

7. **Standardize `X-Api-Key` header casing (M1).** Pick one casing across all buyer clients. `X-Api-Key` matches the seller's FastAPI alias.

8. **Rename buyer's "Deal Jockey" to "Deal Library" or a buyer-appropriate name.** At minimum, update cross-system documentation to note the mapping.

9. **Add proposal lifecycle events to buyer (I2).** If the buyer will participate in OpenDirect proposal negotiations (beyond quote-then-book), it needs `proposal.*` event types.

### P3 — Nice to have

10. **Send `agent_url` in buyer requests (I7).** Register the buyer agent in the seller's agent registry to unlock trust-ceiling-aware tier resolution.

11. **Add `campaign_id`/`campaign_name` to buyer's identity payload (I6).** Enables campaign-level deal scoping on the seller side.
