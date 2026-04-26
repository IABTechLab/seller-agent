# Developer Setup Guide

Set up the seller agent infrastructure, connect to ad servers and SSPs, and generate the config file for your business team to use in Claude Desktop.

## Prerequisites

- Python 3.11+
- Docker (for deployment)
- Ad server credentials (GAM service account JSON or FreeWheel MCP URL)
- SSP API keys (optional: PubMatic, Index Exchange, Magnite)
- Anthropic API key

## Step 1: Deploy the Seller Agent

```bash
# Clone and install
git clone https://github.com/IABTechLab/seller-agent.git
cd seller-agent
pip install -e .

# Or with Docker
cd infra/docker
docker compose up
```

## Step 2: Configure Environment

Create a `.env` file:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Seller Identity
SELLER_ORGANIZATION_NAME=Your Publisher Name
SELLER_ORGANIZATION_ID=your-org-id
SELLER_DOMAIN=your-domain.com

# Storage (SQLite for dev, Postgres for prod)
STORAGE_TYPE=sqlite
DATABASE_URL=sqlite:///./ad_seller.db
```

## Step 3: Connect Ad Server

### Option A: Google Ad Manager

```env
AD_SERVER_TYPE=google_ad_manager
GAM_ENABLED=true
GAM_NETWORK_CODE=12345678
GAM_JSON_KEY_PATH=/path/to/service-account.json
GAM_APPLICATION_NAME=YourPublisher
```

### Option B: FreeWheel

```env
AD_SERVER_TYPE=freewheel
FREEWHEEL_ENABLED=true
FREEWHEEL_SH_MCP_URL=https://shmcp.freewheel.com
FREEWHEEL_NETWORK_ID=your-network-id
# Inventory mode: "deals_only" (default, safer) or "full"
FREEWHEEL_INVENTORY_MODE=deals_only
```

### Option C: No Ad Server (Mock Inventory)

Skip this step — the agent will create mock inventory for testing.

## Step 4: Connect SSPs (Optional)

```env
# Enable SSPs (comma-separated)
SSP_CONNECTORS=pubmatic,index_exchange

# PubMatic (MCP)
PUBMATIC_MCP_URL=https://mcp.pubmatic.com/sses
PUBMATIC_API_KEY=your-pubmatic-key

# Index Exchange (REST)
INDEX_EXCHANGE_API_URL=https://api.indexexchange.com
INDEX_EXCHANGE_API_KEY=your-ix-key

# Routing rules (optional)
SSP_ROUTING_RULES=ctv:pubmatic,display:index_exchange
```

## Step 5: Start the Server

```bash
uvicorn ad_seller.interfaces.api.main:app --host 0.0.0.0 --port 8000
```

Verify: `curl http://localhost:8000/health`

## Step 6: Generate Operator Credentials

```bash
# Create an operator API key
curl -X POST http://localhost:8000/auth/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name": "operator", "seat_id": "operator"}'
```

Save the returned API key.

## Step 7: Generate Claude Desktop Config

Create `claude_desktop_config.json` for your business team:

```json
{
  "mcpServers": {
    "seller-agent": {
      "url": "http://your-server:8000/mcp/mcp",
      "headers": {
        "Authorization": "Bearer <operator-api-key-from-step-6>"
      }
    }
  }
}
```

## Step 8: Hand Off

Give the `claude_desktop_config.json` file to your publisher operations team. They'll add it to Claude Desktop and complete the business setup (media kit, pricing, approval gates, buyer registration) through the interactive wizard.

See [Claude Desktop Setup Guide](claude-desktop-setup.md) for their instructions.

## Trigger Initial Inventory Sync

```bash
curl -X POST http://localhost:8000/api/v1/inventory-sync/trigger
```

This pulls inventory from your ad server so the business team has packages to work with in the wizard.
