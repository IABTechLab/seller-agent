> **V2 — Feature Complete**

# IAB Tech Lab — Seller Agent

An AI-powered inventory management system for **publishers and SSPs** to automate programmatic deal negotiation, booking, and distribution using IAB Tech Lab standards (OpenDirect 2.1, Deals API v1.0, sellers.json).

**[Full Documentation →](https://iabtechlab.github.io/seller-agent/)**

## What This Does

- **Manage your seller agent from Claude** (desktop or web) — interactive setup wizard + 46 MCP tools for day-to-day operations
- **Expose your inventory** via a tiered Media Kit with public and authenticated views
- **Automate deal negotiations** with AI agents that understand your pricing rules
- **Offer tiered pricing** based on buyer identity (public, seat, agency, advertiser)
- **Generate Deal IDs** compatible with any DSP (The Trade Desk, Amazon, DV360, Xandr)
- **Distribute deals through SSPs** — PubMatic (MCP), Index Exchange (REST), Magnite (REST)
- **Push deals to buyers** via IAB Deals API v1.0 standardized push
- **Manage orders** with a full state machine (draft → booked → delivering → complete)
- **Human-in-the-loop** approval gates with configurable guard conditions
- **Connect to ad servers** via a pluggable interface — GAM, FreeWheel, and CSV (demo/testing) supported
- **Authenticate with FreeWheel** — browser-based OAuth 2.1 PKCE for SH + Buyer Cloud, with refresh-token auto-reconnect
- **Support curators** — Agent Range pre-registered, fee-based curation with schain
- **Track deal lineage** — migration, deprecation, and full evolution chain
- **Supply chain transparency** — sellers.json parsing with OpenRTB schain in deal responses

## Access Methods

The seller agent exposes four communication interfaces:

| Interface | Protocol | Use Case |
|-----------|----------|----------|
| **MCP** | `/mcp` (Streamable HTTP), `/mcp-sse/sse` (legacy) | Primary interface — 46 tools for Claude, ChatGPT, Codex, Cursor, and buyer agents |
| **A2A** | `/a2a/{agent}/jsonrpc` | Conversational JSON-RPC 2.0 for natural language queries |
| **REST** | `/api/v1/*` | Programmatic access — 87 endpoints across 25 groups |
| **Chat** | `/chat` | Web-based conversational interface for human buyers |

> [Protocol Documentation](https://iabtechlab.github.io/seller-agent/api/mcp/)

## Architecture

```
Claude / ChatGPT / Codex ──→ MCP /mcp (Streamable HTTP) ──┐
Buyer Agents ──→ A2A / REST ───────────────────────┤
                                                    ▼
                                              FastAPI App
                                                    │
                    ┌───────────────────────────────┼──────────────────────┐
                    ▼                               ▼                      ▼
              CrewAI Agents                   Media Kit Service      Pricing Engine
              (3-level hierarchy)             (Tier-gated catalog)   (4-tier + rate card)
                    │                               │                      │
                    ▼                               ▼                      ▼
              Ad Server Layer               Storage (SQLite/PG)      Event Bus
              ┌──────────────┐              (products, packages,     (22 event types)
              │ GAM    ✅    │               orders, sessions,
              │ FreeWheel ✅ │               deals, curators)
              │ CSV    ✅    │
              │ Your Server* │
              └──────────────┘
              * Pluggable via AdServerClient
                    │
              SSP Connectors
              ┌──────────────────┐
              │ PubMatic (MCP) ✅│
              │ Index Exchange ✅│
              │ Magnite (REST) ✅│
              │ Your SSP*       │
              └──────────────────┘
              * Pluggable via SSPClient
                    │
              Deal Sync
              ┌──────────────────┐
              │ deals-api-mcp ✅ │
              │ Your service*    │
              └──────────────────┘
              * Pluggable via DealSyncClient
```

### Agent Hierarchy

| Level | Agent | Role |
|-------|-------|------|
| **1** | Inventory Manager (Opus) | Strategic orchestration, yield optimization |
| **2** | Channel Specialists (Sonnet) | Display, Video, CTV, Mobile App, Native, Linear TV |
| **3** | Functional Agents (Sonnet) | Pricing, Availability, Proposal Review, Upsell, Audience |

> [Architecture Documentation](https://iabtechlab.github.io/seller-agent/architecture/overview/)

## Getting Started — Two-Phase Setup

### For Developers (Claude Code / Terminal)

Deploy the server, connect ad servers and SSPs, generate operator credentials:

```bash
git clone https://github.com/IABTechLab/seller-agent.git
cd seller-agent
uv sync --locked        # installs into .venv from uv.lock (same install CI uses)

# Configure .env (ad server, SSPs, API key)
cp .env.example .env

# Start
uv run uvicorn ad_seller.interfaces.api.main:app --port 8000
```

No [uv](https://docs.astral.sh/uv/)? `pip install -e .` in a virtualenv of your
choice still works (then run `uvicorn` without the `uv run` prefix) — but
`uv sync --locked` is what CI runs, so it is the reproducible path.

> [Developer Setup Guide](https://iabtechlab.github.io/seller-agent/guides/developer-setup/)

### For Publishers (Claude Desktop / Web / ChatGPT)

Add the seller agent to Claude (desktop or web) and the setup wizard walks you through everything:

```json
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "seller-agent": {
      "url": "http://localhost:8000/mcp/",
      "headers": { "Authorization": "Bearer <your-operator-key>" }
    }
  }
}
```

The wizard guides you through: publisher identity → agent behavior → media kit → pricing → approval gates → buyer registration → curators → launch.

> [Claude Setup Guide](https://iabtechlab.github.io/seller-agent/guides/claude-desktop-setup/)

## Key Features

### MCP Tools (46 tools for Claude / ChatGPT / Codex / Cursor)

| Category | Tools | Examples |
|----------|-------|---------|
| Setup | 4 | `get_setup_status`, `health_check`, `get_config`, `set_publisher_identity` |
| Inventory | 4 | `list_products`, `sync_inventory`, `list_inventory`, `get_sync_status` |
| Media Kit | 2 | `list_packages`, `create_package` |
| Pricing & Quotes | 4 | `get_rate_card`, `update_rate_card`, `get_pricing`, `request_quote` |
| Deals | 9 | `create_deal_from_template`, `push_deal_to_buyers`, `migrate_deal`, `deprecate_deal`, `get_deal_lineage`, `export_deals`, `bulk_deal_operations` |
| Orders & Reporting | 4 | `list_orders`, `transition_order`, `list_gam_orders`, `get_gam_delivery_report` |
| Approvals | 3 | `list_pending_approvals`, `approve_or_reject`, `set_approval_gates` |
| Buyer Agents | 4 | `list_buyer_agents`, `register_buyer_agent`, `set_agent_trust`, `list_agents` |
| Curators | 2 | `list_curators`, `create_curated_deal` |
| SSPs | 3 | `list_ssps`, `distribute_deal_via_ssp`, `troubleshoot_deal` |
| Admin & Sessions | 4 | `create_api_key`, `list_api_keys`, `revoke_api_key`, `list_sessions` |
| Composite | 3 | `get_inbound_queue`, `get_buyer_activity`, `list_configurable_flows` |

Full inventory: [MCP Tools reference](https://iabtechlab.github.io/seller-agent/reference/mcp-tools/).

### Deal Distribution (3 paths)

| Path | How | Use Case |
|------|-----|----------|
| **Direct to buyer** | `POST /api/v1/deals/push` | IAB Deals API v1.0 HTTP push |
| **Through ad server** | FreeWheel `book_deal()` or GAM | Exchange-level deal activation |
| **Through SSP** | `POST /api/v1/deals/distribute` | PubMatic, Index Exchange, Magnite |

### Tiered Pricing

| Tier | Discount | Negotiation | Volume Discounts |
|------|----------|:-----------:|:----------------:|
| **Public** | 0% (range only) | — | — |
| **Seat** | 5% | — | — |
| **Agency** | 10% | Yes | — |
| **Advertiser** | 15% | Yes | Yes |

> [Pricing & Access Tiers](https://iabtechlab.github.io/seller-agent/guides/pricing-rules/)

### Curator Support

Curators package and curate inventory on behalf of buyers. Agent Range is pre-registered to curate deals.

- `POST /api/v1/deals/curated` — create deals with curator overlay (base CPM + curator fee)
- Curator appears as a node in the deal's schain
- `GET /api/v1/curators` — list registered curators

### Scheduled Inventory Sync

```env
INVENTORY_SYNC_ENABLED=true
INVENTORY_SYNC_INTERVAL_MINUTES=60
```

Plus manual trigger, incremental sync with watermarks, and inventory type overrides.

### SSP Connectors

```env
SSP_CONNECTORS=pubmatic,index_exchange
SSP_ROUTING_RULES=ctv:pubmatic,display:index_exchange
PUBMATIC_MCP_URL=https://mcp.pubmatic.com/sses
INDEX_EXCHANGE_API_URL=https://api.indexexchange.com
```

### Ad Server Support

| Ad Server | Status | Config |
|-----------|--------|--------|
| Google Ad Manager | ✅ Supported | `AD_SERVER_TYPE=google_ad_manager` |
| FreeWheel (Streaming Hub + Buyer Cloud) | ✅ Supported | `AD_SERVER_TYPE=freewheel` |
| CSV (testing/demo) | ✅ Supported | `AD_SERVER_TYPE=csv` |
| Custom | Pluggable | Implement `AdServerClient` ABC |

**FreeWheel authentication:**
- **Streaming Hub:** OAuth 2.1 PKCE bootstrap via `ad-seller freewheel-login --provider sh`, then bearer auth to `/mcp/oauth`
- **Buyer Cloud:** OAuth 2.1 PKCE bootstrap via `ad-seller freewheel-login --provider bc`, then bearer auth to `/mcp/oauth`
- Auto-refresh and reconnect on access-token expiry (re-run bootstrap only when refresh is invalid/expired)
- Inventory mode: `FREEWHEEL_INVENTORY_MODE=deals_only` (default) exposes only pre-configured deals, or `full` for all inventory

**CSV adapter:** Full CRUD with atomic writes and file locking — use for testing and demos without an ad server. Sample data included for CTV streaming and web display.

## API Reference

87 endpoints across 25 groups:

| Group | Endpoints | Description |
|-------|-----------|-------------|
| Media Kit | 4 | Public inventory catalog (no auth) |
| Packages | 7 | Tier-gated package CRUD, assembly, and sync |
| Products | 6 | Product catalog, avails, inventory type overrides |
| Quotes | 2 | Non-binding price quotes (IAB Deals API) |
| Deal Booking | 11 | Deals, from-template, push, distribute, migrate, deprecate, lineage, export |
| Deals | 1 | Deal creation from accepted proposals |
| Deal Performance | 1 | Delivery metrics |
| Bulk Operations | 1 | Batch deal create/update/cancel |
| Proposals | 1 | Proposal submission (quote-anchored price verification) |
| Negotiation | 3 | Counter-offers + negotiation state and messages |
| Discovery | 1 | Natural-language inventory discovery |
| Audience | 1 | Agentic audience matching |
| Orders | 6 | Order CRUD, history, transitions, reporting |
| Audit | 1 | Order audit trail |
| Change Requests | 5 | Post-deal modification requests |
| Approvals | 4 | Human-in-the-loop approval decisions |
| Supply Chain | 1 | sellers.json-like self-description |
| Curators | 4 | Curator registration + curated deals |
| Sessions | 5 | Multi-turn session persistence |
| Authentication | 4 | API key management |
| Agent Registry | 6 | Agent card, trust + discovery |
| Pricing | 3 | Rate card + pricing calculation |
| Events | 2 | Event log queries |
| Reporting | 2 | GAM orders + delivery reports |
| Core | 5 | Health check, root, inventory-sync status/trigger/watermark |

Full inventory: [REST Endpoints reference](https://iabtechlab.github.io/seller-agent/reference/endpoints/).

> [Full API Reference](https://iabtechlab.github.io/seller-agent/api/overview/)

## Development

CI installs straight from `uv.lock` (`uv sync --locked`) and never re-resolves,
so use the same locked commands locally:

```bash
# Install dev dependencies (the exact install CI uses)
uv sync --locked --extra dev

# Run tests
ANTHROPIC_API_KEY=test uv run --locked pytest tests/ -v

# Lint + format (CI enforces both)
uv run --locked ruff check src/
uv run --locked ruff format --check src/ tests/

# Build docs locally
uv sync --locked --extra docs
uv run --locked mkdocs serve
```

Changing dependencies in `pyproject.toml`? Re-resolve the lockfile and commit it
alongside your change — otherwise CI fails with a stale-lockfile error:

```bash
uv lock
```

> **Note:** tags `v2.3.3` and earlier ship a `uv.lock` that is stale relative to
> `pyproject.toml`, so `uv sync --locked` fails on those checkouts. Fixed as of
> `v2.4.0` — use `uv sync` (unlocked) if you need to build an older tag.

## Related

- [Buyer Agent](https://github.com/IABTechLab/buyer-agent) — DSP/agency/advertiser-side agent
- [Buyer Agent Docs](https://iabtechlab.github.io/buyer-agent/) — Buyer documentation
- [agentic-direct](https://github.com/InteractiveAdvertisingBureau/agentic-direct) — IAB Tech Lab reference implementation

## License

Apache 2.0
