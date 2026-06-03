# Production Data Integration Guide

Replace the demo data layer (CSV + SQLite) with your production inventory, pricing, and deal management systems.

## Architecture

```
CrewAI Crew → MCP Tools → Ad Server Adapter → Your System
                              ↕
                       Storage Backend → Your Database
```

The agent logic (crew, tools, prompts) stays the same. Only the data layer changes.

## Step 1: Ad Server Adapter

The adapter pattern (`src/ad_seller/clients/ad_server_base.py`) defines the interface:

```python
class AdServerBase:
    async def list_inventory() -> list[InventoryItem]
    async def get_product(product_id: str) -> Product
    async def create_order(...) -> Order
    async def update_order_status(...) -> Order
```

**Current:** `CsvAdServerClient` reads from `data/csv/samples/`.

**To integrate your system:**

1. Create `src/ad_seller/clients/your_system_client.py` implementing `AdServerBase`
2. Register it in `src/ad_seller/clients/ad_server_base.py`:
   ```python
   def get_ad_server_client(ad_server_type: str):
       if ad_server_type == "your_system":
           from .your_system_client import YourSystemClient
           return YourSystemClient()
   ```
3. Set env var: `AD_SERVER_TYPE=your_system`

**Examples of adapters to build:**
- FreeWheel API adapter
- Google Ad Manager (GAM) adapter
- Xandr/AppNexus adapter
- Custom OpenDirect-compatible SSP

## Step 2: Storage Backend

Deals, orders, proposals, and event history need persistent storage.

**Current:** SQLite in-memory (`DATABASE_URL=sqlite:///:memory:`)

**Production options** (pluggable since v2.0):

```bash
# PostgreSQL (recommended for production)
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql://user:password@host:5432/seller_db

# Redis + PostgreSQL hybrid (high-throughput)
STORAGE_BACKEND=hybrid
DATABASE_URL=postgresql://user:password@host:5432/seller_db
REDIS_URL=redis://host:6379/0

# Redis only (ephemeral, fast)
STORAGE_BACKEND=redis
REDIS_URL=redis://host:6379/0
```

No code changes needed — set the env vars at deploy time.

## Step 3: Deploy Configuration

Update your `deploy.sh` or AgentCore env vars:

```bash
agentcore deploy \
  --env "AD_SERVER_TYPE=your_system" \
  --env "STORAGE_BACKEND=postgres" \
  --env "DATABASE_URL=postgresql://..." \
  --env "YOUR_SYSTEM_API_KEY=..." \
  --env "YOUR_SYSTEM_BASE_URL=https://api.your-ssp.com"
```

## What Stays the Same

- MCP tool definitions (`mcp_server.py`) — unchanged
- CrewAI crew logic (`crews/publisher_crew.py`) — unchanged
- Agent prompts and instructions — unchanged
- Deal flow state machine — unchanged
- AgentCore deployment pattern — unchanged

## What Changes

| Component | Demo | Production |
|-----------|------|------------|
| Product catalog source | CSV files | Your inventory API |
| Pricing data | Static CSV CPMs | Real-time pricing engine |
| Deal booking | SQLite insert | Your order management system |
| Event history | In-memory | PostgreSQL/Redis |
| Authentication | Internal API key | Your system's auth (OAuth, API key) |

## Testing the Integration

```bash
# Unit test your adapter
python -m pytest tests/unit/ -k "your_system"

# Integration test against live system
AD_SERVER_TYPE=your_system python -m pytest tests/integration/

# Deploy and test on AgentCore
bash infra/aws/agentcore/deploy.sh --profile prod --name seller_prod --mode http --test
```
