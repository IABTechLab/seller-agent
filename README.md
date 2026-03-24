> **V2 — Active Development**
> See [PROGRESS.md](.beads/PROGRESS.md) for roadmap status (93% complete).

# IAB Tech Lab — Seller Agent

An AI-powered inventory management system for **publishers and SSPs** to automate programmatic direct sales using IAB OpenDirect 2.1 standards.

**[Full Documentation →](https://iabtechlab.github.io/seller-agent/)**

## What This Does

- **Manage your seller agent from Claude Desktop** — interactive setup wizard + 45+ MCP tools for day-to-day operations
- **Expose your inventory** via a tiered Media Kit with public and authenticated views
- **Automate deal negotiations** with AI agents that understand your pricing rules
- **Offer tiered pricing** based on buyer identity (public, seat, agency, advertiser)
- **Generate Deal IDs** compatible with any DSP (The Trade Desk, Amazon, DV360, Xandr)
- **Distribute deals through SSPs** — PubMatic (MCP), Index Exchange (REST), Magnite (REST)
- **Push deals to buyers** via IAB Deals API v1.0 standardized push
- **Manage orders** with a full state machine (draft → booked → delivering → complete)
- **Human-in-the-loop** approval gates with configurable guard conditions
- **Connect to ad servers** via a pluggable interface — GAM and FreeWheel supported
- **Support curators** — Agent Range pre-registered, fee-based curation with schain
- **Track deal lineage** — migration, deprecation, and full evolution chain

## Access Methods

The seller agent exposes four communication interfaces:

| Interface | Protocol | Use Case |
|-----------|----------|----------|
| **MCP** | `/mcp/sse` | Primary interface — 45+ tools for Claude Desktop, ChatGPT, and buyer agents |
| **A2A** | `/a2a/{agent}/jsonrpc` | Conversational JSON-RPC 2.0 for natural language queries |
| **REST** | `/api/v1/*` | Programmatic access — 70+ endpoints across 15 groups |
| **Chat** | `/chat` | Web-based conversational interface for human buyers |

> [Protocol Documentation](https://iabtechlab.github.io/seller-agent/api/mcp/)

## Architecture

```
Claude Desktop / ChatGPT ──→ MCP SSE (/mcp/sse) ──┐
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
              ┌──────────────┐              (products, packages,     (16 event types)
              │ GAM    ✅    │               orders, sessions,
              │ FreeWheel ✅ │               deals, curators)
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
pip install -e .

# Configure .env (ad server, SSPs, API key)
cp .env.example .env

# Start
uvicorn ad_seller.interfaces.api.main:app --port 8000
```

> [Developer Setup Guide](https://iabtechlab.github.io/seller-agent/guides/developer-setup/)

### For Publishers (Claude Desktop)

Add the seller agent to Claude Desktop and the setup wizard walks you through everything:

```json
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "seller-agent": {
      "url": "http://localhost:8000/mcp/sse",
      "headers": { "Authorization": "Bearer <your-operator-key>" }
    }
  }
}
```

The wizard guides you through: publisher identity → agent behavior → media kit → pricing → approval gates → buyer registration → curators → launch.

> [Claude Desktop Setup Guide](https://iabtechlab.github.io/seller-agent/guides/claude-desktop-setup/)

## Key Features

### MCP Tools (45+ tools for Claude Desktop / ChatGPT)

| Category | Tools | Examples |
|----------|-------|---------|
| Setup | 4 | `get_setup_status`, `health_check`, `get_config` |
| Inventory | 4 | `list_products`, `sync_inventory`, `list_inventory` |
| Media Kit | 3 | `list_packages`, `create_package`, `search_packages` |
| Pricing | 4 | `get_rate_card`, `update_rate_card`, `get_pricing` |
| Deals | 12 | `create_deal_from_template`, `push_deal_to_buyers`, `distribute_deal_via_ssp`, `migrate_deal`, `deprecate_deal`, `get_deal_lineage` |
| Approvals | 3 | `list_pending_approvals`, `approve_or_reject`, `set_approval_gates` |
| Buyer Agents | 4 | `list_buyer_agents`, `register_buyer_agent`, `set_agent_trust` |
| Curators | 3 | `list_curators`, `create_curated_deal` |
| SSPs | 3 | `list_ssps`, `distribute_deal`, `troubleshoot_deal` |
| Admin | 5 | `create_api_key`, `list_api_keys`, `list_sessions` |

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
| Custom | Pluggable | Implement `AdServerClient` ABC |

FreeWheel publishers can choose `FREEWHEEL_INVENTORY_MODE=deals_only` (default) to only expose pre-configured deals, or `full` for all inventory.

## API Reference

70+ endpoints across 15 groups:

| Group | Endpoints | Description |
|-------|-----------|-------------|
| Media Kit | 4 | Public inventory catalog (no auth) |
| Packages | 7 | Tier-gated package CRUD |
| Products | 5 | Product catalog + inventory type overrides |
| Quotes | 2 | Non-binding price quotes (IAB Deals API) |
| Deal Booking | 12 | Deals, from-template, push, distribute, migrate, deprecate, lineage, export |
| Proposals | 6 | Proposal lifecycle + counter-offers |
| Orders | 8 | Order CRUD + state transitions |
| Change Requests | 5 | Post-deal modification requests |
| Supply Chain | 1 | sellers.json-like self-description |
| Deal Performance | 1 | Delivery metrics |
| Bulk Operations | 1 | Batch deal create/update/cancel |
| Curators | 4 | Curator registration + curated deals |
| Sessions | 4 | Multi-turn session persistence |
| Authentication | 3 | API key management |
| Agent Registry | 5 | Agent trust + discovery |
| Pricing | 3 | Rate card + pricing calculation |
| Inventory Sync | 3 | Scheduler status, trigger, watermark |
| Core | 2 | Health check + root |

> [Full API Reference](https://iabtechlab.github.io/seller-agent/api/overview/)

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
ANTHROPIC_API_KEY=test pytest tests/ -v

# Lint
ruff check src/

# Build docs locally
pip install -e ".[docs]"
mkdocs serve
```

## Related

- [Buyer Agent](https://github.com/IABTechLab/buyer-agent) — DSP/agency/advertiser-side agent
- [Buyer Agent Docs](https://iabtechlab.github.io/buyer-agent/) — Buyer documentation
- [agentic-direct](https://github.com/InteractiveAdvertisingBureau/agentic-direct) — IAB Tech Lab reference implementation

## License

Apache 2.0
