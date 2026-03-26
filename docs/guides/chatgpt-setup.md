# ChatGPT, Codex & AI IDE Setup Guide

Connect your seller agent to ChatGPT, OpenAI Codex, Cursor, Windsurf, or any MCP-compatible AI assistant.

## Prerequisites

Same as [Claude Desktop Setup](claude-desktop-setup.md) — your developer must have deployed the seller agent and generated credentials.

Your seller agent MCP endpoint: `https://your-publisher.example.com/mcp/sse`

---

## ChatGPT

ChatGPT natively supports MCP servers via Developer Mode.

### Step 1: Enable Developer Mode

1. Open [chatgpt.com](https://chatgpt.com)
2. Go to **Settings > Apps & Connectors > Advanced settings**
3. Toggle **Developer Mode** on

> Available on Plus, Pro, Business, Enterprise, and Education plans.

### Step 2: Add the Seller Agent

1. Go to **Settings > Connectors** (or **Settings > Apps**)
2. Click **Create**
3. Enter your MCP server URL: `https://your-publisher.example.com/mcp/sse`
4. Name it: `Seller Agent`
5. Add a description: `Manage publisher inventory, deals, pricing, and buyer relationships`
6. Click **Create**

### Step 3: Use in a Chat

1. Start a new chat
2. Click the **+** button at the bottom
3. Select your seller agent from the **More** menu
4. Start chatting: *"Show me my media kit"* or *"Create a PMP deal for GroupM at $28 CPM"*

ChatGPT will call your seller agent's MCP tools and show the results inline.

### Getting Started After Setup

Unlike Claude Desktop, ChatGPT does not display slash commands. Instead, use natural language to trigger the same actions:

| Instead of... | Say... |
|---|---|
| `/setup` | "Help me set up my seller agent" |
| `/status` | "Show me my seller agent status" |
| `/inventory` | "What inventory do I have?" |
| `/deals` | "Give me a deal status report" |
| `/queue` | "Show me what's in my inbound queue" |
| `/new-deal` | "Help me create a new deal" |
| `/configure` | "Show me my automation rules" |
| `/buyers` | "Who's been looking at my inventory?" |
| `/help` | "What can you do?" |

---

## OpenAI Codex

Codex supports MCP servers via its config file.

### Option A: CLI

```bash
codex mcp add seller-agent --url https://your-publisher.example.com/mcp/sse
```

### Option B: Config File

Edit `~/.codex/config.toml` (global) or `.codex/config.toml` (project):

```toml
[mcp_servers.seller-agent]
url = "https://your-publisher.example.com/mcp/sse"
bearer_token_env_var = "SELLER_AGENT_API_KEY"
```

Set the environment variable:

```bash
export SELLER_AGENT_API_KEY="sk-operator-XXXXX"
```

### Verify

In Codex, type `/mcp` to see connected servers and available tools.

---

## Cursor

Cursor supports MCP servers on all plans (including free).

### Option A: Project-Level Config

Create `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "seller-agent": {
      "url": "https://your-publisher.example.com/mcp/sse",
      "headers": {
        "Authorization": "Bearer sk-operator-XXXXX"
      }
    }
  }
}
```

### Option B: Global Config

Create `~/.cursor/mcp.json` with the same format.

### Using Environment Variables

```json
{
  "mcpServers": {
    "seller-agent": {
      "url": "https://your-publisher.example.com/mcp/sse",
      "headers": {
        "Authorization": "Bearer ${env:SELLER_AGENT_API_KEY}"
      }
    }
  }
}
```

---

## Windsurf

Windsurf supports MCP via its Cascade panel.

### Option A: MCP Marketplace

1. Click the **MCPs icon** in the top-right of the Cascade panel
2. Search for your seller agent or click **Add Custom**
3. Enter the MCP URL and credentials

### Option B: Config File

Edit `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "seller-agent": {
      "serverUrl": "https://your-publisher.example.com/mcp/sse",
      "headers": {
        "Authorization": "Bearer sk-operator-XXXXX"
      }
    }
  }
}
```

---

## Available Tools (All Platforms)

Once connected, all platforms have access to the same 41 MCP tools:

| Category | Examples |
|----------|---------|
| **Setup** | `get_setup_status`, `health_check`, `get_config` |
| **Inventory** | `list_products`, `sync_inventory`, `list_inventory` |
| **Media Kit** | `list_packages`, `create_package` |
| **Pricing** | `get_rate_card`, `update_rate_card`, `get_pricing` |
| **Deals** | `create_deal_from_template`, `push_deal_to_buyers`, `distribute_deal_via_ssp`, `migrate_deal`, `deprecate_deal` |
| **Approvals** | `list_pending_approvals`, `approve_or_reject` |
| **Buyers** | `list_buyer_agents`, `register_buyer_agent`, `set_agent_trust` |
| **Curators** | `list_curators`, `create_curated_deal` |
| **SSPs** | `list_ssps`, `troubleshoot_deal` |

See the [MCP Protocol Reference](../api/mcp.md) for the full tool catalog.

---

## REST API Alternative

If your platform doesn't support MCP, you can use the REST API directly:

```
Base URL: https://your-publisher.example.com
Auth: Authorization: Bearer sk-operator-XXXXX
```

The seller agent exposes 82 REST endpoints. See the [API Overview](../api/overview.md).

For ChatGPT specifically, you can also create a **Custom GPT** with Actions pointing to the REST API's OpenAPI spec at `https://your-publisher.example.com/openapi.json`.
