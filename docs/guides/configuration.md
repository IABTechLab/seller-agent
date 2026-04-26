# Configuration & Environment

All seller agent configuration is managed through environment variables, loaded
via a `.env` file or the shell environment. The agent uses
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
with case-insensitive variable names.

---

## Core Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANTHROPIC_API_KEY` | `str` | **required*** | API key for your LLM provider (see [Supported Providers](#supported-providers)) |
| `SELLER_ORGANIZATION_ID` | `str` | auto-generated | Your organization ID |
| `SELLER_ORGANIZATION_NAME` | `str` | `"Default Publisher"` | Organization display name |
| `SELLER_AGENT_NAME` | `str` | `"Ad Seller Agent"` | Agent name shown in discovery |
| `SELLER_AGENT_URL` | `str` | `"http://localhost:8000"` | Public URL for agent discovery (`.well-known/agent.json`) |

## Protocol & API Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DEFAULT_PROTOCOL` | `str` | `"opendirect21"` | Protocol selection: `opendirect21` or `a2a` |
| `OPENDIRECT_BASE_URL` | `str` | `"http://localhost:3000"` | OpenDirect service base URL |
| `OPENDIRECT_API_KEY` | `str` | `None` | OpenDirect API key (optional) |
| `OPENDIRECT_TOKEN` | `str` | `None` | OpenDirect auth token (optional) |

## Ad Server Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AD_SERVER_TYPE` | `str` | `"google_ad_manager"` | Ad server type: `google_ad_manager` or `freewheel` |
| `GAM_ENABLED` | `bool` | `false` | Feature flag to enable GAM integration |
| `GAM_NETWORK_CODE` | `str` | `None` | GAM network code (numeric ID) |
| `GAM_JSON_KEY_PATH` | `str` | `None` | Path to GAM service account JSON key file |
| `GAM_APPLICATION_NAME` | `str` | `"AdSellerSystem"` | Application name for GAM API requests |
| `GAM_API_VERSION` | `str` | `"v202411"` | GAM SOAP API version |
| `GAM_DEFAULT_TRAFFICKER_ID` | `str` | `None` | Default trafficker user ID for order creation |
| `FREEWHEEL_ENABLED` | `bool` | `false` | Feature flag to enable FreeWheel integration |
| `FREEWHEEL_NETWORK_ID` | `str` | `None` | Publisher network/account ID in FreeWheel |
| `FREEWHEEL_INVENTORY_MODE` | `str` | `"deals_only"` | `"deals_only"` (only pre-configured deals) or `"full"` (all inventory) |
| `FREEWHEEL_SH_MCP_URL` | `str` | `None` | Streaming Hub MCP endpoint URL |
| `FREEWHEEL_SH_USERNAME` | `str` | `None` | SH publisher account username (OAuth 2.0 ROPCG, 7-day token TTL) |
| `FREEWHEEL_SH_PASSWORD` | `str` | `None` | SH publisher account password |
| `FREEWHEEL_BC_MCP_URL` | `str` | `None` | Buyer Cloud MCP endpoint URL (for PG deals), e.g. `https://<buzz_key>.api.beeswax.com` |
| `FREEWHEEL_BC_EMAIL` | `str` | `None` | Beeswax account email (session cookie auth, 100h/30-day TTL) |
| `FREEWHEEL_BC_PASSWORD` | `str` | `None` | Beeswax account password |
| `FREEWHEEL_BC_BUZZ_KEY` | `str` | `None` | Buzz API key (determines API hostname and cookie name) |

## SSP Connector Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SSP_CONNECTORS` | `str` | `""` | Comma-separated list of SSPs: `pubmatic,index_exchange,magnite` |
| `SSP_ROUTING_RULES` | `str` | `""` | Routing rules: `ctv:pubmatic,display:index_exchange` |
| `PUBMATIC_MCP_URL` | `str` | `None` | PubMatic MCP endpoint (e.g., `https://mcp.pubmatic.com/sses`) |
| `PUBMATIC_API_KEY` | `str` | `None` | PubMatic API key |
| `INDEX_EXCHANGE_API_URL` | `str` | `None` | Index Exchange REST API URL |
| `INDEX_EXCHANGE_API_KEY` | `str` | `None` | Index Exchange API key |
| `MAGNITE_API_URL` | `str` | `None` | Magnite REST API URL |
| `MAGNITE_API_KEY` | `str` | `None` | Magnite API key |

## Inventory Sync Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `INVENTORY_SYNC_ENABLED` | `bool` | `false` | Enable periodic background inventory sync |
| `INVENTORY_SYNC_INTERVAL_MINUTES` | `int` | `60` | How often to sync (minutes) |
| `INVENTORY_SYNC_INCLUDE_ARCHIVED` | `bool` | `false` | Include archived ad units in sync |

## LLM Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DEFAULT_LLM_MODEL` | `str` | `"anthropic/claude-sonnet-4-5-20250929"` | Model for specialist agents (pricing, negotiation, etc.) |
| `MANAGER_LLM_MODEL` | `str` | `"anthropic/claude-opus-4-20250514"` | Model for manager/orchestrator agents |
| `LLM_TEMPERATURE` | `float` | `0.3` | LLM temperature (lower = more deterministic) |
| `LLM_MAX_TOKENS` | `int` | `4096` | Maximum tokens per LLM response |

### Supported Providers

The seller agent uses CrewAI's native provider integrations via `provider/model-name` format. No code changes are required to switch providers. Install the matching extra (e.g., `pip install "crewai[anthropic]"` or `pip install "crewai[openai]"`).

| Provider | Model Format | API Key Variable | Install Extra |
|----------|-------------|-----------------|---------------|
| **Anthropic** (default) | `anthropic/claude-sonnet-4-5-20250929` | `ANTHROPIC_API_KEY` | `crewai[anthropic]` |
| **OpenAI** | `openai/gpt-4o` | `OPENAI_API_KEY` | `crewai[openai]` |
| **Google Gemini** | `gemini/gemini-2.0-flash` | `GOOGLE_API_KEY` | `crewai[gemini]` |
| **Azure OpenAI** | `azure/my-deployment` | `AZURE_API_KEY`, `AZURE_API_BASE` | `crewai[azure]` |
| **AWS Bedrock** | `bedrock/anthropic.claude-3-sonnet` | AWS credentials | `crewai[bedrock]` |

**Example — switching to OpenAI:**

```bash
# Install: pip install "crewai[openai]"
DEFAULT_LLM_MODEL=openai/gpt-4o
MANAGER_LLM_MODEL=openai/gpt-4o
OPENAI_API_KEY=sk-xxxxx
```

!!! note "Prompt Tuning"
    Agent prompts are tuned and tested with Claude models. Other providers work but may produce different quality results. If you switch providers, test negotiation and pricing flows to verify acceptable output quality.

For other providers, see the [CrewAI LLM documentation](https://docs.crewai.com/en/learn/litellm-removal-guide).

## Database & Storage

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_URL` | `str` | `"sqlite:///./ad_seller.db"` | Database connection URL (SQLite or PostgreSQL) |
| `REDIS_URL` | `str` | `None` | Redis URL (for `redis` or `hybrid` storage) |
| `STORAGE_TYPE` | `str` | `"sqlite"` | Storage backend: `sqlite`, `redis`, or `hybrid` |
| `POSTGRES_POOL_MIN` | `int` | `2` | Minimum PostgreSQL connection pool size |
| `POSTGRES_POOL_MAX` | `int` | `10` | Maximum PostgreSQL connection pool size |

!!! tip "Hybrid Storage (Recommended for Production)"
    Set `STORAGE_TYPE=hybrid` with both `DATABASE_URL` (PostgreSQL) and `REDIS_URL` to route
    business data (products, deals, orders) to PostgreSQL and sessions/cache to Redis.
    See [Deployment](deployment.md) for details.

## Pricing Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DEFAULT_CURRENCY` | `str` | `"USD"` | Default currency for pricing |
| `MIN_DEAL_VALUE` | `float` | `1000.0` | Minimum deal value |
| `DEFAULT_PRICE_FLOOR_CPM` | `float` | `5.0` | Global default price floor (CPM) |

## Yield Optimization

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `YIELD_OPTIMIZATION_ENABLED` | `bool` | `true` | Enable yield optimization engine |
| `PROGRAMMATIC_FLOOR_MULTIPLIER` | `float` | `1.2` | Floor multiplier for programmatic deals |
| `PREFERRED_DEAL_DISCOUNT_MAX` | `float` | `0.15` | Maximum discount for preferred deals (15%) |

## Feature Flags

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GAM_ENABLED` | `bool` | `false` | Enable Google Ad Manager integration |
| `EVENT_BUS_ENABLED` | `bool` | `true` | Enable event publishing (required for approvals) |
| `APPROVAL_GATE_ENABLED` | `bool` | `false` | Enable human-in-the-loop approval gates |
| `AGENT_REGISTRY_ENABLED` | `bool` | `true` | Enable agent registry for A2A trust management |
| `API_KEY_AUTH_ENABLED` | `bool` | `true` | Enable API key authentication for buyers |
| `CREW_MEMORY_ENABLED` | `bool` | `true` | Enable CrewAI agent memory (conversation recall) |
| `YIELD_OPTIMIZATION_ENABLED` | `bool` | `true` | Enable yield optimization engine |

## Agent Registry

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AGENT_REGISTRY_ENABLED` | `bool` | `true` | Enable agent registry |
| `AGENT_REGISTRY_URL` | `str` | `"https://tools.iabtechlab.com/agent-registry"` | Primary IAB AAMP registry URL |
| `AGENT_REGISTRY_EXTRA_URLS` | `str` | `""` | Additional registry URLs (comma-separated) |
| `AUTO_APPROVE_REGISTERED_AGENTS` | `bool` | `true` | Auto-approve agents found in a registry |
| `REQUIRE_APPROVAL_FOR_UNREGISTERED` | `bool` | `true` | Require operator approval for unknown agents |

## Approval Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `APPROVAL_GATE_ENABLED` | `bool` | `false` | Enable approval gates |
| `APPROVAL_TIMEOUT_HOURS` | `int` | `24` | Hours before a pending approval expires |
| `APPROVAL_REQUIRED_FLOWS` | `str` | `""` | Comma-separated gate names requiring approval (e.g., `"proposal_decision,deal_registration"`) |

## Session Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SESSION_TTL_SECONDS` | `int` | `604800` | Session time-to-live in seconds (default: 7 days) |
| `SESSION_MAX_MESSAGES` | `int` | `200` | Maximum messages per session |

## CrewAI Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CREW_MEMORY_ENABLED` | `bool` | `true` | Enable agent memory |
| `CREW_VERBOSE` | `bool` | `true` | Enable verbose CrewAI logging |
| `CREW_MAX_ITERATIONS` | `int` | `15` | Maximum iterations per crew run |

## API Key Authentication

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `API_KEY_AUTH_ENABLED` | `bool` | `true` | Enable API key authentication |
| `API_KEY_DEFAULT_EXPIRY_DAYS` | `int` | `None` | Default key expiry in days (`None` = never expires) |

---

## Complete Example `.env` File

```bash
# =============================================================================
# LLM Provider (set the key for your chosen provider)
# =============================================================================
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxx
# OPENAI_API_KEY=sk-xxxxx                   # For OpenAI / Azure
# COHERE_API_KEY=xxxxx                      # For Cohere

# =============================================================================
# Seller Identity
# =============================================================================
SELLER_ORGANIZATION_ID=pub-acme-001
SELLER_ORGANIZATION_NAME=Acme Media Group
SELLER_AGENT_NAME=Acme Seller Agent
SELLER_AGENT_URL=https://seller.acme-media.com

# =============================================================================
# Ad Server (Google Ad Manager)
# =============================================================================
AD_SERVER_TYPE=google_ad_manager
GAM_ENABLED=true
GAM_NETWORK_CODE=12345678
GAM_JSON_KEY_PATH=/etc/secrets/gam-service-account.json
GAM_APPLICATION_NAME=AcmeSellerAgent
GAM_API_VERSION=v202411
# GAM_DEFAULT_TRAFFICKER_ID=111222333  # Optional

# =============================================================================
# LLM
# =============================================================================
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=4096

# =============================================================================
# Storage
# =============================================================================
STORAGE_TYPE=sqlite                                    # sqlite, redis, hybrid
DATABASE_URL=sqlite:///./ad_seller.db
# DATABASE_URL=postgresql+asyncpg://seller:seller@localhost:5432/ad_seller  # For Postgres/hybrid
# REDIS_URL=redis://localhost:6379/0                   # For redis or hybrid mode
# POSTGRES_POOL_MIN=2
# POSTGRES_POOL_MAX=10

# =============================================================================
# Pricing
# =============================================================================
DEFAULT_CURRENCY=USD
MIN_DEAL_VALUE=1000.0
DEFAULT_PRICE_FLOOR_CPM=5.0

# =============================================================================
# Yield Optimization
# =============================================================================
YIELD_OPTIMIZATION_ENABLED=true
PROGRAMMATIC_FLOOR_MULTIPLIER=1.2
PREFERRED_DEAL_DISCOUNT_MAX=0.15

# =============================================================================
# Feature Flags
# =============================================================================
EVENT_BUS_ENABLED=true
APPROVAL_GATE_ENABLED=false
AGENT_REGISTRY_ENABLED=true
API_KEY_AUTH_ENABLED=true
CREW_MEMORY_ENABLED=true

# =============================================================================
# Agent Registry
# =============================================================================
AGENT_REGISTRY_URL=https://tools.iabtechlab.com/agent-registry
# AGENT_REGISTRY_EXTRA_URLS=https://other-registry.example.com
AUTO_APPROVE_REGISTERED_AGENTS=true
REQUIRE_APPROVAL_FOR_UNREGISTERED=true

# =============================================================================
# Approval Gates
# =============================================================================
# APPROVAL_GATE_ENABLED=true
# APPROVAL_TIMEOUT_HOURS=24
# APPROVAL_REQUIRED_FLOWS=proposal_decision

# =============================================================================
# Sessions
# =============================================================================
SESSION_TTL_SECONDS=604800
SESSION_MAX_MESSAGES=200

# =============================================================================
# Protocol
# =============================================================================
DEFAULT_PROTOCOL=opendirect21
OPENDIRECT_BASE_URL=http://localhost:3000
```

---

## Roadmap

!!! note "Planned Configuration Improvements"
    Future releases plan to add:

    - Runtime configuration API (update settings without restart)
    - Per-product pricing overrides via API
    - Config validation and health-check endpoint
    - Secret management integration (AWS Secrets Manager, HashiCorp Vault)
    - FreeWheel ad server integration

    See [PROGRESS.md](https://github.com/IABTechLab/seller-agent/blob/main/.beads/PROGRESS.md) for roadmap status.
