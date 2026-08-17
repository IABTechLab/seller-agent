# Changelog

All notable changes to the IAB Tech Lab Seller Agent are documented here.

## [Unreleased]

## [2.4.2] — 2026-08-17

### Added

- Operator key CLI management (#59): `ad-seller list-operator-keys` lists
  operator keys (metadata only, secrets are never re-shown; pass
  `--include-inactive` to include revoked/expired keys) and
  `ad-seller delete-operator-key --label "..."` (or `--key-id ...`)
  revokes an operator key out-of-band, freeing the label for reuse.
  Creating an operator key now rejects a label already in use by an
  active operator key. The `ad-seller` console script entry point moved
  from `ad_seller.interfaces.cli.main:app` to `:main`; editable installs
  must re-run `pip install -e .` to regenerate the script.
- CI now builds the Docker image on every PR (new docker-build job,
  mirroring buyer-agent), so Dockerfile breakage can no longer land
  silently (issue #3).

### Fixed

- Pricing: a higher-priority discount-only rule no longer stacks its
  discount onto a lower-priority rule's `base_price_override`, and the
  stale discount no longer leaks into the pricing rationale text that
  flows into quotes and negotiation responses (#54).
- Curated deals: a supplied `product_id` that is not in the catalog now
  returns 404 `product_not_found` instead of silently booking a
  confirmed deal at the generic $12 CPM default; the default now applies
  only when no `product_id` is given at all (#58).
- Deal migration: migrating an already-migrated deal now returns 409
  with the existing `replacement_deal_id` instead of silently creating a
  second replacement and orphaning the first from the lineage chain
  (#56).
- FD-12 idempotency is now enforced on `POST /api/v1/quotes`: replaying
  a request with the same `idempotency_key` and payload returns the
  original quote, and reusing a key with a different payload returns 409
  `idempotency_conflict` via a payload hash. `POST /api/v1/deals` gains
  the same conflict detection and still replays legacy pre-hash records
  (#50). Behavior note: idempotency keys are now scoped per buyer
  (verified buyer context on quotes, resolved identity on deals), so two
  buyers reusing the same client-chosen key no longer collide, and buyer
  verification on quotes runs before the replay lookup.
- Idempotency is now also enforced on
  `POST /api/v1/negotiations/messages`: a retried message replays the
  stored response instead of consuming an extra negotiation round, and a
  reused key with a different payload returns 409 (#52). The same
  per-buyer key scoping applies.
- `deploy.sh` smoke check now detects chat routing fallthrough: the
  hardcoded chat-mode invocations fail the check if the response type is
  `general`, instead of passing on any error-free response (#49).
- Chat-mode router: "list products" now reaches the availability handler
  and "create a deal" reaches the deal handler instead of falling
  through to the general fallback (#47).
- `deploy.sh` no longer hardcodes the US-scoped
  `bedrock/us.amazon.nova-lite-v1:0` as `MEMORY_LLM_MODEL`; memory now
  inherits `DEFAULT_LLM_MODEL`, so non-US deployments get a region-valid
  model (#48).
- `infra/docker/Dockerfile` builds again: git is installed in the
  builder stage, the silently-failing "cache-friendly" dependency layer
  is removed, and the runtime stage re-points the editable install with
  `--no-deps` instead of re-resolving all dependencies (issue #3).

### Changed

- `iab-agentic-primitives` pinned to v0.5.1, picking up the fix that
  single-sources `__version__` from package metadata.

### Docs

- The pricing guide documents the quote-then-propose sequence required
  for `pricing_verified` (there is deliberately no rate-card or floor
  fallback), cross-referenced from the API overview and the README
  endpoint table (#43).

## [2.4.1] — 2026-08-05

### Changed

- `CREW_MEMORY_ENABLED` now defaults to `false`; when enabled without a
  configured embedder, memory is disabled with a single startup warning
  instead of failing on every call (issue #42).
- Embedder options documented in `.env.example` and the configuration
  guide.

### Docs

- README documents the locked uv workflow.

## [2.4.0] — 2026-08-04

### BREAKING: admin surface now requires operator credentials

API keys now carry a role (`buyer` or `operator`), and every
seller-side admin/mutation surface requires an operator-role key.
Requests without a key get `401`; requests with a buyer-role key get
`403`. Buyer-facing paths (discovery, products, avails, pricing,
quotes, proposals, negotiation, deal booking reads, order/CR reads,
change-request submission) are unchanged.

Operator-gated REST endpoints:

- API key lifecycle: `POST/GET /auth/api-keys`,
  `GET/DELETE /auth/api-keys/{key_id}`, `POST /auth/api-keys/operator`
- Event log: `GET /events`, `GET /events/{event_id}`
- Rate card writes: `PUT /api/v1/rate-card` (reads stay public)
- Agent registry mutations: `PUT /registry/agents/{agent_id}/trust`,
  `DELETE /registry/agents/{agent_id}` (reads stay public)
- Package mutations: `POST /packages`, `PUT/DELETE /packages/{package_id}`,
  `POST /packages/assemble`, `POST /packages/sync`
- Inventory sync trigger: `POST /api/v1/inventory-sync/trigger`
- Deal push/distribution and curator registration:
  `POST /api/v1/deals/push`, `POST /api/v1/deals/distribute`,
  `POST /api/v1/curators`
- Inventory-type overrides:
  `POST/DELETE /api/v1/products/{product_id}/inventory-type`
  (`GET` stays on the buyer-facing surface)
- Change-request decisions: `POST /api/v1/change-requests/{cr_id}/review`,
  `POST /api/v1/change-requests/{cr_id}/apply` (submission and reads
  stay buyer-facing)
- Order state transitions: `POST /api/v1/orders/{order_id}/transition`
  (order reads/audit stay buyer-facing)

The 19 admin MCP tools are gated the same way over HTTP transports
(Streamable HTTP `/mcp` and legacy SSE): `set_publisher_identity`,
`sync_inventory`, `create_package`, `update_rate_card`,
`list_gam_orders`, `get_gam_delivery_report`, `push_deal_to_buyers`,
`distribute_deal_via_ssp`, `transition_order`, `approve_or_reject`,
`set_approval_gates`, `register_buyer_agent`, `set_agent_trust`,
`create_api_key`, `list_api_keys`, `revoke_api_key`, `list_sessions`,
`get_inbound_queue`, `get_buyer_activity`. Local stdio MCP access
(no HTTP request context) remains trusted, same model as the CLI.

Migration:

1. Mint the first operator key out-of-band on the server host with
   `ad-seller create-operator-key`, running with the SAME storage
   configuration as the server (it writes the key record directly to
   storage — no network surface).
2. Send that key on admin calls as `Authorization: Bearer <key>` or
   `X-Api-Key: <key>` (for MCP over HTTP, e.g.
   `mcp-remote --header "Authorization: Bearer <key>"`). Additional
   operator keys can then be minted via `POST /auth/api-keys/operator`.
3. Existing (legacy) API keys deserialize with the default `buyer`
   role — they keep working on buyer-facing paths but are never
   silently promoted to operator.

### BREAKING: failed proposals report structured errors[]

Failed proposals now report a structured `errors[]` array of
`{stage, code, detail}` entries with stable snake_case `code` values
(`missing_required_fields`, `product_not_found`, `audience_validation`,
`pricing`, `availability`, `crew_evaluation_error`, `internal`).
Branch on `code`, not `detail` — the detail text is free-form and may
change. See the stage/code table in `docs/guides/troubleshooting.md`.

### Fixed

- `temperature` is omitted from LLM requests for models whose API
  rejects the parameter (Opus 4.7+, Sonnet 5+, Fable, Mythos), instead
  of failing with a 400 `invalid_request_error`.
- `ANTHROPIC_API_KEY` is now optional at startup: the server boots and
  all deterministic (non-LLM) surfaces work without it. LLM-backed
  flows check the key lazily at LLM construction time and fail with a
  clear, actionable `MissingApiKeyError` instead of a cryptic provider
  error mid-request.
- CI installs are locked (`uv sync --locked` / `uv run --locked`), so
  builds are reproducible against the committed `uv.lock`.

### Known issues

- Releases <= 2.3.3 shipped a stale `uv.lock` (locked
  `iab-agentic-primitives` v0.3.0 against a required v0.5.0), so locked
  installs on those tags fail. Use v2.4.0, or install via pip on the
  older tags.

## [2.3.3] — 2026-07-29

### Fixed
- Replaced the retired manager-LLM default: Anthropic retired
  `claude-opus-4-20250514` on June 15, 2026, so fresh installs using an
  Anthropic key failed with `model_not_found`. `MANAGER_LLM_MODEL` now
  defaults to `claude-opus-4-8` (Bedrock inference profile
  `us.anthropic.claude-opus-4-8`) across settings, `.env.example`, and
  the configuration guide. (#35)
- Ported the surgical toolResult stripping from buyer-agent, restoring
  patch parity between the two agents. (#36)
- Default-catalog product IDs are now derived deterministically
  (`uuid5` of the product name) instead of minted randomly on every
  catalog build, so product IDs are stable across restarts and cache
  resets. The reset-ids test was updated to the deterministic-ID
  contract. (#37, follow-up to issue #34)

## [2.3.2] — 2026-07-28

### Added
- deals-api-mcp deal-sync integration (#32): a new deal-sync connector
  family — `DealSyncClient` ABC and `DealSyncRegistry`
  (`clients/deal_sync_base.py`), a peer of the SSP registry — with
  `DealsAPIMCPClient` pushing negotiated deals to the IAB
  [deals-api-mcp](https://github.com/IABTechLab/deals-api-mcp) server
  (IAB Deal Sync API v1.0) over MCP Streamable HTTP. See
  `docs/integration/deals-api-mcp.md`.

## [2.3.1] — 2026-07-22

### Fixed
- Agent card (`/.well-known/agent.json`) advertises only the protocols
  the server actually serves; A2A documentation marked
  designed-not-implemented.

## [2.3.0] — 2026-07-22

### Added
- OpenDirect 2.1 spec dialect on `POST /products/avails` (dialect
  convergence, shared avails contract): the published
  `ProductAvailsSearch` request (multi-product `productids` array +
  required `accountid`/`advertiserbrandid`) is now accepted alongside the
  legacy single-product profile, and spec requests are answered with the
  spec `avails` collection envelope of `Avails` records carrying
  `availsstatus` (Available / Partially Available / Unavailable, reason
  `Booked` when capacity caps or exhausts inventory). The response
  dialect follows the request dialect, so legacy round-trips are
  byte-for-byte unchanged. Requested volume/budget arrive on the spec
  dialect as the contract's Investment `producttargeting` entries and
  feed the same honest-availability policy. Regenerated
  `docs/api/openapi.json`. (primitives v0.5.0)

## [2.2.2] — 2026-07-22

### Fixed
- Proposal availability is grounded in `check_avails`; volume
  shortfalls now produce a counter-offer instead of a rejection.
- CSV inventory is included in the product catalog; `update_package`
  applies a field whitelist.

## [2.2.1] — 2026-07-22

### Fixed
- Catalog-aware package resolution and idempotent inventory sync
  (issue #34).

## [2.2.0] — 2026-07-21

### Changed
- Avails endpoint (`POST /products/avails`) adopted the shared avails wire
  contract: request/response models are now
  `iab_agentic_primitives.protocol.AvailsRequest`/`AvailsResponse`
  (re-exported through `ad_seller.interfaces.api.schemas`), the canonical
  home of the contract. Same wire dialect and field set. (primitives
  v0.4.0)
- Avails responses no longer null-pad valueless optionals: per the shared
  contract policy, `deliveryConfidence` is omitted entirely (this
  reference seller has no forecast data source), `guaranteedImpressions`
  appears only for PG-capable products, and `availableTargeting` is
  omitted when the product declares no targeting dicts. Readers that
  parsed the previous explicit nulls parse the omitted form identically
  under the shared models. Regenerated `docs/api/openapi.json`.

### Added
- Leak-prevention guardrails: git hooks and hygiene CI; internal issue
  tracker removed from the public tree.

## [2.1.1] — 2026-07-21

### Changed
- Universal lowball counter: all below-floor offers are countered at
  floor rather than rejected (spec change).
- Bounded proposal-flow latency with a deterministic fallback.

### Added
- Cold-start negotiation surface: proposal persistence, quote-led
  opens, and booking honors `ACCEPTED` proposals.
- Grounded quote availability and enriched catalog metadata.

### Fixed
- Docs CI deploys without installing the app (private dependency); the
  OpenAPI artifact is committed.

## [2.1.0] — 2026-07-20

### Added
- MCP Streamable HTTP transport at `/mcp` (current MCP standard, protocol 2025-06-18) — resolves buyer agent 405 errors on MCP connection
- Legacy HTTP+SSE transport kept at `/mcp-sse/sse` for backwards compatibility with older Claude Desktop / ChatGPT clients
- FreeWheel OAuth 2.1 PKCE authentication integration:
  - Streaming Hub: interactive bootstrap via `ad-seller freewheel-login --provider sh`, then bearer auth to `/mcp/oauth`
  - Buyer Cloud: interactive bootstrap via `ad-seller freewheel-login --provider bc`, then bearer auth to `/mcp/oauth`
  - Legacy SH/BC login-tool credential paths removed (`streaming_hub_login`, `buyer_cloud_login`)
  - Auto-refresh and reconnect on access-token expiry for both SH and BC
  - Connection validation via `reconnect()` method on MCP client
- CSV ad server adapter with full CRUD and atomic writes (61 tests)
- 9 MCP prompts (slash commands) for Claude Desktop/web (/setup, /status, /inventory, /deals, /queue, /new-deal, /configure, /buyers, /help)
- 3 composite tools: get_inbound_queue, get_buyer_activity, list_configurable_flows
- Avails endpoint `POST /products/avails` with honest-availability policy
- Auto-generated tool/endpoint/event inventories (`docs/reference/`) with a CI drift guard
- Tested quickstart: smoke test boots the documented entrypoint and asserts core routes respond
- Server-side trust-tier verification with `VerifiedTrust` persisted on price-moving paths; agent-registry wiring
- Threshold-driven mandatory approval gates; approval endpoints authenticated and stamped with the verified principal
- Comprehensive unit tests (86 new tests) and integration tests (38 new tests)
- Troubleshooting guide
- Buyer agent compatibility report

### Changed
- Service layer extracted: MCP tools, CLI, and chat interface are thin adapters over it; background REST sidecar removed from the AgentCore MCP entrypoint
- Shared iab-agentic-primitives contracts adopted at the Quote, Deal-booking, Negotiation, and Catalog wire edges
- OpenDirect tier-1 wire aliases on the public wire surface
- Two-way main/v2 reconciliation: GAM adapter restored, LLM provider configuration unified
- Renamed "Deal Jockey" to "Deal Library" across codebase and documentation
- Linted and formatted entire codebase with ruff
- Removed `FREEWHEEL_BC_CLIENT_ID` and `FREEWHEEL_BC_CLIENT_SECRET` settings (Beeswax uses session cookie auth, not OAuth client_credentials)

### Fixed
- CPM hallucination: `pricing_type` enum + quote validation (#7)
- Route shadowing: `/api/v1/deals/export` registered before `/{deal_id}`
- Auth header binding in `_get_optional_api_key_record`; audit trail fails closed
- `MANAGER_LLM_MODEL` default: `opus-4-20250514` → `sonnet-4-5-20250929` (#19)
- CrewAI shutdown telemetry hang disabled; agent memory respects `crew_memory_enabled` (#21, #22)
- Shared `Product.ad_formats` populated from `inventory_type`
- Documentation tool count (41 MCP tools, not "45+")
- Documentation endpoint count (82 REST endpoints, not "70+")
- Port typo in media-kit guide (8001 → 8000)

### Removed
- Unused `services/openrtb_parser.py` and `services/setup_wizard.py`; abandoned CrewAI tool subpackages (gam, pricing, proposal, availability, deal_library)

## [2.0.0] — 2026-03-23

### Added
- MCP server with 41 tools for Claude Desktop, ChatGPT, Cursor
- Interactive setup wizard (developer + business phases)
- Deal migration, deprecation, and lineage tracking
- Curator support with Agent Range as day-one curator
- IAB Deals API v1.0 integration
- SSP connector abstraction (PubMatic MCP, Index Exchange REST)
- SSP deal distribution in ExecutionActivationFlow
- FreeWheel Streaming Hub integration (Phases 1-2)
- Order workflow state machine with audit trail
- Enhanced multi-round negotiation engine
- Change request management
- API key authentication with 4 access tiers
- Agent registry integration
- IaC deployment (CloudFormation + Terraform)
- Docker + docker-compose with PostgreSQL + Redis
