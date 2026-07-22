# Changelog

All notable changes to the IAB Tech Lab Seller Agent are documented here.

## [Unreleased]

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
  `docs/api/openapi.json`.
- Avails endpoint (`POST /products/avails`) adopted the shared avails wire
  contract: request/response models are now
  `iab_agentic_primitives.protocol.AvailsRequest`/`AvailsResponse`
  (re-exported through `ad_seller.interfaces.api.schemas`), the canonical
  home of the contract. Same wire dialect and field set.

### Changed
- Avails responses no longer null-pad valueless optionals: per the shared
  contract policy, `deliveryConfidence` is omitted entirely (this
  reference seller has no forecast data source), `guaranteedImpressions`
  appears only for PG-capable products, and `availableTargeting` is
  omitted when the product declares no targeting dicts. Readers that
  parsed the previous explicit nulls parse the omitted form identically
  under the shared models. Regenerated `docs/api/openapi.json`.
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
- Comprehensive unit tests (86 new tests) and integration tests (38 new tests)
- Troubleshooting guide
- Buyer agent compatibility report

### Changed
- Renamed "Deal Jockey" to "Deal Library" across codebase and documentation
- Linted and formatted entire codebase with ruff
- Removed `FREEWHEEL_BC_CLIENT_ID` and `FREEWHEEL_BC_CLIENT_SECRET` settings (Beeswax uses session cookie auth, not OAuth client_credentials)

### Fixed
- Documentation tool count (41 MCP tools, not "45+")
- Documentation endpoint count (82 REST endpoints, not "70+")
- Port typo in media-kit guide (8001 → 8000)

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
