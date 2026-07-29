# Authentication

The seller agent supports authenticated and anonymous access. Authentication unlocks tiered pricing, negotiation, and richer data in responses. **Operator** credentials unlock the control plane (key management, rate card, registry trust, packages, inventory sync).

## Authentication Methods

Two methods are accepted. Both can be used on any endpoint:

### Bearer Token

```
Authorization: Bearer <api_key>
```

### API Key Header

```
X-Api-Key: <api_key>
```

When both headers are present, the system validates whichever is found first. Anonymous requests (no key) are allowed on most buyer-facing endpoints but receive public-tier access only.

## Key Roles

Every API key has a role:

| Role | Purpose |
|------|---------|
| `buyer` | Buyer-agent credential. Grants tiered data access (seat/agency/advertiser pricing). **No** control-plane rights. |
| `operator` | Publisher operator credential. Required for admin REST endpoints and admin MCP tools over HTTP. |

Pre-existing keys (stored before the role field existed) deserialize as `buyer` — they are never silently promoted to operator.

## Bootstrap: First Operator Key

Creating keys via the HTTP API itself requires an operator credential. Mint the **first** operator key out-of-band with the CLI (writes directly to storage — no network surface):

```bash
ad-seller create-operator-key --label "Primary operator"
```

Run this with the same storage config (`.env`) as the server so the key lands in the backend the server reads. The full key is printed **once** — store it securely.

Subsequent operator keys can be minted over HTTP with an existing operator credential (see below).

## Operator Surface

These routes require a valid **operator** key (anonymous → 401, buyer key → 403):

- All `/auth/api-keys` routes (create buyer, create operator, list, get, revoke)
- `/events`, `/events/{id}`
- `PUT /api/v1/rate-card`
- `POST /api/v1/inventory-sync/trigger`
- `GET /gam/orders`, `GET /gam/report`
- Registry mutations: discover, trust update, delete
- Package mutations: `POST/PUT/DELETE /packages`, `/packages/assemble`, `/packages/sync`
- `POST /api/v1/curators`, `POST /api/v1/deals/push`, `POST /api/v1/deals/distribute`

Buyer-facing reads (`GET /packages`, `GET /registry/agents`, `GET /api/v1/rate-card`, media-kit search, etc.) stay public or buyer-authenticated as before.

Admin MCP tools over HTTP (Streamable HTTP / SSE) enforce the same operator check via `Authorization` / `X-Api-Key`. Local stdio MCP access is trusted like the CLI.

## API Key Lifecycle

All key-management endpoints below require an operator credential.

### Create a Buyer Key

```bash
curl -X POST http://localhost:8000/auth/api-keys \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "seat_id": "seat-acme-001",
    "seat_name": "Acme DSP",
    "agency_id": "agency-mega",
    "agency_name": "Mega Agency",
    "advertiser_id": "adv-widget-co",
    "advertiser_name": "Widget Co",
    "label": "Widget Co production key",
    "expires_in_days": 365
  }'
```

This endpoint always creates a **buyer** key. There is no `role` field — operator keys use a separate endpoint.

The response contains the **full API key** which is shown **only once**. Store it securely --- it cannot be retrieved again.

### Create an Operator Key

```bash
curl -X POST http://localhost:8000/auth/api-keys/operator \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "label": "Ops secondary key",
    "expires_in_days": 365
  }'
```

Operator keys carry no buyer identity (no seat/agency/advertiser fields) — only `label` and optional `expires_in_days`.

### List Keys

```bash
curl http://localhost:8000/auth/api-keys \
  -H "Authorization: Bearer <operator_api_key>"
```

Returns metadata for all keys (no secrets). Includes key ID, label, role, identity, creation date, and status.

### Get Key Details

```bash
curl http://localhost:8000/auth/api-keys/{key_id} \
  -H "Authorization: Bearer <operator_api_key>"
```

### Revoke a Key

```bash
curl -X DELETE http://localhost:8000/auth/api-keys/{key_id} \
  -H "Authorization: Bearer <operator_api_key>"
```

Revoked keys immediately return HTTP 401 on use.

## Access Tiers

Access tiers control pricing visibility, discount eligibility, and negotiation access for **buyer** keys:

| Tier | Description | Pricing Visibility | Negotiation |
|------|-------------|-------------------|-------------|
| `public` | Anonymous / unknown buyer | Price ranges only | No |
| `seat` | Identified DSP seat | Exact prices, no discounts | Limited |
| `agency` | Agency-level identity | Tier discounts applied | Standard |
| `advertiser` | Full advertiser identity | Full discounts + volume | Premium |

The tier is determined automatically from the API key's identity fields. If no key is provided, the tier falls back to `public` (or to the `buyer_tier` body parameter for backward compatibility).

## Agent Registry Trust Levels

When a buyer agent provides its `agent_url`, the seller looks up the agent in its registry and maps trust level to a maximum access tier:

| Trust Status | Description | Max Access Tier |
|-------------|-------------|-----------------|
| `unknown` | Never seen before | `public` |
| `registered` | Fetched agent card, not yet verified | `seat` |
| `approved` | Manually approved by seller operator | `advertiser` |
| `preferred` | Trusted partner with custom pricing rules | `advertiser` |
| `blocked` | Rejected --- returns HTTP 403, zero data | None |

The effective tier is the **minimum** of the API key tier and the agent trust tier. A `preferred` agent with a `seat`-level API key gets `seat` access. A `public` API key with an `approved` agent gets `public` access.

### Managing Trust

Registry mutations require an operator credential:

```bash
# Discover and register an agent
curl -X POST http://localhost:8000/registry/agents/discover \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"agent_url": "https://buyer.example.com"}'

# Approve the agent
curl -X PUT http://localhost:8000/registry/agents/{agent_id}/trust \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"trust_status": "approved", "notes": "Verified by ops team"}'

# Block a malicious agent
curl -X PUT http://localhost:8000/registry/agents/{agent_id}/trust \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"trust_status": "blocked", "notes": "Abuse detected"}'
```
