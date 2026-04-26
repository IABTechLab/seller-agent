# MCP (Model Context Protocol)

MCP is the **primary interface** for the seller agent. Publishers manage their agent from Claude (desktop or web), ChatGPT, Codex, Cursor, or any MCP-compatible assistant via 41 tools. Buyer agents also call seller tools through MCP for automated workflows.

## Connection

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp/mcp` | POST / GET | Streamable HTTP transport (current MCP standard, protocol 2025-06-18) — recommended |
| `/mcp-sse/sse` | GET | Legacy HTTP+SSE transport (protocol 2024-11-05, deprecated) — kept for backwards compat |

> **Path note:** The MCP sub-app is mounted at `/mcp`, but Starlette sub-app routing means the working client URL is `/mcp/mcp`. Connecting to bare `/mcp` returns a 307 redirect that most MCP clients cannot follow.

### Setup

**Claude (Desktop & Web):** Settings > Integrations > Add Custom Integration > paste your MCP URL

**ChatGPT:** Settings > Apps & Connectors > Developer Mode > Create connector > paste your MCP URL

**Codex:** Add to `~/.codex/config.toml`:
```toml
[mcp_servers.seller-agent]
url = "https://your-server.example.com/mcp/mcp"
bearer_token_env_var = "SELLER_AGENT_API_KEY"
```

**Cursor:** Add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "seller-agent": {
      "url": "https://your-server.example.com/mcp/mcp",
      "headers": { "Authorization": "Bearer <operator-api-key>" }
    }
  }
}
```

See full setup guides: [Claude](../guides/claude-desktop-setup.md) | [ChatGPT, Codex & AI IDEs](../guides/chatgpt-setup.md)

## Available Tools (41)

### Setup & Status

| Tool | Description |
|------|-------------|
| `get_setup_status` | Check what's configured vs missing — triggers setup wizard on first run |
| `health_check` | System health — storage, ad server, SSPs |
| `get_config` | Current configuration summary (no secrets) |
| `set_publisher_identity` | Set publisher name, domain, org ID |
| `list_agents` | Show the 3-level agent hierarchy and their roles |

### Inventory & Media Kit

| Tool | Description |
|------|-------------|
| `list_products` | Browse the product catalog |
| `list_inventory` | List raw inventory from ad server |
| `sync_inventory` | Trigger inventory sync from ad server (GAM or FreeWheel) |
| `get_sync_status` | Check sync scheduler status |
| `list_packages` | Browse media kit packages |
| `create_package` | Create a curated package |

### Pricing

| Tool | Description |
|------|-------------|
| `get_rate_card` | Current rate card (base CPMs by inventory type) |
| `update_rate_card` | Update rate card entries |
| `get_pricing` | Calculate tiered price for a product with buyer context |

### Deal Operations

| Tool | Description |
|------|-------------|
| `request_quote` | Request a non-binding price quote |
| `create_deal_from_template` | One-step deal creation (no quote needed) |
| `create_curated_deal` | Deal with curator overlay (base CPM + curator fee) |
| `push_deal_to_buyers` | IAB Deals API v1.0 push to buyer endpoints |
| `distribute_deal_via_ssp` | Route deal through SSP (PubMatic, Index Exchange) |
| `get_deal_performance` | Delivery and performance metrics |
| `migrate_deal` | Replace a deal with lineage tracking |
| `deprecate_deal` | Sunset a deal with reason and optional replacement |
| `get_deal_lineage` | Walk the deal evolution chain |
| `export_deals` | Export in DSP format (TTD, DV360, Amazon, Xandr) |
| `bulk_deal_operations` | Batch create/update/cancel |
| `troubleshoot_deal` | SSP diagnostics for underperforming deals |

### Orders & Approvals

| Tool | Description |
|------|-------------|
| `list_orders` | List orders and states |
| `transition_order` | Transition order to new state |
| `list_pending_approvals` | Show approval requests waiting for decision |
| `approve_or_reject` | Submit approval decision (approve/reject/counter) |
| `set_approval_gates` | Configure which flows require approval |

### Buyer Agent Management

| Tool | Description |
|------|-------------|
| `list_buyer_agents` | Show registered buyer agents and trust levels |
| `register_buyer_agent` | Register a buyer agent by URL |
| `set_agent_trust` | Set trust level (unknown/registered/approved/preferred/blocked) |

### SSP & Supply Chain

| Tool | Description |
|------|-------------|
| `list_ssps` | Show configured SSP connectors and routing rules |
| `get_supply_chain` | Seller identity and schain (sellers.json format) |
| `list_curators` | Available curators (Agent Range pre-registered) |

### API Keys

| Tool | Description |
|------|-------------|
| `create_api_key` | Create an API key for a buyer or agent |
| `list_api_keys` | List active keys |
| `revoke_api_key` | Revoke a key |

## Example Tool Call

```json
{
  "name": "create_deal_from_template",
  "arguments": {
    "deal_type": "PD",
    "product_id": "premium-ctv",
    "max_cpm": 35.0,
    "impressions": 5000000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-06-30"
  }
}
```

## When to Use MCP

- **Publishers**: Manage your seller agent from Claude, ChatGPT, or any MCP assistant — setup, deals, pricing, approvals
- **Buyer agents**: Automated deal workflows — structured, deterministic, fastest path
- **ChatGPT users**: Same tools available via MCP connection

## Protocol Comparison

| | MCP | A2A | REST API |
|---|-----|-----|----------|
| **Interface style** | Structured tool calls | Natural language (JSON-RPC) | HTTP request/response |
| **Best for** | Publisher operations, agent workflows | Discovery, negotiation, complex queries | Dashboards, programmatic access |
| **Response format** | Typed tool results | Mixed text + structured data | JSON |
| **Speed** | Fastest | Moderate (LLM processing) | Fast |
| **Determinism** | Fully deterministic | Non-deterministic (LLM) | Fully deterministic |
| **Transport** | Streamable HTTP (SSE fallback) | HTTP POST (JSON-RPC 2.0) | HTTP verbs |

## See Also

- [Claude Setup Guide](../guides/claude-desktop-setup.md) — Claude desktop & web setup
- [ChatGPT Setup Guide](../guides/chatgpt-setup.md) — OpenAI configuration
- [Developer Setup Guide](../guides/developer-setup.md) — infrastructure setup
- [A2A Protocol](a2a.md) — conversational agent-to-agent interface
- [API Overview](overview.md) — full REST API reference
