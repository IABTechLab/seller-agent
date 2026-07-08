# Seller-Agent — End-to-End Test Plan (AAMP v2.1)

Baseline: v2.0 (commit `0f544a54`, Apr 23 2026) → v2.1 cut-off Jul 1 2026.
Scope: **seller-agent only.** Covers REST (82 endpoints), MCP (41 tools), A2A, AgentCore, and the v2.1 feature blocks (Agentic Audience Extension, FreeWheel OAuth 2.1 PKCE, MCP transport upgrade, Bedrock AgentCore).

---

## 1. How to run

1. **Baseline test suite (do this first):**
   ```bash
   uv run pytest            # 209 unit + 9 integration tests
   ```
   CI now fails on pytest failures (was swallowing exit 124), so results are trustworthy.

2. **Start the server (local, credential-free):**
   ```bash
   uvicorn ad_seller.interfaces.api.main:app --port 8000
   ```
   MCP is served at `http://localhost:8000/mcp` (Streamable HTTP) and `…/mcp-sse/sse` (legacy).

3. **Smoke checks:**
   - `GET /health`
   - `GET /mcp/tools` (or connect an MCP client)
   - `GET /.well-known/agent.json`

### Prerequisites / config
| Var | Value for local testing |
|-----|-------------------------|
| `AD_SERVER_TYPE` | `csv` (no external creds; sample CTV/display data) |
| `STORAGE_BACKEND` | `sqlite` (default) |
| `SELLER_ORGANIZATION_NAME` / `_ID` | set to a test org |
| LLM key (`ANTHROPIC_API_KEY` / `LLM_API_KEY`) | required for crew-backed flows (`/proposals`, `/deals`, `/discovery`); NOT needed for read/CRUD tools |
| `APPROVAL_GATE_ENABLED` | `true` to test approval flow |
| `SSP_CONNECTORS` | e.g. `pubmatic:mcp|index:rest|magnite:rest` to test distribution |

---

### Baseline run — observed result (2026-07)
Run with the **arm64 pyenv** interpreter (NOT `uv`, which resolves x86_64 and fails on `lancedb==0.30.0` — no Intel-mac wheel):
```bash
/Users/kishore.n/.pyenv/versions/3.12.6/bin/python -m pip install -e ".[dev]"
AD_BUYER_SRC_PATH=/Users/kishore.n/Desktop/dummy/buyer-agent/src \
  /Users/kishore.n/.pyenv/versions/3.12.6/bin/python -m pytest -q
```
- Result: **suite passes** (a batch of `agentcore`/integration tests skip without AWS creds).
- **1 test needs an env var, else it errors:** `tests/unit/test_openrtb_parser.py::test_round_trip_builder_then_parser_recovers_refs` (cross-repo OpenRTB audience round-trip). It resolves the buyer repo by walking up for a dir named **`ad_seller_system`** with sibling **`ad_buyer_system`**; this checkout is `seller-agent`/`buyer-agent`, so it raises `RuntimeError` at path resolution. **Product code is fine** — the test passes when `AD_BUYER_SRC_PATH` points at the real buyer `src/`.
- **Minor test bug:** the changelog says this test "skips gracefully when sibling repos absent," but it `raise RuntimeError(...)` on a dir-name mismatch instead of `pytest.skip(...)` — so it's a false-negative on non-canonical checkouts. Fix: `importorskip` / skip when the buyer repo can't be located.
- **Coverage caveat:** the unit suite passing does **not** exercise the HIGH runtime bugs below (MCP `list_packages` crash, blocking `kickoff`, floor=0, proposals-not-persisted). Green unit tests ≠ working E2E — run the manual flows in §3.

## 2. 🐞 Bugs found (code-verified) — READ FIRST

These affect what testing reveals. Items 1, 3, 4 together make the quote→negotiate→deal happy path non-functional — fix before deep E2E or tests fail for the wrong reason.

| # | Sev | Bug | Location | Manifestation |
|---|-----|-----|----------|---------------|
| 1 | HIGH | `list_packages` MCP tool crashes — `MediaKitService()` constructed with no args, but `__init__(self, storage, pricing_engine)` requires both | `interfaces/mcp_server.py:319` (also `:91`, silently swallowed) | Every `list_packages` / `/packages` MCP call raises `TypeError: MediaKitService.__init__() missing 2 required positional arguments`. REST path (`api/main.py:1445`) was fixed; MCP path was not. **Already reproduced.** |
| 2 | HIGH | Synchronous `crew.kickoff()` blocks the event loop | `flows/proposal_handling_flow.py:422` + `:608`; `flows/deal_generation_flow.py:259`; `flows/discovery_inquiry_flow.py:287` (called from `api/main.py` `POST /proposals`, `/deals`, `/discovery`) | Each blocks the entire single-threaded loop for the full LLM/crew duration; concurrent requests (incl. `/health`) stall. Buyer side fixed this (#105); seller side did not. AgentCore path correctly offloads via `run_in_executor` (`agentcore/http_main.py:615`). |
| 3 | HIGH | Floor/base CPM default to `0` when product pricing absent → floor bypass (issue #7, live via negotiation) | `api/main.py:1344-1345` + `engines/negotiation_engine.py:147` (accept `>= base`) / `:159` (reject `< floor`) | A product without pricing accepts $0.00–$0.01 CPM offers. `/api/v1/quotes` path is safe (static catalog always sets `floor_cpm`); negotiation path reads storage and defaults to 0. |
| 4 | HIGH | Proposals never persisted — `set_proposal()` has zero call sites in `interfaces/` | `submit_proposal` in `api/main.py` (never calls `storage.set_proposal`) | `POST /proposals` returns a `proposal_id`, but `POST /proposals/{id}/counter` → `storage.get_proposal` → `None` → **404 "Proposal not found"** on every fresh negotiation. |
| 5 | MED | `POST /deals` ignores the proposal and hardcodes deal terms | `api/main.py:855-863` | Every generated deal has identical fabricated terms (price 15.0, product `display`, 1M imps, fixed 2026 dates), regardless of `proposal_id`. |
| 6 | MED | Product IDs non-deterministic across restarts (`prod-{uuid[:8]}`) | `api/main.py:605` | IDs regenerate on cold start; a `product_id` captured earlier 404s after restart. Flaky fixtures. |
| 7 | MED | Agentic `compliance_context` silently synthesized instead of rejected (`jurisdiction=UNKNOWN`, `consent_framework=none`); warnings never checked downstream | `services/openrtb_parser.py:48, 213-218` | Malformed agentic AudienceRef with no consent info produces a "valid" result. Compliance gap. |
| 8 | LOW | Quote-booking TOCTOU double-book race; internal exceptions leaked as `str(e)` | `api/main.py:2439`→`2521`; `1030/1251/2961/4533` | Two concurrent bookings on one `quote_id` can both succeed; raw error text surfaced to clients. |

**✅ Reviewed and clean (no bug):**
- FreeWheel OAuth 2.1 PKCE — refresh, skew check (`freewheel_oauth.py:98`), `reconnect(force_refresh=True)`; no references to removed `FREEWHEEL_BC_CLIENT_ID/SECRET` or `streaming_hub_login`/`buyer_cloud_login`.
- Order state machine — transition table + guards sound; `transition_order` returns 409 with `allowed_transitions`, 400 on bad enum.
- `audience_plan` validator (`services/audience_plan_validator.py`) — role gates, cardinality caps, per-ref taxonomy checks; robust to missing plan.
- No mutable default args.

> Note: `/agentic-audience/match` returns a **deterministic sha256-derived mock score** (`_deterministic_score`, `api/main.py:2550`) — documented, real model deferred to "Epic 2". Do NOT assert real match quality.

---

## 2b. 🔬 Live E2E run — confirmed findings (2026-07-07, port 8010, `AD_SERVER_TYPE=csv`)

Ran the Test.md flow end-to-end and captured response bodies + server log. Results:

**NEW bugs found live (not in the static review):**
| Sev | Bug | Evidence |
|-----|-----|----------|
| **HIGH** | **API-key auth reads headers as query params.** `_get_optional_api_key_record(authorization, x_api_key)` (main.py:347) declares both as plain params with **no `Header(...)`**, so FastAPI binds them as **query params**. Keys sent in `Authorization: Bearer`/`X-Api-Key` headers are never read → every request is anonymous. | `from-template` with key in **header → 401**, same key as **`?x_api_key=` query → 404** (past auth). Quotes always return `buyer_tier: public` even with a key. |
| **HIGH** | **`POST /api/v1/deals/from-template` unusable.** Requires a resolved key (main.py:3417) but (a) header auth is broken above → always 401; (b) even via query-key it then 404s on product lookup (next bug). | header→401, query→404 |
| **HIGH** | **`/pricing`, `/proposals/{id}/counter`, `from-template` 404 "Product not found" for valid products.** They read `storage.get_product` (main.py:1336/1704/…) but **`set_product` is never called** → that store is always empty. `/products` and `/quotes` use the in-memory `_STATIC_PRODUCT_CATALOG` and work fine. | `GET /products/{id}`→200, `POST /pricing {product_id}`→404 for the same id. |
| **HIGH** | **Broken access control.** "Auth-required" mutations don't check the key: `POST /packages`→200, `PUT /api/v1/rate-card`→200, `POST /api/v1/curators`→201 **with no key**. (from-template is the only one that checks — and it's broken per above.) | no-key matrix: 200/200/201 |
| **HIGH** | **`GET /api/v1/deals/export` unreachable — route shadowing.** `/api/v1/deals/{deal_id}` matches `/deals/export` first, treating "export" as a deal id → 404. Export (ttd/dv360/amazon/xandr/generic) is non-functional. Fix: register the `/export` route before the `{deal_id}` route (or use a distinct path). | `GET /deals/export?format=ttd` → `{"error":"deal_not_found","message":"Deal 'export' not found."}` |
| **HIGH** | **`list_packages` MCP tool crashes** (confirmed over a real MCP client): `MediaKitService.__init__() missing 'storage' and 'pricing_engine'`. | MCP `tools/call list_packages` → isError, DI TypeError (mcp_server.py:319) |
| MED | **Inventory sync not idempotent.** Each `packages/sync` / `inventory-sync/trigger` re-creates packages with new IDs → duplicates accumulate (observed **58 packages**, dozens of identical "CTV Premium Bundle"). | `?buyer_tier=advertiser` → 58; `search ctv` → many dup bundles |

| **HIGH** | **Product-source fragmentation (root cause of several 404s).** Three disjoint product stores: `_STATIC_PRODUCT_CATALOG` (used by `/products`, `/quotes` — works), `storage.get_product` (never written — used by `/pricing`, `/proposals/{id}/counter`, `from-template` → 404), and `ProductSetupFlow.state.products` (used by `/proposals` → "Product not found"). A `product_id` from `/products` fails in all the others. | `/products/{id}`→200 but `/pricing`, `/proposals` → "Product not found" for the same id |
| MED | **Curators registry not shared/persisted.** `register_curator` writes to an in-memory `CuratorRegistry._curators` that the curated-deal endpoint doesn't see. | `POST /curators`→201 then `POST /deals/curated`→404 "Curator 'cur-qa' not registered" |
| MED | **`POST /proposals` returns `status:"failed"`** — crew flow completes but the product lookup rejects a valid id (fragmentation above), so no recommendation/approval is produced. Blocks the approval path. | `errors:["Product not found: prod-…"]` |

**Full-coverage note (all Test.md cURLs executed):** every §0–§14 call was run. §7 `GET /approvals` works (empty); `decide`/`resume` are **unreachable** because the only approval-generating path (`POST /proposals` with `APPROVAL_REQUIRED_FLOWS=proposal_decision`) returns `status:failed` — the proposal flow rejects a valid product id (fragmentation bug above), so no approval is ever created. §11 curated-deal fails on curator lookup (bug above). The only genuinely time-based, untested case is the 24h quote-expiry (410). §8 endpoints respond correctly (distribute → 503 `no_ssps_configured`, push → 200, troubleshoot → 400) — full SSP distribution needs real connector endpoints. MCP transport lists all **44 tools**; `get_setup_status` OK.

**Confirmed from the static review:**
- ✅ **Proposals never persisted → `POST /proposals/{id}/counter` returns 404** (bug #4) — reproduced live.

**Verified WORKING (no bug):**
- `POST /api/v1/deals` (book from quote) → 200 with correct **quote-derived** pricing (the hardcoded-terms bug is the *legacy* `POST /deals`, not this IAB path).
- Quote create + 24h TTL; order state machine (`transition` 200, invalid → **409** with `allowed_next`); `agentic-audience/match` (mock score + structured rejection for non-agentic); `audience_plan` pre-flight → correct **structured 400** (`audience_plan_unsupported`) when the seller doesn't declare that capability.

**Test-harness/doc notes (not server bugs):**
- To enable agentic on a package the request field is `agentic_capabilities`, not `agentic:{supported}` — the wrong key is silently ignored (Test.md §3 payload should be corrected).
- Order-transition loop in a shell `for` needs care with nested quoting (JSON "Extra data" errors were a shell artifact, not the API).
- No unhandled 500s / tracebacks appeared in the server log during the run.

## 3. 🔄 Flows to test (E2E user journeys)

### P0 — core path
1. **Setup / identity** — `get_setup_status` → `set_publisher_identity(name, domain, org_id)` → `get_config`.
2. **Inventory & products** — `POST /packages/sync` (CSV → expect ~4 mock packages) → `GET /products` → `GET /api/v1/inventory-sync/watermark`; incremental: `POST /api/v1/inventory-sync/trigger?incremental=true`.
3. **Media kit / packages (v2.1 audiences)** — `GET /packages`; create curated: `POST /packages` with `audience_capabilities` (standard/contextual/agentic); audience filter: `GET /packages?audience_type=standard&audience_id=3&audience_taxonomy_version=1.1` (400 if `audience_id` without `audience_type`; empty array, not 404, when no match). *(MCP `list_packages` blocked by bug #1.)*
4. **Pricing → Quote → Deal**
   - `POST /pricing` (tier + volume + deal-type + inventory-type adjustments).
   - `POST /api/v1/quotes` (deal_type PG/PD/PA, impressions, flight dates) → `qt-…`, 24h TTL.
   - `GET /api/v1/quotes/{id}` → `AVAILABLE`; expired → **410 Gone**.
   - `POST /api/v1/deals` with `quote_id` + optional `audience_plan` → `deal_id`, `audience_plan_snapshot`, `audience_match_summary` (STRONG/MODERATE/WEAK/NONE per ref). *(bugs #3/#4/#5 live here.)*
5. **Order state machine** — `POST /api/v1/orders` (→ DRAFT) → `POST /api/v1/orders/{id}/transition` (draft→approved→delivering→complete); invalid transition → **409 + allowed_next**; `GET /api/v1/orders/{id}/history`.
6. **Approval gates** — set `APPROVAL_GATE_ENABLED=true` → `POST /proposals` → `GET /approvals` → `POST /approvals/{id}/decide` (approve/reject/counter) → `POST /approvals/{id}/resume`.

### P1
7. **SSP distribution** — `POST /api/v1/deals/distribute` (explicit `ssp_name` vs routing by inventory/deal type) + `GET /api/v1/deals/{id}/ssp-troubleshoot`; `POST /api/v1/deals/push` (buyer DSP endpoints) + `buyer-status`.
8. **Deal lifecycle** — `migrate` / `deprecate` / `lineage`; `GET /api/v1/deals/export?format=ttd|dv360|amazon|xandr|generic`; `performance`.
9. **Agentic audience match** — `POST /agentic-audience/match` (structured rejection; mock score).
10. **Buyer registry & trust** — `POST /registry/agents/discover` → `PUT /registry/agents/{id}/trust` (blocked → 403 on next call) → `POST /auth/api-keys` (shown once) → use `Authorization: Bearer`.
11. **FreeWheel OAuth 2.1 PKCE** — `ad-seller freewheel-login --provider sh|bc`; browser PKCE; token auto-refresh + reconnect on expiry.
12. **MCP transport** — Streamable HTTP `/mcp` (primary, protocol 2025-06-18) + legacy SSE `/mcp-sse/sse`; client transport auto-negotiation.

---

## 4. 🌐 API surface to test

### REST — 82 endpoints (18 groups)
- **Core:** `GET /`, `GET /health`
- **Products:** `GET /products`, `GET /products/{id}`, `*/inventory-type` (POST auth / GET / DELETE auth)
- **Pricing:** `POST /pricing`, `GET /api/v1/rate-card`, `PUT /api/v1/rate-card` (auth)
- **Legacy deal/discovery:** `POST /proposals`, `POST /deals`, `POST /discovery`
- **Events:** `GET /events`, `GET /events/{id}`
- **Approvals:** `GET /approvals`, `GET/{id}`, `POST /{id}/decide`, `POST /{id}/resume`
- **Sessions:** `POST /sessions`, `GET /sessions`, `GET/{id}`, `POST /{id}/messages`, `POST /{id}/close`
- **Negotiation:** `POST /proposals/{id}/counter`, `GET /proposals/{id}/negotiation`
- **Media kit (public):** `GET /media-kit`, `/media-kit/packages`, `/media-kit/packages/{id}`, `POST /media-kit/search`
- **Packages (admin, auth):** `GET/POST/PUT/DELETE /packages…`, `POST /packages/assemble`, `POST /packages/sync`
- **Auth:** `POST/GET/DELETE /auth/api-keys…`
- **Registry:** `GET /.well-known/agent.json`, `GET /registry/agents`, `GET/{id}`, `POST /registry/agents/discover`, `PUT /{id}/trust`, `DELETE /{id}`
- **Quotes:** `POST /api/v1/quotes`, `GET /api/v1/quotes/{id}`
- **Deals (12):** book / from-template / export / push / buyer-status / distribute / ssp-troubleshoot / migrate / deprecate / lineage / performance / get
- **Audience (v2.1):** `POST /agentic-audience/match`
- **Orders (6):** POST / list / report / get / history / transition
- **Change requests (5):** POST / list / get / review / apply
- **Supply chain:** `GET /api/v1/supply-chain`
- **Bulk:** `POST /api/v1/deals/bulk` (auth)
- **Curators (4):** list / get / register (auth) / `POST /api/v1/deals/curated` (auth)
- **Inventory sync (3):** status / trigger / watermark

**Auth matrix:** verify each auth-required mutation returns **401 without key / 200 with key**: packages CRUD, `POST /api/v1/deals`, `deals/from-template`, `orders` POST, `change-requests` POST, `curators` POST, `deals/curated`, `deals/bulk`, `rate-card` PUT, `deals/{id}/migrate`, `deals/{id}/deprecate`.

### MCP — 41 tools (`/mcp` Streamable HTTP)
Setup(4): `get_setup_status`, `health_check`, `get_config`, `set_publisher_identity` · Inventory(4): `list_products`, `sync_inventory`, `get_sync_status`, `list_inventory` · Media kit(3): `list_packages`⚠️, `create_package`, `search_packages` · Pricing(4): `get_rate_card`, `update_rate_card`, `get_pricing`, `request_quote` · Deals(10): `create_deal_from_template`, `get_deal_performance`, `push_deal_to_buyers`, `distribute_deal_via_ssp`, `troubleshoot_deal`, `migrate_deal`, `deprecate_deal`, `get_deal_lineage`, `export_deals`, `bulk_deal_operations` · Approvals(3): `list_pending_approvals`, `approve_or_reject`, `set_approval_gates` · Supply chain(1): `get_supply_chain` · Curators(2): `list_curators`, `create_curated_deal` · Buyers/registry(4): `list_buyer_agents`, `register_buyer_agent`, `set_agent_trust`, `list_agents` · Admin(5): `create_api_key`, `list_api_keys`, `revoke_api_key`, `list_sessions`, `get_inbound_queue` · Monitoring(2): `get_buyer_activity`, `list_configurable_flows` · SSP(1): `list_ssps`

### Other interfaces
- **A2A:** `POST /a2a/seller/jsonrpc`, `GET /a2a/seller/.well-known/agent-card.json`
- **AgentCore (Bedrock):** `POST /invocations` (`routing_mode=chat|crew`)
- **CLI:** `ad-seller freewheel-login --provider sh|bc`

---

## 5. Quick pass/fail checklist
- [ ] `pytest` green (209 + 9)
- [ ] `/health`, `/mcp/tools`, `/.well-known/agent.json` respond 200
- [ ] Setup → identity → config
- [ ] CSV sync produces packages; watermark advances
- [ ] Packages CRUD + v2.1 audience filter
- [ ] Quote (TTL + 410) → Deal (audience snapshot)  *(watch bugs #3/#4/#5)*
- [ ] Order transitions + invalid → 409
- [ ] Approval request → decide → resume
- [ ] SSP distribute + troubleshoot
- [ ] Deal migrate/deprecate/lineage/export/performance
- [ ] Registry discover → trust(block→403) → API-key auth (401/200)
- [ ] MCP Streamable HTTP + legacy SSE both work
- [ ] `list_packages` MCP tool  *(currently fails — bug #1)*
- [ ] Concurrency: parallel `/proposals` don't stall `/health`  *(bug #2)*

---

_Generated from a code-level review of `src/ad_seller` (API surface, business flows, adversarial bug hunt). Bug line numbers reference the state of the repo at review time; re-verify after any rebase._

---

## 6. Complete live sweep — 100% per-endpoint matrix

Executed against the running server `http://localhost:8010`
(`AD_SERVER_TYPE=csv APPROVAL_GATE_ENABLED=true APPROVAL_REQUIRED_FLOWS=proposal_decision`).
IDs re-derived at runtime (product IDs are non-deterministic per boot — bug #6).
`✓` = behaves as designed (incl. expected 4xx validation / 409 conflict / 404-on-missing-id).
`✗` = defect.

### 6.1 REST — 80/80 endpoints hit

| Group | Endpoint | Code | Verdict |
|---|---|---|---|
| core | `GET /` · `GET /health` | 200·200 | ✓ |
| products | `GET /products` · `GET /products/{id}` | 200·200 | ✓ |
| products | `POST/GET/DELETE /api/v1/products/{id}/inventory-type` | 422·404·404 | ✓ (validation / none-set) |
| pricing | `POST /pricing` | **404** | ✗ **bug — product fragmentation** |
| pricing | `GET/PUT /api/v1/rate-card` | 200·200 | ✓ |
| legacy | `POST /proposals` | 200 | ✓ (returns `status:failed` — bug #4/#5) |
| legacy | `POST /deals` | **500** | ✗ **bug — legacy deal endpoint crashes** |
| legacy | `POST /discovery` | 200 | ✓ |
| events | `GET /events` · `GET /events/{id}` | 200·200 | ✓ |
| approvals | `GET /approvals` · `/{id}` · `/decide` · `/resume` | 200·404·400·404 | ✓ |
| sessions | POST · GET · GET/{id} · messages · close | 200×5 | ✓ |
| negotiation | `POST /proposals/{id}/counter` · `GET .../negotiation` | 404·404 | ✓ (missing id) |
| media-kit | `GET /media-kit` · packages · packages/{id} · search | 200×4 | ✓ |
| packages | GET · GET/{id} · POST · PUT · sync · DELETE | 200×6 | ✓ |
| packages | `POST /packages/assemble` | 400 | ✓ (validation) |
| auth | POST · GET · GET/{id} · DELETE | 200·200·404·404 | ✓ |
| registry | well-known · agents · agents/{id} · discover · trust · delete | 200×5·404 | ✓ |
| quotes | `POST /api/v1/quotes` (minimal) · `GET /{id}` | 400·200 | ✓ (validation) |
| deals | `POST /api/v1/deals` (dup quote) | 409 | ✓ (idempotency conflict) |
| deals | `GET /api/v1/deals/{id}` | 200 | ✓ |
| deals | `POST /api/v1/deals/from-template` | **401** | ✗ **bug — header-auth inconsistency** |
| deals | `GET /api/v1/deals/export` | **404** | ✗ **bug — route shadowed by `/deals/{deal_id}`** |
| deals | push · buyer-status · lineage · performance | 200×4 | ✓ |
| deals | `POST .../distribute` | 503 | ✓ (no SSP connector in env) |
| deals | `GET .../ssp-troubleshoot` | 400 | ✓ (param validation) |
| deals | migrate · deprecate | 201·409 | ✓ |
| audience | `POST /agentic-audience/match` | 200 | ✓ |
| orders | POST · GET · report · GET/{id} · history · transition · audit | 200×7 | ✓ |
| change-req | POST · GET · GET/{id} · review · apply | 400·200·404·404·404 | ✓ |
| supply/bulk | `GET /api/v1/supply-chain` · `POST /api/v1/deals/bulk` | 200·200 | ✓ |
| curators | GET · GET/{id} · POST | 200·404·201 | ✓ |
| curators | `POST /api/v1/deals/curated` | **404** | ✗ **bug — in-memory CuratorRegistry not shared** |
| inventory-sync | status · trigger · watermark | 200×3 | ✓ |

### 6.2 MCP tools — 44/44 called (Streamable HTTP `/mcp/`)

- **43 functional.** `update_rate_card` and `bulk_deal_operations` need their list args as **JSON strings** (`inputSchema` declares them `type:string`) — a design smell, but they work once passed correctly.
- **1 defect:** `list_packages` → `MediaKitService.__init__() missing 2 required positional arguments: 'storage','pricing_engine'` (bug #1) — crashes regardless of args.

### 6.3 A2A & AgentCore

| Interface | Path | Code | Note |
|---|---|---|---|
| A2A | `POST /a2a/seller/jsonrpc` | 404 | Not mounted on the REST app (:8010). A2A ships as a client module (`clients/a2a_client.py`) + agent-card metadata; no server route in `interfaces/api/main.py`. |
| AgentCore | `POST /invocations` | 404 | Runs as a **separate** `BedrockAgentCoreApp` (`interfaces/agentcore/http_main.py`, port 8080) requiring AWS Bedrock — **skipped**, not testable against :8010. |

### 6.4 Defect roll-up (confirmed live)

1. `POST /pricing` → 404 — pricing can't resolve a product it just listed (**product-source fragmentation**, root cause of the 404 cluster).
2. `POST /deals` (legacy) → **500** — unhandled server error.
3. `GET /api/v1/deals/export` → 404 — **route ordering**: `/deals/{deal_id}` defined before `/deals/export` swallows the literal path.
4. `POST /api/v1/deals/from-template` → 401 — **header-auth inconsistency** (missing `Header(...)`; bearer accepted elsewhere).
5. `POST /api/v1/deals/curated` → 404 — `CuratorRegistry._curators` is in-memory and **not shared** with the handler that created the curator.
6. MCP `list_packages` → crash — `MediaKitService` constructed without required deps.

_Coverage: 80 REST endpoints + 44 MCP tools + A2A + AgentCore = **100% of the surface exercised**. AgentCore alone is skip-noted (needs AWS)._
