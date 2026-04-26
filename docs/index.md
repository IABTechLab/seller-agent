# Ad Seller Agent

The Ad Seller Agent is an **IAB OpenDirect 2.1 compliant** programmatic advertising seller system. It enables automated ad selling through AI agents, supporting the full lifecycle from inventory discovery through deal execution, SSP distribution, and post-deal management.

**Manage everything from Claude, ChatGPT, or any MCP-compatible AI assistant** — the seller agent exposes 41 MCP tools for conversational setup and day-to-day operations. An interactive setup wizard walks publishers through configuration step by step.

Part of the IAB Tech Lab Agent Ecosystem --- see also the [Buyer Agent](https://iabtechlab.github.io/buyer-agent/).

## Access Methods

| Protocol | Endpoint | Best For |
|----------|----------|----------|
| **[MCP](api/mcp.md)** | `/mcp/mcp` (Streamable HTTP), `/mcp-sse/sse` (legacy) | Primary interface — 41 tools for Claude, ChatGPT, Codex, Cursor, and buyer agents |
| **[A2A](api/a2a.md)** | `/a2a/seller/jsonrpc` | Conversational agent interactions — natural language, multi-turn |
| **[REST API](api/overview.md)** | `/api/v1/*` | Programmatic access — 82 endpoints across 15 groups |

## Key Capabilities

- **41 MCP tools** for Claude, ChatGPT, Codex, Cursor, and Windsurf — interactive setup wizard + full operations
- **82 REST endpoints** across 15 categories covering the complete ad selling workflow
- **Pluggable ad server** support — Google Ad Manager and FreeWheel (Streaming Hub + Buyer Cloud)
- **Multi-SSP distribution** — PubMatic (MCP), Index Exchange (REST), Magnite (REST) with routing rules
- **IAB Deals API v1.0** — standardized deal push to buyer DSPs
- **Curator support** — Agent Range pre-registered, fee-based curation with schain
- **Tiered pricing engine** with rate card API and buyer-context-aware discounts
- **Multi-round automated negotiation** with configurable strategies
- **Order state machine** with 12 states and 20 transitions
- **Deal lifecycle** — create, migrate, deprecate, and track lineage across deal evolution
- **Scheduled inventory sync** with incremental change detection and type overrides
- **Human-in-the-loop approval gates** with configurable guard conditions
- **Supply chain transparency** — sellers.json parsing and OpenRTB schain in deal responses
- **Event bus** for full observability of system activity (16 event types)
- **Agent-to-agent discovery** and trust management via IAB AAMP registry

## Getting Started

### Recommended: Interactive Setup Wizard

1. **Developer** deploys the server and connects ad server + SSPs → [Developer Setup](guides/developer-setup.md)
2. **Publisher ops** adds seller agent to Claude (desktop/web), ChatGPT, or Codex → wizard guides through business setup → [Setup Guide](guides/claude-desktop-setup.md)

### Manual Setup

- [Quickstart](getting-started/quickstart.md) — install, run, and make your first API call
- [Publisher Setup Guide](guides/publisher-setup.md) — step-by-step manual configuration

## Documentation Sections

### AI Assistant Setup

- [Claude (Desktop & Web)](guides/claude-desktop-setup.md) — publisher setup via interactive wizard
- [ChatGPT / Codex Setup](guides/chatgpt-setup.md) — OpenAI configuration
- [Developer Setup](guides/developer-setup.md) — infrastructure and credential setup

### API Reference

- [API Overview](api/overview.md) --- all 82 endpoints grouped by tag
- [MCP Protocol](api/mcp.md) --- 41 MCP tools for Claude, ChatGPT, and buyer agents
- [A2A Protocol](api/a2a.md) --- conversational agent-to-agent interface
- [Agent Discovery](api/agent-discovery.md) --- `/.well-known/agent.json` and trust registry
- [Authentication](api/authentication.md) --- API keys, access tiers, and agent trust
- [Quotes](api/quotes.md) --- non-binding price quotes
- [Orders](api/orders.md) --- order creation and state machine transitions
- [Change Requests](api/change-requests.md) --- post-deal modifications

### Publisher Guide

- [Publisher Setup](guides/publisher-setup.md) --- setup checklist (or use the wizard)
- [Configuration](guides/configuration.md) --- all environment variables
- [Inventory Sync](guides/inventory-sync.md) --- GAM, FreeWheel, scheduled sync, overrides
- [Media Kit](guides/media-kit.md) --- packages, tiers, featured items
- [Pricing & Access Tiers](guides/pricing-rules.md) --- rate card, discounts, yield optimization
- [Approval & HITL](guides/approval-rules.md) --- approval gates and guard conditions
- [Buyer & Agent Management](guides/agent-management.md) --- API keys, trust, registration

### Architecture

- [System Overview](architecture/overview.md) --- components and how they connect
- [Data Flow](architecture/data-flow.md) --- sequence diagrams for key workflows
- [Storage](architecture/storage.md) --- backend interface and key conventions

### State Machines

- [Order Lifecycle](state-machines/order-lifecycle.md) --- 12 states, 20 transitions
- [Change Request Flow](state-machines/change-request-flow.md) --- validation and approval pipeline

### Event Bus

- [Event Bus Overview](event-bus/overview.md) --- all 16 event types and usage
