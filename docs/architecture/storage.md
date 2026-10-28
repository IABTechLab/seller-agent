# Storage

The seller agent uses a pluggable storage backend for all persistent state. The `StorageBackend` abstract base class defines the interface, and concrete implementations handle the actual data storage.

## StorageBackend Interface

Defined in `src/ad_seller/storage/base.py`.

### Core Methods

All methods are async:

| Method | Signature | Description |
|--------|-----------|-------------|
| `connect` | `() -> None` | Establish connection to storage backend |
| `disconnect` | `() -> None` | Close connection |
| `get` | `(key: str) -> Optional[Any]` | Retrieve a value by key |
| `set` | `(key: str, value: Any, ttl: Optional[int] = None) -> None` | Store a value with optional TTL in seconds |
| `delete` | `(key: str) -> bool` | Delete a key, returns True if key existed |
| `exists` | `(key: str) -> bool` | Check if key exists |
| `keys` | `(pattern: str = "*") -> list[str]` | List keys matching a glob pattern |

### Higher-Level Operations

The base class provides convenience methods built on the core interface. These handle key prefixing automatically:

| Method | Key Pattern | Description |
|--------|------------|-------------|
| `get_product` / `set_product` | `product:{id}` | Product catalog entries |
| `get_proposal` / `set_proposal` | `proposal:{id}` | Buyer proposals |
| `get_deal` / `set_deal` / `list_deals` | `deal:{id}` | Executed deals |
| `get_session` / `set_session` / `list_sessions` | `session:{id}` | Buyer conversation sessions |
| `get_quote` / `set_quote` / `list_quotes` | `quote:{id}` | Price quotes (default 24h TTL) |
| `get_order` / `set_order` / `list_orders` | `order:{id}` | Order state machines |
| `get_change_request` / `set_change_request` / `list_change_requests` | `change_request:{id}` | Post-deal modifications |
| `get_negotiation` / `set_negotiation` | `negotiation:{proposal_id}` | Negotiation histories |
| `get_agent` / `set_agent` / `list_agents` | `agent:{id}` | Registered buyer agents |
| `get_package` / `set_package` / `list_packages` | `package:{id}` | Media kit packages |
| `get_media_kit` / `set_media_kit` | `media_kit:{org_id}` | Seller organization media kit metadata |

## Key Prefix Convention

All keys follow the pattern `{entity_type}:{identifier}`. This enables efficient listing via the `keys()` method with glob patterns.

| Prefix | Entity | Example Key |
|--------|--------|-------------|
| `product:` | Product catalog | `product:display` |
| `proposal:` | Buyer proposal | `proposal:prop-a1b2c3d4` |
| `deal:` | Executed deal | `deal:DEMO-A1B2C3D4E5F6` |
| `session:` | Conversation session | `session:550e8400-e29b-...` |
| `quote:` | Price quote | `quote:qt-a1b2c3d4e5f6` |
| `order:` | Order state machine | `order:ORD-A1B2C3D4E5F6` |
| `change_request:` | Change request | `change_request:CR-A1B2C3D4E5F6` |
| `negotiation:` | Negotiation history | `negotiation:prop-a1b2c3d4` |
| `agent:` | Registered agent | `agent:550e8400-e29b-...` |
| `media_kit:` | Media kit metadata | `media_kit:default` |
| `package:` | Curated/dynamic package | `package:pkg-a1b2c3d4` |

Index keys use a secondary prefix pattern: `session_index:buyer:{pricing_key}` and `agent_url_index:{url}`.

## Available Backends

### SQLite (Default)

File-based storage using aiosqlite. Suitable for development and single-instance deployments.

### Redis

Network-based storage using aioredis. Supports native TTL, key pattern matching, and multi-instance deployments. Recommended for production.

## Durability on AgentCore

The default backend on a deployed AgentCore runtime is **in-memory SQLite**
(`sqlite:///:memory:`), so all authorization state — minted API keys,
`VerifiedTrust` records, and deal/order records — is **EPHEMERAL and lost on
every container recycle**. This is fine for same-account/dev but not for
production.

Durable persistence uses `--storage postgres`, which deploys Aurora + Redis and
runs the runtime with `STORAGE_TYPE=hybrid` (`DATABASE_URL` → Aurora KV,
`REDIS_URL` → Redis). Because Aurora lives in private subnets, this **requires
CUSTOMER_VPC mode AND the VPC reachability fix**: the private-subnet runtime
needs interface VPC endpoints for `bedrock-agentcore`/`bedrock-runtime`, `sts`,
and `secretsmanager` (plus ECR + CloudWatch Logs), and a **443 self-ingress on
the runtime's security group** so its ENIs can reach those shared-SG endpoints.
Without that 443 self-ingress the container is dark — health-check times out
with zero logs, since it cannot even reach the Logs endpoint. See
`infra/aws/agentcore/network-agentcore.yaml`.

**Product data is independent of the KV backend.** Products load from the
CSV/S3 ad-server adapter (`AD_SERVER_TYPE=csv|s3`) into an in-memory dict at
startup — never from the KV store — so the Postgres KV backend and the CSV/S3
catalog coexist with no product-into-Postgres migration.

The entrypoints use `os.environ.setdefault("STORAGE_TYPE", "sqlite")`, so a
deploy-supplied `hybrid` value wins while the dev default stays SQLite.

## Implementing a Custom Backend

1. Subclass `StorageBackend` from `src/ad_seller/storage/base.py`
2. Implement the 7 core abstract methods: `connect`, `disconnect`, `get`, `set`, `delete`, `exists`, `keys`
3. All higher-level methods (get_product, set_order, etc.) are provided by the base class and do not need to be overridden unless you want to optimize them

Example skeleton:

```python
from ad_seller.storage.base import StorageBackend
from typing import Any, Optional


class MyCustomBackend(StorageBackend):
    async def connect(self) -> None:
        # Initialize your connection
        pass

    async def disconnect(self) -> None:
        # Clean up
        pass

    async def get(self, key: str) -> Optional[Any]:
        # Return stored value or None
        pass

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        # Store value, respect TTL if provided (seconds)
        pass

    async def delete(self, key: str) -> bool:
        # Delete key, return True if it existed
        pass

    async def exists(self, key: str) -> bool:
        # Check existence
        pass

    async def keys(self, pattern: str = "*") -> list[str]:
        # Return keys matching glob pattern
        pass
```

Register your backend in the storage factory to make it available to the application.
