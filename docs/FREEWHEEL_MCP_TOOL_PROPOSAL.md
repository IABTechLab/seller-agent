# FreeWheel Dual-MCP Integration — Seller Agent Architecture & Tool Requirements

**From:** IAB Tech Lab Seller Agent Team
**To:** FreeWheel / Beeswax MCP Engineering
**Date:** March 3, 2026
**Re:** Dual-MCP integration architecture for CTV/Linear publisher pilot (Streaming Hub + Buyer Cloud)

---

## Executive Summary

The IAB Tech Lab Seller Agent manages the sell-side programmatic workflow for publishers. For the CTV/Linear publisher pilot, the seller agent will interact with FreeWheel through **two existing MCP servers**:

| MCP Server | URL | Role | Tool Count |
|---|---|---|---|
| **Streaming Hub** (MRM) | `shmcp.freewheel.com` | Publisher-side ad server — inventory, IOs, campaigns, placements, deals, audiences, forecasting | ~190 |
| **Buyer Cloud** (Beeswax) | `bcmcp.freewheel.com` | Demand-side execution — campaign management, creatives, bidding, reporting | ~191 |

The seller agent's internal adapter layer will route each operation to the correct MCP (or orchestrate across both when needed). This document describes:

1. **Which existing MCP tools** the seller agent will call on each server
2. **How cross-MCP operations work** (e.g., PG booking that spans SH + BC)
3. **The IAB standard primitives** used in all request/response data
4. **Gaps and enhancement requests** — capabilities we need that may not exist yet

**Important:** All data flowing between the seller agent and FreeWheel MCPs uses **IAB Tech Lab standard primitives** — OpenDirect 2.1 field naming, AdCOM enums, OpenRTB 2.5 deal semantics, and IAB taxonomy identifiers. The seller agent normalizes FreeWheel-native responses into these standards internally.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     IAB Tech Lab Seller Agent                │
│                                                              │
│  ProductSetupFlow / ExecutionActivationFlow / Negotiation    │
│                          │                                   │
│              FreeWheelAdServerClient (ABC)                   │
│              ┌───────────┼───────────┐                       │
│              │           │           │                       │
│         SH Client    Coordinator   BC Client                 │
│              │           │           │                       │
└──────────────┼───────────┼───────────┼───────────────────────┘
               │           │           │
    ┌──────────▼──┐   (orchestrates)  ┌▼──────────────┐
    │ Streaming   │                   │  Buyer Cloud   │
    │ Hub MCP     │   shared key:     │  MCP           │
    │ (Publisher) │◄── deal_id ──────►│  (Demand)      │
    │ shmcp.fw.com│                   │  bcmcp.fw.com  │
    └─────────────┘                   └────────────────┘
```

**Routing principle:** The seller agent determines which MCP to call based on the operation type:

| Operation Category | MCP Target | Rationale |
|---|---|---|
| Inventory discovery, ad slots, sites | **Streaming Hub** | Publisher's view of what's available |
| Audiences, segments, forecasting | **Streaming Hub** | Publisher-side targeting data |
| Programmatic deal setup | **Streaming Hub** | Direct deal creation (no IO/campaign/placement needed) |
| Deal activation in DSP, campaign execution | **Buyer Cloud** | Demand-side execution |
| Campaign reporting, delivery metrics | **Buyer Cloud** | Beeswax campaign performance |
| Creative management, attachment | **Buyer Cloud** | Demand-side creative workflow |
| PG booking (full) | **Both** | SH for deal setup, BC for campaign binding |

---

## IAB Standards Reference

The seller agent uses the following IAB standards internally. FreeWheel responses are normalized to these values by the seller agent's adapter layer. Where possible, FreeWheel MCP responses should use these values directly to minimize translation.

### DealType (OpenDirect 2.1)

| Value | Alias | Description |
|---|---|---|
| `"programmaticguaranteed"` | PG | Fixed price, guaranteed delivery |
| `"preferreddeal"` | PD | Fixed price, non-guaranteed |
| `"privateauction"` | PA | Floor price, auction-based |

### PricingModel (OpenDirect 2.1)

| Value | Description |
|---|---|
| `"cpm"` | Cost per mille (1,000 impressions) |
| `"cpv"` | Cost per view |
| `"cpc"` | Cost per click |
| `"cpcv"` | Cost per completed view |
| `"flat_fee"` | Flat fee |

### GoalType (OpenDirect 2.1)

| Value | Description |
|---|---|
| `"impressions"` | Impression-based delivery |
| `"clicks"` | Click-based delivery |
| `"viewableimpressions"` | Viewable impression-based |
| `"completions"` | Video completion-based |

### BillableEvent (OpenDirect 2.1)

| Value | Description |
|---|---|
| `"impression"` | Standard impression |
| `"viewableimpression"` | MRC-viewable impression |
| `"click"` | Click event |
| `"completion"` | Video completion event |

### AdCOM DeviceType Integers

| Value | Device |
|---|---|
| 1 | Mobile/Tablet |
| 2 | Personal Computer |
| 3 | Connected TV |
| 4 | Phone |
| 5 | Tablet |
| 6 | Connected Device |
| 7 | Set Top Box |

### Ad Formats (OpenRTB impression sub-objects)

`"banner"`, `"video"`, `"native"`, `"audio"`

### Content Categories

IAB Content Taxonomy 2.0 IDs (e.g., `"IAB19"` = Sports, `"IAB1"` = Arts & Entertainment). Indicated by `cattax: 2`.

### Audience Segments

IAB Audience Taxonomy 1.1 numeric IDs (e.g., `"3"`, `"4"`, `"5"`).

### Geo Targeting

ISO 3166-2 codes (e.g., `"US"`, `"US-NY"`, `"US-CA"`).

### Currency

ISO 4217 codes (e.g., `"USD"`).

### OpenRTB 2.5 Deal Auction Types

| `at` Value | Meaning |
|---|---|
| 1 | First-price auction (used for `privateauction`) |
| 3 | Fixed price (used for `programmaticguaranteed` and `preferreddeal`) |

---

## Seller Agent ↔ FreeWheel Terminology Mapping

| Seller Agent (OpenDirect) | FreeWheel Streaming Hub | FreeWheel Buyer Cloud |
|---|---|---|
| Order / ExecutionOrder | Insertion Order (IO) | — |
| Line Item (ProposalLine) | Campaign + Placement | Line Item |
| Deal (DealOutput) | Programmatic Deal | Deal |
| InventorySegment | Site + Site Section | — |
| AudienceSegment | Audience Item | — |
| Product | Product / Inventory Package | — |
| Creative | — | Creative |
| Campaign (execution) | Campaign (container) | Campaign |

---

## Part 1: Streaming Hub MCP — Tools Used by Seller Agent

The Streaming Hub MCP (`shmcp.freewheel.com`) is the publisher-side ad server. The seller agent uses it for all inventory, trafficking, and deal setup operations.

### 1.1 Authentication

| Tool | Purpose | Notes |
|---|---|---|
| `streaming_hub_login` | Establish session | Returns `session_id` injected into all subsequent calls |
| `streaming_hub_logout` | End session | Called on disconnect |

**Seller agent auth flow:** On `connect()`, the adapter calls `streaming_hub_login` with configured username/password. The returned `session_id` is stored and passed to all subsequent SH tool calls.

### 1.2 Inventory Discovery

| SH Tool | Seller Agent Method | What We Use It For |
|---|---|---|
| `sh_1_0_list-sites` | `list_inventory()` | Get top-level inventory hierarchy (networks/sites) |
| `sh_1_0_site-sections` | `list_inventory()` | Get child sections within a site |
| `sh_1_0_listinventorypackages` | Media kit sync | Get pre-packaged inventory bundles |

**Normalization:** SH site/section responses → `AdServerInventoryItem` (id, name, parent_id, status, sizes, ad_server_type="freewheel")

### 1.3 Audiences & Forecasting

| SH Tool | Seller Agent Method | What We Use It For |
|---|---|---|
| `sh_1_0_list-audience-items` | `list_audience_segments()` | List available 1P/3P/ACR audience segments |
| `sh_1_0_simplified-on-demand-forecastpostforecasts` | Availability check | Real-time avails for inventory + targeting + dates |
| `sh_1_0_getdealnightlyforecast` | Deal health monitoring | Nightly forecast for active deals |

**Normalization:** SH audience items → `AdServerAudienceSegment` (id, name, description, size, status, ad_server_type="freewheel")

### 1.4 Insertion Order (IO) Management — DIRECT-SOLD ONLY

> **NOT USED for programmatic deals.** IOs are a direct-sold concept on FreeWheel.
> Programmatic deals are created directly via `book_deal()` without IO/campaign/placement.

| SH Tool | Seller Agent Method | What We Use It For |
|---|---|---|
| `sh_1_1_create-an-insertion-order` | — | Direct-sold only (not used by seller agent) |
| `sh_1_1_get-a-insertion-order` | — | Direct-sold only |
| `sh_1_1_book-an-insertion-order` | — | Direct-sold only |
| `sh_1_1_update-an-insertion-order` | — | Direct-sold only |

**Normalization:** SH IO responses → `AdServerOrder` (id, name, advertiser_id, status → OrderStatus enum, ad_server_type="freewheel")

**Status mapping:**

| FreeWheel IO Status | Seller Agent OrderStatus |
|---|---|
| CREATED | `"draft"` |
| PENDING | `"pending_approval"` |
| BOOKED | `"approved"` |
| PAUSED | `"paused"` |
| CANCELLED | `"canceled"` |
| COMPLETED | `"completed"` |

### 1.5 Campaign & Placement (Line Item) — DIRECT-SOLD ONLY

> **NOT USED for programmatic deals.** Campaigns and placements are direct-sold
> concepts on Streaming Hub. Philippe has offered to recycle `*_line_item()` for
> Buyer Cloud campaign/line item management in a future phase.

| SH Tool | Seller Agent Method | What We Use It For |
|---|---|---|
| `sh_1_1_create-a-campaign` | — | Direct-sold only (not used by seller agent) |
| `sh_1_1_create-a-placement` | — | Direct-sold only |
| `sh_1_1_update-a-placement` | — | Direct-sold only |
| `sh_1_1_get-a-campaign` | — | Direct-sold only |

### 1.6 Programmatic Deal Management

| SH Tool | Seller Agent Method | What We Use It For |
|---|---|---|
| `sh_1_0_createdeal` | `create_deal()` (step 1) | Create deal with external deal_id, pricing, inventory |
| `sh_1_0_activatedeal` | `create_deal()` (step 2) | Activate deal for bidding |
| `sh_1_0_updatedeal` | `update_deal()` | Modify deal pricing/dates/targeting |
| `sh_1_0_searchdeals` | — | Find existing deals |

**Deal creation flow (SH side):**
1. `sh_1_0_createdeal` — creates deal with external `deal_id` (used in OpenRTB bid requests), pricing, inventory references, buyer seat IDs
2. `sh_1_0_activatedeal` — transitions deal from draft → active, making it available in the exchange

**Normalization:** SH deal responses → `AdServerDeal` (id, deal_id, name, deal_type → DealType, floor_price_micros, fixed_price_micros, currency, buyer_seat_ids, status → DealStatus, ad_server_type="freewheel")

**Status mapping:**

| FreeWheel Deal Status | Seller Agent DealStatus |
|---|---|
| DRAFT | `"active"` (pre-activation) |
| ACTIVE | `"active"` |
| PAUSED | `"paused"` |
| ARCHIVED | `"archived"` |

---

## Part 2: Buyer Cloud MCP — Tools Used by Seller Agent

The Buyer Cloud MCP (`bcmcp.freewheel.com`) is the demand-side (Beeswax DSP). The seller agent uses it for campaign execution, creative management, and reporting.

### 2.1 Authentication

| Tool | Purpose | Notes |
|---|---|---|
| OAuth 2.0 token endpoint | Get access token | `client_id` + `client_secret` → `access_token` |
| `buyer_cloud_login` | Establish session | Email/password/buzz_key → session cookie |
| `buyer_cloud_logout` | End session | Called on disconnect |

**Seller agent auth flow:** On `connect()`, the adapter first obtains an OAuth 2.0 access token, then calls `buyer_cloud_login` with email/password/buzz_key. Both credentials are needed for full API access.

### 2.2 Campaign Execution

| BC Tool | Purpose | When Used |
|---|---|---|
| `bc_v2_get_campaigns` | List campaigns | Verify BC-side campaign state |
| `bc_v2_create_campaign` | Create BC campaign | PG booking (bind to SH deal) |
| `bc_v2_get_line_items` | List line items | Campaign delivery details |
| `bc_v2_create_line_item` | Create BC line item | PG booking (budget/targeting) |
| `bc_v2_activate_campaign` | Activate campaign | PG booking final step |

### 2.3 Creative Management

| BC Tool | Purpose | When Used |
|---|---|---|
| `bc_v2_get_creatives` | List creatives | Verify creative status |
| `bc_v2_create_creative` | Upload creative | Creative sync workflow |
| `bc_v2_attach_creative_to_line_item` | Associate creative | PG booking creative attachment |

### 2.4 Reporting & Analytics

| BC Tool | Purpose | When Used |
|---|---|---|
| `bc_v2_get_reporting_impressions` | Impression delivery report | Campaign monitoring |
| `bc_v2_get_reporting_revenue` | Revenue report | Deal performance |
| `check_campaign_health` | Campaign health check | Proactive monitoring |
| `deal_analyst` | Deal performance analysis | Deal optimization |

---

## Part 3: Cross-MCP Operations

Some operations require coordinated calls to **both** MCP servers. The seller agent's internal coordinator handles sequencing and error recovery.

### 3.1 The Shared Key: `deal_id`

The **external deal ID** (the OpenRTB deal ID used in bid requests) is the shared key that binds entities across both systems:

- **Streaming Hub** creates the deal with the external `deal_id`
- **Buyer Cloud** references the same `deal_id` when creating/linking a campaign
- The seller agent tracks all entity IDs across both systems in a binding record

```
┌─ Streaming Hub ─────────────────┐     ┌─ Buyer Cloud ──────────────────┐
│                                 │     │                                │
│  Inventory ──── Deal ───────────┼──►──┼──── Campaign ──── Line Items  │
│                           deal_id     │       │                        │
│  Audiences            (shared key)    │    Creatives                   │
│                                 │     │                                │
│  Forecasting                    │     │    Reporting                   │
└─────────────────────────────────┘     └────────────────────────────────┘
```

> **CONFIRMED by Philippe (FreeWheel):** Streaming Hub programmatic deals do NOT
> require IO, Campaign, or Placement objects. Those are direct-sold concepts only.
> The SH side is simpler: deals are created directly via `book_deal()`.

### 3.2 Cross-MCP Binding Record

For every cross-MCP operation, the seller agent maintains a binding record:

```json
{
  "deal_id": "IAB-A1B2C3D4E5F6",
  "sh_io_id": "fw-io-789",
  "sh_campaign_id": "fw-camp-100",
  "sh_placement_id": "fw-plc-200",
  "sh_deal_id": "fw-deal-001",
  "bc_campaign_id": "bc-camp-500",
  "bc_line_item_ids": ["bc-li-600"],
  "bc_creative_ids": ["bc-cr-700"],
  "binding_status": "complete",
  "created_at": "2026-03-03T12:00:00Z"
}
```

### 3.3 Partial Failure Handling

If Streaming Hub succeeds but Buyer Cloud fails during a cross-MCP operation:

1. The deal exists in SH and is active in the exchange
2. The seller agent returns `binding_status: "partial"` with a warning
3. The BC-side can be completed manually or retried
4. The deal is still functional — a buyer can target it from any DSP via the deal ID

This design ensures that **no deal is lost** due to a BC failure.

---

## Part 4: End-to-End Use Cases

### UC1: Buyer Discovers Inventory

```
Buyer Agent → Seller Agent.list_inventory()
  └→ SH: sh_1_0_list-sites (get networks/sites)
  └→ SH: sh_1_0_site-sections (get sections for each site)
  └→ SH: sh_1_0_listinventorypackages (get pre-built packages)
  ←── Returns: AdServerInventoryItem[] (normalized)
      + Package[] (media kit)
```

**MCP:** Streaming Hub only

### UC2: Buyer Checks Availability

```
Buyer Agent → Seller Agent.check_avails(inventory_ids, dates, targeting)
  └→ SH: sh_1_0_simplified-on-demand-forecastpostforecasts
  ←── Returns: available impressions, contended impressions, probability
```

**MCP:** Streaming Hub only

### UC3: Book PD/PA Deal (Deal ID Path — Streaming Hub Only)

For Preferred Deals and Private Auctions, the deal ID is created in Streaming Hub and handed to the buyer's DSP. No Buyer Cloud involvement needed.

```
Negotiation accepted → DealGenerationFlow → ExecutionActivationFlow:

  1. SH: sh_1_0_createdeal
     - External deal_id (OpenRTB)
     - Deal type: "preferreddeal" or "privateauction"
     - Pricing: floor_price_micros (PA) or fixed_price_micros (PD)
     - Inventory references
     - Buyer seat IDs (wseat)

  2. SH: sh_1_0_activatedeal
     - Transition deal to active status

  3. Return to buyer:
     - deal_id for entry into their DSP
     - OpenRTB deal parameters (bidfloor, bidfloorcur, at, wseat)

Seller Agent Response (BookingResult):
  {
    "deal": {
      "deal_id": "IAB-A1B2C3D4E5F6",
      "deal_type": "preferreddeal",
      "status": "active",
      "fixed_price_micros": 28000000,
      "currency": "USD",
      "buyer_seat_ids": ["ttd-seat-123"]
    },
    "openrtb_params": {
      "id": "IAB-A1B2C3D4E5F6",
      "bidfloor": 28.0,
      "bidfloorcur": "USD",
      "at": 3,
      "wseat": ["ttd-seat-123"]
    },
    "success": true
  }
```

**MCP:** Streaming Hub only

### UC4: Book PG Deal (Cross-MCP)

> **REVISED per Philippe's feedback:** No IO/campaign/placement needed on SH.
> Philippe's `book_deal()` handles both SH and BC deal creation in one call.

Programmatic Guaranteed deals require setup in **both** MCPs. Philippe's
`book_deal()` creates the deal on SH and BC. BC campaign/line item/creative
management is a separate step (Phase 3).

```
Negotiation accepted → ExecutionActivationFlow:

  ── Step 1: Deal creation (SH + BC) ─────────────────────

  1. Philippe's book_deal()
     - Creates deal on Streaming Hub (with external deal_id, pricing, inventory)
     - Creates deal on Buyer Cloud (linked via deal_id)
     - Does NOT create BC campaign/line item/creative

  ← Return: BookingResult with deal_id

  ── Step 2: BC campaign setup (Phase 3 — future) ────────

  2. BC: campaign + line item creation (when Philippe adds *_line_item() for BC)
  3. BC: creative attachment
  4. BC: campaign activation
```

**MCP:** Both (via Philippe's `book_deal()` which orchestrates SH + BC)

### UC5: Campaign Reporting

```
Seller Agent monitoring:

  Publisher-side forecast:
  └→ SH: sh_1_0_getdealnightlyforecast (deal delivery forecast)

  Demand-side performance:
  └→ BC: bc_v2_get_reporting_impressions (impression delivery)
  └→ BC: bc_v2_get_reporting_revenue (revenue metrics)
  └→ BC: check_campaign_health (built-in analytics tool)
  └→ BC: deal_analyst (built-in analytics tool)
```

**MCP:** Both (SH for forecasting, BC for execution reporting)

---

## Part 5: MCP Routing Table (Complete)

This table shows every `AdServerClient` method and which MCP tool(s) the seller agent calls.

| Seller Agent Method | MCP | Streaming Hub Tool(s) | Buyer Cloud Tool(s) |
|---|---|---|---|
| `connect()` | Both | `streaming_hub_login` | OAuth 2.0 + `buyer_cloud_login` |
| `disconnect()` | Both | `streaming_hub_logout` | `buyer_cloud_logout` |
| `list_inventory()` | SH | `sh_1_0_list-sites` + `sh_1_0_site-sections` | — |
| `list_audience_segments()` | SH | `sh_1_0_list-audience-items` | — |
| `create_order()` | — | Not used (direct-sold only) | — |
| `get_order()` | — | Not used (direct-sold only) | — |
| `approve_order()` | — | Not used (direct-sold only) | — |
| `create_line_item()` | — | Not used on SH (direct-sold only) | Phase 3: BC campaign/line item |
| `update_line_item()` | — | Not used on SH (direct-sold only) | Phase 3: BC line item updates |
| `create_deal()` | SH | `sh_1_0_createdeal` + `sh_1_0_activatedeal` | — |
| `update_deal()` | SH | `sh_1_0_updatedeal` | — |
| `book_deal()` — PD/PA | Both | Philippe's `book_deal` (creates deal on SH + BC) | — |
| `book_deal()` — PG | Both | Philippe's `book_deal` (creates deal on SH + BC) | Phase 3: campaign + line item + creative |

### Philippe's Implemented MCP Tools (CONFIRMED)

| MCP Tool | Status | Description |
|---|---|---|
| `list_inventory()` | ✅ Implemented | Returns "template deals" from SH representing packages. Not yet OpenDirect compliant but maps to IAB taxonomy. |
| `list_audience_segments()` | ⚠️ Partial | Audience segment listing from SH. |
| `book_deal()` | ✅ Implemented | Creates deal on BOTH SH and BC. Does NOT create campaign/line item/creative on BC. |
| `update_deal()` | ✅ Implemented | Updates deal on SH and BC. |

---

## Part 6: Gaps & Enhancement Requests

After mapping the seller agent's requirements to the existing ~380 tools across both MCPs, we have identified the following gaps and requests.

### 6.1 Streaming Hub — Needed But Possibly Missing

| Capability | Status | Notes |
|---|---|---|
| **Create deal with external deal_id** | `sh_1_0_createdeal` exists | **Confirm:** Can we pass an external `deal_id` string (for OpenRTB `imp.pmp.deals[].id`) at creation time? |
| **Deal buyer seat IDs** | Likely in `sh_1_0_createdeal` params | **Confirm:** Can we specify `buyer_seat_ids` (maps to OpenRTB `wseat`) at deal creation? |
| **Deal activation** | `sh_1_0_activatedeal` exists | OK |
| **Forecasting with audience targeting** | `sh_1_0_simplified-on-demand-forecastpostforecasts` exists | **Confirm:** Can we pass audience segment IDs into the forecast request? |
| **Inventory package listing** | `sh_1_0_listinventorypackages` exists | **Confirm:** Response includes ad format, device type, content category metadata? |

### 6.2 Buyer Cloud — Needed But Possibly Missing

| Capability | Status | Notes |
|---|---|---|
| **Link BC campaign to SH deal by deal_id** | Needs confirmation | **Critical:** How does a BC campaign reference a deal created in SH? Is `deal_id` a field on BC campaign/line item creation? |
| **Creative approval status** | `bc_v2_get_creatives` likely includes status | **Confirm:** Response includes review/approval status for PG creative pre-approval? |
| **Deal-level reporting** | `deal_analyst` tool exists | **Confirm:** Can we query by external `deal_id`? |

### 6.3 Cross-MCP — Key Questions for FreeWheel Engineering

1. **Deal ID binding:** What is the exact field/parameter used to link a Buyer Cloud campaign to a Streaming Hub deal? Is it `deal_id`, `external_deal_id`, or another field?

2. **Authentication scope:** Can a single set of credentials access both SH and BC, or are these always separate credential sets?

3. **Deal creation sequencing:** For PG deals, must the SH deal exist before the BC campaign is created? Or can they be created in parallel with binding after?

4. **Inventory reference format:** When creating a deal in SH with `sh_1_0_createdeal`, what format are inventory references? Site IDs? Section IDs? Slot IDs?

5. **Price format in SH deals:** Does `sh_1_0_createdeal` accept prices in microcurrency (micro-USD), or does it use decimal dollars? The seller agent uses microcurrency internally.

6. **Real-time vs. batch activation:** After `sh_1_0_activatedeal`, how quickly is the deal available for bidding? Is there a propagation delay?

7. **IO booking atomicity:** Does `sh_1_1_book-an-insertion-order` validate all placements before committing, or can it partially succeed?

### 6.4 Enhancement Requests (Nice-to-Have)

These are not blockers but would improve the integration:

| Enhancement | MCP | Description |
|---|---|---|
| **IAB-standard deal type in responses** | SH | Return `"programmaticguaranteed"` instead of FW-internal values. Reduces mapping overhead. |
| **OpenRTB deal params in deal responses** | SH | Include `bidfloor`, `bidfloorcur`, `at` in `createdeal`/`activatedeal` responses. Lets us pass deal params directly to DSPs. |
| **Composite PG booking tool** | SH + BC | A single tool that creates IO + campaign + placement + deal + books + creates BC campaign in one call. Would reduce round-trips from 10 to 1. |
| **Deal status webhooks** | Both | Push notifications when deal status changes (buyer acceptance, creative approval, delivery milestones). Currently we must poll. |
| **Bulk inventory listing** | SH | A single call that returns the full site→section→slot hierarchy rather than requiring site-by-site traversal. |

---

## Part 7: Shared Conventions

### Pricing in Microcurrency

The seller agent stores all prices in **microcurrency** (1 USD = 1,000,000 micro-USD). This avoids floating-point precision issues. If FreeWheel MCP tools use decimal dollars, the seller agent will convert internally.

| Human Price | Microcurrency |
|---|---|
| $15.00 CPM | `15000000` |
| $28.50 CPM | `28500000` |
| $0.05 CPC | `50000` |

### DeliveryGoal Object

Used across the seller agent's line item and deal workflows:

```json
{
  "goaltype": "impressions",
  "goalamount": 5000000,
  "billableevent": "impression"
}
```

Field names use OpenDirect 2.1 camelCase aliases (no underscores).

### OpenRTB Deal Parameters

For all programmatic deals, the seller agent generates OpenRTB 2.5-compatible parameters for DSP distribution:

```json
{
  "id": "IAB-A1B2C3D4E5F6",
  "bidfloor": 28.0,
  "bidfloorcur": "USD",
  "at": 3,
  "wseat": ["ttd-seat-123"],
  "wadomain": [],
  "ext": {
    "guaranteed": true,
    "impressions": 5000000
  }
}
```

If FreeWheel SH deal responses include these fields natively, the seller agent can use them directly. Otherwise, the adapter constructs them from the deal entity fields.

### Targeting Object

The seller agent uses a consistent targeting schema across all operations:

```json
{
  "geo": ["US", "US-NY"],
  "device_types": [3, 7],
  "cat": ["IAB19"],
  "cattax": 2,
  "audience_segment_ids": ["fw-seg-001"],
  "custom": {
    "genre": ["sports", "news"],
    "daypart": ["primetime"]
  }
}
```

Standard fields use IAB identifiers; `custom` contains FreeWheel-specific key-values.

### Status Enums (Seller Agent)

| Entity | Status Values |
|---|---|
| Order | `"draft"`, `"pending_approval"`, `"approved"`, `"rejected"`, `"paused"`, `"canceled"`, `"completed"` |
| Line Item | `"draft"`, `"ready"`, `"delivering"`, `"paused"`, `"completed"`, `"canceled"` |
| Deal | `"draft"`, `"active"`, `"paused"`, `"archived"` |
| Creative Review | `"pending"`, `"approved"`, `"rejected"` |

### Error Response Contract

All MCP tools follow the JSON-RPC 2.0 error format:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32001,
    "message": "Insertion order not found",
    "data": {
      "fw_error_code": "ORDER_NOT_FOUND",
      "retryable": false
    }
  }
}
```

---

## Part 8: Integration Timeline

### Phase 1 — Read-Only (Unblocks Inventory Discovery + Avails)

The seller agent connects to **Streaming Hub only** and performs read operations:
- `list_inventory()` via `sh_1_0_list-sites` + `sh_1_0_site-sections`
- `list_audience_segments()` via `sh_1_0_list-audience-items`
- Avails checking via `sh_1_0_simplified-on-demand-forecastpostforecasts`
- Product/package discovery via `sh_1_0_listinventorypackages`

**Deliverable:** Seller agent can populate its media kit from FreeWheel inventory and respond to buyer avails queries.

### Phase 2 — PD/PA Deal Booking (Streaming Hub Only)

The seller agent creates and activates deals:
- `create_deal()` via `sh_1_0_createdeal` + `sh_1_0_activatedeal`
- `update_deal()` via `sh_1_0_updatedeal`
- IO management via `sh_1_1_create-an-insertion-order`, `sh_1_1_book-an-insertion-order`
- Line items via `sh_1_1_create-a-campaign` + `sh_1_1_create-a-placement`

**Deliverable:** Full PD/PA deal lifecycle. Buyer gets deal_id to enter in any DSP.

### Phase 3 — PG Booking + Reporting (Both MCPs)

The seller agent connects to **both** MCPs for cross-MCP PG booking:
- SH: Full IO + deal setup (steps 1-6 from UC4)
- BC: Campaign creation and binding (steps 7-10 from UC4)
- BC: Reporting via `bc_v2_get_reporting_*`, `check_campaign_health`, `deal_analyst`
- BC: Creative management via `bc_v2_create_creative`, `bc_v2_attach_creative_to_line_item`

**Deliverable:** Complete PG workflow with campaign execution and reporting.

---

## Appendix A: Complete Tool Usage Inventory

### Streaming Hub Tools Used (~15 tools)

| # | SH Tool Name | Category | Seller Agent Usage |
|---|---|---|---|
| 1 | `streaming_hub_login` | Auth | Session establishment |
| 2 | `streaming_hub_logout` | Auth | Session cleanup |
| 3 | `sh_1_0_list-sites` | Inventory | List sites/networks |
| 4 | `sh_1_0_site-sections` | Inventory | List sections within site |
| 5 | `sh_1_0_listinventorypackages` | Inventory | List inventory packages |
| 6 | `sh_1_0_list-audience-items` | Audience | List audience segments |
| 7 | `sh_1_0_simplified-on-demand-forecastpostforecasts` | Forecasting | Avails/forecast |
| 8 | `sh_1_0_getdealnightlyforecast` | Forecasting | Deal delivery forecast |
| 9 | `sh_1_1_create-an-insertion-order` | IO | Create IO |
| 10 | `sh_1_1_get-a-insertion-order` | IO | Get IO details |
| 11 | `sh_1_1_book-an-insertion-order` | IO | Approve/book IO |
| 12 | `sh_1_1_create-a-campaign` | Campaign | Create campaign container |
| 13 | `sh_1_1_create-a-placement` | Placement | Create placement under campaign |
| 14 | `sh_1_1_update-a-placement` | Placement | Update placement |
| 15 | `sh_1_0_createdeal` | Deal | Create programmatic deal |
| 16 | `sh_1_0_activatedeal` | Deal | Activate deal |
| 17 | `sh_1_0_updatedeal` | Deal | Update deal |
| 18 | `sh_1_0_searchdeals` | Deal | Search/list deals |

### Buyer Cloud Tools Used (~12 tools)

| # | BC Tool Name | Category | Seller Agent Usage |
|---|---|---|---|
| 1 | `buyer_cloud_login` | Auth | Session establishment |
| 2 | `buyer_cloud_logout` | Auth | Session cleanup |
| 3 | `bc_v2_get_campaigns` | Campaign | List campaigns |
| 4 | `bc_v2_create_campaign` | Campaign | Create campaign (PG binding) |
| 5 | `bc_v2_activate_campaign` | Campaign | Activate campaign |
| 6 | `bc_v2_get_line_items` | Line Item | List line items |
| 7 | `bc_v2_create_line_item` | Line Item | Create line item (PG binding) |
| 8 | `bc_v2_get_creatives` | Creative | List creatives |
| 9 | `bc_v2_create_creative` | Creative | Upload creative |
| 10 | `bc_v2_attach_creative_to_line_item` | Creative | Associate creative |
| 11 | `bc_v2_get_reporting_impressions` | Reporting | Impression report |
| 12 | `bc_v2_get_reporting_revenue` | Reporting | Revenue report |
| 13 | `check_campaign_health` | Reporting | Campaign health |
| 14 | `deal_analyst` | Reporting | Deal analysis |

---

## Appendix B: IAB Standards Referenced

| Standard | Version | Usage |
|---|---|---|
| OpenDirect | 2.1 | Entity naming, field aliases, enums (DealType, PricingModel, GoalType, BillableEvent) |
| AdCOM | 1.0 | DeviceType integers, AdProfile values |
| OpenRTB | 2.5 | Deal wire format (`bidfloor`, `bidfloorcur`, `at`, `wseat`, `wadomain`, `ext`) |
| IAB Content Taxonomy | 2.0 | Content categorization (`cat`, `cattax`) |
| IAB Audience Taxonomy | 1.1 | Audience segment classification |
| ISO 4217 | — | Currency codes |
| ISO 3166-2 | — | Geo targeting codes |
| ISO 8601 | — | Date/time formatting |
