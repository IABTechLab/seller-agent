# Changelog

All notable changes to the IAB Tech Lab Seller Agent are documented here.

## [Unreleased]

### Added
- MCP Streamable HTTP transport at `/mcp` (current MCP standard, protocol 2025-06-18) — resolves buyer agent 405 errors on MCP connection
- Legacy HTTP+SSE transport kept at `/mcp-sse/sse` for backwards compatibility with older Claude Desktop / ChatGPT clients
- FreeWheel authentication integration (seller-91t):
  - Streaming Hub: OAuth 2.0 ROPCG via `streaming_hub_login` MCP tool (7-day token TTL)
  - Buyer Cloud: Beeswax session cookie via `buyer_cloud_login` MCP tool (100h/30-day TTL)
  - Auto-reconnect on session expiry for both SH and BC
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
