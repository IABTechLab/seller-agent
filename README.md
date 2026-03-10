> **V2 — Active Development**
> See [PROGRESS.md](.beads/PROGRESS.md) for roadmap status.

# IAB Tech Lab — Seller Agent

An AI-powered inventory management system for **publishers and SSPs** to automate programmatic direct sales using IAB OpenDirect 2.1 standards.

**[Full Documentation →](https://iabtechlab.github.io/seller-agent/)**

## What This Does

- **Expose your inventory** via a tiered Media Kit with public and authenticated views
- **Automate deal negotiations** with AI agents that understand your pricing rules
- **Offer tiered pricing** based on buyer identity (public, seat, agency, advertiser)
- **Generate Deal IDs** compatible with any DSP (The Trade Desk, Amazon, DV360, etc.)
- **Manage orders** with a full state machine (draft → booked → delivering → complete)
- **Human-in-the-loop** approval gates for operator oversight of deal decisions
- **Connect to ad servers** via a pluggable interface — GAM supported today, FreeWheel in progress, extend for any ad server

## Access Methods

The seller agent exposes three communication interfaces:

| Interface | Protocol | Use Case |
|-----------|----------|----------|
| **MCP** | `/mcp/sse` | Primary agentic interface — structured tool calls for buyer agents |
| **A2A** | `/a2a/{agent}/jsonrpc` | Conversational JSON-RPC 2.0 for natural language queries |
| **REST** | Standard HTTP | Operator/admin interface for setup and monitoring |

→ [Protocol Documentation](https://iabtechlab.github.io/seller-agent/api/mcp/)

## Architecture

```
Buyer Agents ──→ MCP / A2A / REST ──→ FastAPI
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
              CrewAI Agents       Media Kit Service    Pricing Engine
              (3-level hierarchy)  (Tier-gated catalog)  (4-tier pricing)
                    │                    │                    │
                    ▼                    ▼                    ▼
              Ad Server Layer      Storage (SQLite/Redis)   Event Bus
              ┌──────────────┐     (products, packages,     (22 event types)
              │ GAM    ✅    │      orders, sessions)
              │ FreeWheel 🚧 │
              │ Your Server* │
              └──────────────┘
              * Pluggable via AdServerClient interface
```

### Agent Hierarchy

| Level | Agent | Role |
|-------|-------|------|
| **1** | Inventory Manager (Opus) | Strategic orchestration, yield optimization |
| **2** | Channel Specialists (Sonnet) | Display, Video, CTV, Mobile App, Native, Linear TV |
| **3** | Functional Agents (Sonnet) | Pricing, Availability, Proposal Review, Upsell, Audience |

→ [Architecture Documentation](https://iabtechlab.github.io/seller-agent/architecture/overview/)

## Key Features

### Media Kit & Inventory Discovery
Three-layer package system (synced from ad server, publisher-curated, agent-assembled) with tier-gated access — unauthenticated buyers see price ranges, authenticated buyers see exact pricing and placements.

→ [Media Kit Guide](https://iabtechlab.github.io/seller-agent/guides/media-kit/)

### Tiered Pricing

| Tier | Discount | Negotiation | Volume Discounts |
|------|----------|:-----------:|:----------------:|
| **Public** | 0% (range only) | — | — |
| **Seat** | 5% | — | — |
| **Agency** | 10% | Yes | — |
| **Advertiser** | 15% | Yes | Yes |

→ [Pricing & Access Tiers](https://iabtechlab.github.io/seller-agent/guides/pricing-rules/)

### Order Lifecycle
Full state machine with 12 states and 20+ transitions, audit trail, and change request management.

→ [Order Lifecycle](https://iabtechlab.github.io/seller-agent/state-machines/order-lifecycle/)

### Multi-Turn Negotiation
Strategy-based negotiation engine (AGGRESSIVE, STANDARD, COLLABORATIVE, PREMIUM) with configurable concession limits per buyer tier.

→ [Negotiation Protocol](https://iabtechlab.github.io/seller-agent/integration/negotiation/)

## Quick Start

### Install

```bash
git clone https://github.com/IABTechLab/seller-agent.git
cd seller-agent
pip install -e .
```

### Configure

```bash
cp .env.example .env
```

Key settings:

```bash
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx        # Required
SELLER_ORGANIZATION_ID=my-publisher
SELLER_ORGANIZATION_NAME=My Publishing Company

# Ad server (optional — falls back to mock inventory)
GAM_ENABLED=false
GAM_NETWORK_CODE=12345678
GAM_JSON_KEY_PATH=/path/to/service-account.json

# Storage
STORAGE_TYPE=sqlite                          # or redis
DATABASE_URL=sqlite:///./ad_seller.db
```

→ [Full Configuration Reference](https://iabtechlab.github.io/seller-agent/guides/configuration/)

### Run

```bash
uvicorn ad_seller.interfaces.api.main:app --reload --port 8001
```

### Verify

```bash
# Health check
curl http://localhost:8001/health

# Public media kit (no auth)
curl http://localhost:8001/media-kit

# Agent discovery
curl http://localhost:8001/.well-known/agent.json
```

→ [Quickstart Guide](https://iabtechlab.github.io/seller-agent/getting-started/quickstart/)

## Publisher Setup

1. [Configuration & Environment](https://iabtechlab.github.io/seller-agent/guides/configuration/)
2. [Inventory Sync](https://iabtechlab.github.io/seller-agent/guides/inventory-sync/) — Connect GAM/FreeWheel
3. [Media Kit](https://iabtechlab.github.io/seller-agent/guides/media-kit/) — Set up packages and featured items
4. [Pricing & Access Tiers](https://iabtechlab.github.io/seller-agent/guides/pricing-rules/) — Configure buyer pricing
5. [Approval & HITL](https://iabtechlab.github.io/seller-agent/guides/approval-rules/) — Set up approval gates
6. [Buyer & Agent Management](https://iabtechlab.github.io/seller-agent/guides/agent-management/) — Manage API keys and trust

## API Reference

58 endpoints across 19 groups:

| Group | Endpoints | Description |
|-------|-----------|-------------|
| Media Kit | 4 | Public inventory catalog (no auth) |
| Packages | 7 | Tier-gated package CRUD |
| Products | 5 | Product catalog management |
| Quotes | 2 | Quote creation and retrieval |
| Proposals | 6 | Proposal lifecycle + counter-offers |
| Orders | 8 | Order CRUD + state transitions |
| Change Requests | 5 | CR lifecycle management |
| Sessions | 4 | Multi-turn session persistence |
| Authentication | 3 | API key management |
| Agent Registry | 4 | Agent trust management |
| MCP | 3 | MCP server interface |
| A2A | 2 | Agent-to-Agent JSON-RPC |

→ [Full API Reference](https://iabtechlab.github.io/seller-agent/api/overview/)

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests (393 tests)
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
