# Seller-Agent — E2E cURL Test Guide

Concrete `curl` calls for every flow in `TEST_PLAN.md`, with real payloads, ID chaining, and the flow each covers. Runnable top-to-bottom.

## Conventions
```bash
export BASE=http://localhost:8000          # local server (uvicorn ad_seller.interfaces.api.main:app --port 8000)
# jq is used to capture IDs between calls. Auth accepts either header:
#   Authorization: Bearer <key>   OR   X-Api-Key: <key>
```
Enums used below:
- `deal_type`: `PG` | `PD` | `PA`
- `buyer_tier`: `public` | `seat` | `agency` | `advertiser`
- `order to_status`: draft→{submitted,cancelled} · submitted→{pending_approval,approved,cancelled,failed} · approved→{in_progress,cancelled} · in_progress→{syncing,failed,cancelled} · syncing→{booked,failed} · booked→{completed,unbooked}
- `trust_status`: `unknown` | `registered` | `approved` | `preferred` | `blocked`
- SSP names: `pubmatic` | `magnite` | `indexexchange`
- export `format`: `generic` | `ttd` | `dv360` | `amazon` | `xandr`

---

## 0. Smoke (no auth)
_Covers: server up, MCP + A2A exposure._
```bash
curl -s $BASE/health | jq .
curl -s $BASE/.well-known/agent.json | jq '.name, .capabilities'
# MCP liveness: the seller has NO /mcp/tools GET (that's a registry-only convenience route).
# MCP is JSON-RPC over Streamable HTTP at /mcp. Note: POST /mcp → 307 (redirects to /mcp/);
# POST /mcp/ with no handshake → 400. BOTH mean "reachable" (only 404/5xx = problem).
curl -s -o /dev/null -w "mcp: %{http_code}\n" -X POST "$BASE/mcp/" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# To actually list/run tools: use the registry "Tools Simulation" tab → http://localhost:8000/mcp,
# or an MCP client (raw curl needs the full initialize→initialized→tools/list session handshake).
```

---

## 1. Auth — create an API key
_Covers: buyer identity + API-key auth (used by all auth-required calls)._
```bash
export KEY=$(curl -s -X POST $BASE/auth/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "seat_id": "seat-qa-01",
    "seat_name": "QA Seat",
    "dsp_platform": "ttd",
    "advertiser_id": "adv-qa-01",
    "advertiser_name": "QA Advertiser",
    "label": "e2e-testing"
  }' | jq -r '.api_key')
echo "KEY=$KEY"     # shown once; reused below via -H "Authorization: Bearer $KEY"

curl -s $BASE/auth/api-keys | jq '.[]? // .'          # list (metadata only)
```
> ⚠️ **Known server bug (live-confirmed):** the API-key resolver reads the key as a *query param*, not a
> header, so `Authorization: Bearer`/`X-Api-Key` headers below are currently **ignored** (every request is
> treated as anonymous). Until fixed, tiered pricing won't apply and `from-template` always 401s. As a
> temporary workaround for testing, append the key as a query param, e.g. `"$BASE/api/v1/quotes?x_api_key=$KEY"`.

---

## 2. Inventory & products
_Covers: inventory sync (CSV) → product catalog._
```bash
curl -s -X POST $BASE/packages/sync -H "Authorization: Bearer $KEY" | jq .
curl -s $BASE/api/v1/inventory-sync/status | jq .
curl -s $BASE/api/v1/inventory-sync/watermark | jq .
curl -s -X POST "$BASE/api/v1/inventory-sync/trigger?incremental=true" | jq .

# capture a product_id for later — /products returns an OBJECT ({products:[…]}), not an array
curl -s $BASE/products | jq 'type, (keys? // empty)'   # inspect shape if unsure
export PID=$(curl -s $BASE/products | jq -r '(if type=="array" then . else (.products // .data // .items) end)[0].product_id')
echo "PID=$PID"
curl -s $BASE/products/$PID | jq .
```

---

## 3. Media kit / packages (+ v2.1 audience filters)
_Covers: package CRUD, audience_capabilities, audience filtering._
```bash
# public discovery
curl -s $BASE/media-kit/packages | jq '.packages[0]'
curl -s -X POST $BASE/media-kit/search -H "Content-Type: application/json" -d '{"query":"ctv"}' | jq .

# tier-gated list
curl -s "$BASE/packages?buyer_tier=advertiser" -H "Authorization: Bearer $KEY" | jq '.packages | length'

# create a curated package with audience_capabilities (v2.1)
export PKG=$(curl -s -X POST $BASE/packages -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "name": "QA CTV Sports Pack",
  "description": "E2E test package",
  "product_ids": ["'"$PID"'"],
  "cat": ["IAB17"],
  "cattax": 2,
  "audience_capabilities": {
    "standard_segment_ids": ["3-7"],
    "contextual_segment_ids": ["IAB1-2"],
    "agentic_capabilities": {}
  },
  "device_types": [3],
  "ad_formats": ["video"],
  "geo_targets": ["US"],
  "base_price": 32.0,
  "floor_price": 20.0,
  "tags": ["qa"],
  "is_featured": false
}' | jq -r '.package_id')
echo "PKG=$PKG"
# NOTE: reads return a PUBLIC SUMMARY of audience_capabilities — booleans + taxonomy versions
# (supports_standard / supports_contextual / supports_agentic / *_taxonomy_version), NOT the raw
# segment_ids you sent (those are hidden by design at the public tier). This is expected, not a bug.
curl -s $BASE/packages/$PKG -H "Authorization: Bearer $KEY" | jq '.audience_capabilities'

# audience filter (v2.1): standard segment
curl -s "$BASE/packages?audience_type=standard&audience_id=3-7&audience_taxonomy_version=1.1" -H "Authorization: Bearer $KEY" | jq '.packages | length'
# expect 400 when audience_id set without audience_type:
curl -s -o /dev/null -w "no-type: %{http_code}\n" "$BASE/packages?audience_id=3-7" -H "Authorization: Bearer $KEY"

# update / archive
curl -s -X PUT $BASE/packages/$PKG -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"is_featured": true}' | jq .status
curl -s -X DELETE $BASE/packages/$PKG -H "Authorization: Bearer $KEY" | jq .
```

---

## 4. Pricing → Quote → Deal  ⭐ core money path
_Covers: tiered pricing, quote (24h TTL), deal booking with audience plan._
_⚠️ Watch bugs #3 (floor→0), #5 (deal terms hardcoded)._
```bash
# pricing  — ⚠️ CURRENTLY BUGGED: returns 404 "Product not found" for a valid product
# (endpoint reads storage.get_product, which is never populated). cURL is correct; server bug.
curl -s -X POST $BASE/pricing -H "Content-Type: application/json" -d '{
  "product_id": "'"$PID"'", "buyer_tier": "agency", "volume": 1000000
}' | jq .

# quote
export QID=$(curl -s -X POST $BASE/api/v1/quotes -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "product_id": "'"$PID"'",
  "deal_type": "PG",
  "impressions": 1000000,
  "flight_start": "2026-08-01",
  "flight_end": "2026-08-31",
  "target_cpm": 30.0,
  "buyer_identity": {"seat_id":"seat-qa-01","advertiser_id":"adv-qa-01","dsp_platform":"ttd"}
}' | jq -r '.quote_id')
echo "QID=$QID"
curl -s $BASE/api/v1/quotes/$QID | jq '.status, .pricing.final_cpm, .expires_at'

# book deal from quote (+ audience_plan → snapshot + match summary)
# Happy path: primary=standard only. NOTE: constraints (contextual) / extensions (agentic)
# get a structured 400 `audience_plan_unsupported` unless the seller's package declares those
# capabilities (contextual_segment_ids / agentic_capabilities). See the agentic case below.
export DID=$(curl -s -X POST $BASE/api/v1/deals -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "quote_id": "'"$QID"'",
  "buyer_identity": {"seat_id":"seat-qa-01","advertiser_id":"adv-qa-01","dsp_platform":"ttd"},
  "notes": "e2e",
  "audience_plan": { "primary": {"type":"standard","identifier":"3-7"} }
}' | jq -r '.deal_id')
echo "DID=$DID"
curl -s $BASE/api/v1/deals/$DID | jq '.status, .audience_match_summary'

# Unsupported-role case (expect 400 audience_plan_unsupported for a seller that hasn't declared agentic):
curl -s -o /dev/null -w "agentic extension: %{http_code}\n" -X POST $BASE/api/v1/deals -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "quote_id": "'"$QID"'",
  "audience_plan": { "primary": {"type":"standard","identifier":"3-7"}, "extensions": [{"type":"agentic","identifier":"emb://x/q1"}] }
}'
```

---

## 5. Deal lifecycle
_Covers: from-template, export, migrate, deprecate, performance._
```bash
# create directly from template (auth required); 422 if max_cpm < floor
# ⚠️ CURRENTLY BUGGED: returns 401 even with a valid key (header-auth bug, see below) and 404 on
# product lookup (empty store). cURL is correct; endpoint is non-functional until those are fixed.
export DID2=$(curl -s -X POST $BASE/api/v1/deals/from-template -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "deal_type":"PD","product_id":"'"$PID"'","impressions":500000,"max_cpm":25.0,
  "flight_start":"2026-08-01","flight_end":"2026-08-31"
}' | jq -r '.deal_id')
echo "DID2=$DID2"

curl -s "$BASE/api/v1/deals/export?format=ttd" | jq .
curl -s "$BASE/api/v1/deals/export?format=dv360&status=confirmed" | jq .
curl -s $BASE/api/v1/deals/$DID2/performance | jq .

# migrate (old_deal_id must equal path id)
curl -s -X POST $BASE/api/v1/deals/$DID2/migrate -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "old_deal_id":"'"$DID2"'","deal_type":"PD","product_id":"'"$PID"'","max_cpm":26.0,"reason":"rate refresh"
}' | jq '.new_deal_id, .lineage'

curl -s -X POST $BASE/api/v1/deals/$DID/deprecate -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "reason":"superseded","replacement_deal_id":"'"$DID2"'"
}' | jq .
curl -s $BASE/api/v1/deals/$DID/lineage | jq .
```

---

## 6. Order state machine
_Covers: create order → transitions → invalid transition (409) → history._
```bash
export OID=$(curl -s -X POST $BASE/api/v1/orders -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "deal_id":"'"$DID2"'","metadata":{"campaign":"qa"}
}' | jq -r '.order_id')
echo "OID=$OID"

# valid path: draft → submitted → approved → in_progress → syncing → booked → completed
for S in submitted approved in_progress syncing booked completed; do
  curl -s -X POST $BASE/api/v1/orders/$OID/transition -H "Content-Type: application/json" \
    -d '{"to_status":"'"$S"'","actor":"qa","reason":"e2e"}' | jq -c '{status, allowed_next}'
done

# invalid transition → expect 409
curl -s -o /dev/null -w "invalid transition: %{http_code}\n" -X POST $BASE/api/v1/orders/$OID/transition \
  -H "Content-Type: application/json" -d '{"to_status":"draft"}'

curl -s $BASE/api/v1/orders/$OID/history | jq '.transitions | length'
curl -s $BASE/api/v1/orders/report | jq .
```

---

## 7. Approval gates
_Covers: request (via proposal when gate enabled) → list → decide → resume._
_Requires server started with `APPROVAL_GATE_ENABLED=true`._
```bash
curl -s $BASE/approvals | jq '.approvals'
export AID=$(curl -s $BASE/approvals | jq -r '.approvals[0].approval_id')
curl -s -X POST $BASE/approvals/$AID/decide -H "Content-Type: application/json" -d '{
  "decision":"approve","decided_by":"qa@example.com","reason":"within floor"
}' | jq .
curl -s -X POST $BASE/approvals/$AID/resume | jq .
```

---

## 8. SSP distribution
_Covers: distribute (explicit SSP + routing) + troubleshoot + push to buyers._
_Requires `SSP_CONNECTORS` configured for real SSPs._
```bash
curl -s -X POST $BASE/api/v1/deals/distribute -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "deal_id":"'"$DID2"'","deal_type":"PMP","name":"QA Deal","advertiser":"QA Advertiser",
  "cpm":25.0,"buyer_seat_ids":["seat-qa-01"],"ssp_name":"pubmatic","inventory_type":"ctv"
}' | jq .
curl -s "$BASE/api/v1/deals/$DID2/ssp-troubleshoot?ssp_name=pubmatic" | jq .

# push to buyer DSP endpoints (use a mock/echo URL if no real buyer)
curl -s -X POST $BASE/api/v1/deals/push -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "deal_id":"'"$DID2"'","buyer_urls":["https://httpbin.org/post"],"deal_type":"PD","price":25.0
}' | jq '.pushed_to, .succeeded, .failed'
```

---

## 9. Agentic audience match (v2.1)
_Covers: agentic match endpoint (⚠️ deterministic MOCK score — don't assert real quality)._
```bash
curl -s -X POST $BASE/agentic-audience/match -H "Content-Type: application/json" -d '{
  "audience_ref": {"type":"agentic","identifier":"emb://buyer.example.com/q1-converters"},
  "package_id": "'"$PKG"'"
}' | jq .
# structured rejection: non-agentic type
curl -s -X POST $BASE/agentic-audience/match -H "Content-Type: application/json" -d '{
  "audience_ref": {"type":"standard","identifier":"3-7"}
}' | jq .
```

---

## 10. Buyer registry & trust
_Covers: discover → set trust → blocked=403 → API-key auth boundary._
```bash
curl -s -X POST $BASE/registry/agents/discover -H "Content-Type: application/json" -d '{
  "agent_url":"https://buyer.example.com"
}' | jq .
export AGID=$(curl -s $BASE/registry/agents | jq -r '.[0].agent_id // .agents[0].agent_id')
curl -s -X PUT $BASE/registry/agents/$AGID/trust -H "Content-Type: application/json" -d '{
  "trust_status":"approved","notes":"qa approved"
}' | jq .
curl -s -X PUT $BASE/registry/agents/$AGID/trust -H "Content-Type: application/json" -d '{"trust_status":"blocked"}' | jq .

# auth boundary: mutation without key should 401/403
curl -s -o /dev/null -w "no-key create pkg: %{http_code}\n" -X POST $BASE/packages -H "Content-Type: application/json" -d '{"name":"x","base_price":1,"floor_price":1}'
```

---

## 11. Curators
_Covers: register curator → curated deal (fee overlay)._
```bash
curl -s -X POST $BASE/api/v1/curators -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "curator_id":"cur-qa","name":"QA Curator","domain":"curator.example.com",
  "curator_type":"audience","fee_type":"percent","fee_value":10.0
}' | jq .
curl -s -X POST $BASE/api/v1/deals/curated -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{
  "curator_id":"cur-qa","deal_type":"PMP","product_id":"'"$PID"'","max_cpm":30.0,
  "audience_segments":["seg-1"]
}' | jq '.deal_id, .pricing'
```

---

## 12. Rate card
_Covers: read + update rate card._
```bash
curl -s $BASE/api/v1/rate-card | jq .
curl -s -X PUT $BASE/api/v1/rate-card -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '[
  {"inventory_type":"ctv","base_cpm":40.0,"currency":"USD"},
  {"inventory_type":"display","base_cpm":5.0,"currency":"USD"}
]' | jq .
```

---

## 13. Negotiation (proposals)  ⚠️ known-broken
_Covers: proposal → counter-offer rounds._
_⚠️ Bug #4: proposals are never persisted → the counter call returns **404**. Included so you can confirm/track the bug._
```bash
export PROP=$(curl -s -X POST $BASE/proposals -H "Content-Type: application/json" -d '{
  "product_id":"'"$PID"'","deal_type":"PD","price":5.0,"impressions":500000,
  "start_date":"2026-08-01","end_date":"2026-08-31"
}' | jq -r '.proposal_id')
echo "PROP=$PROP"
# expected today: 404 "Proposal not found" (bug #4)
curl -s -o /dev/null -w "counter: %{http_code}\n" -X POST $BASE/proposals/$PROP/counter \
  -H "Content-Type: application/json" -d '{"buyer_price":6.0,"buyer_tier":"agency"}'
```

---

## 14. MCP tools (Streamable HTTP)
_Covers: MCP transport + tool calls. Use an MCP client, or the raw JSON-RPC below._
```bash
# initialize + list tools (Streamable HTTP; note the two headers)
curl -s -X POST $BASE/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# call a read tool
curl -s -X POST $BASE/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_setup_status","arguments":{}}}'
# ⚠️ Bug #1: {"name":"list_packages","arguments":{}} currently errors (MediaKitService DI).
```
> The `/validation` "Tools Simulation" tab (registry side) is the easy UI for MCP tool calls.

---

## Quick sequence (happy-path smoke)
```
health → api-keys → packages/sync → products → quotes → deals → orders → transition(completed)
```
Expect friction at: **quote→deal price** (bug #3/#5), **/proposals counter** (bug #4, 404), **list_packages MCP** (bug #1), and concurrency stalls on `/proposals`,`/deals`,`/discovery` (bug #2). See `TEST_PLAN.md` §2.

_Payloads verified against `src/ad_seller/interfaces/api/main.py` request models at review time._
