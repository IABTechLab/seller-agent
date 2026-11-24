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

When both headers are present, the system validates whichever is found first. Anonymous requests (no key) are allowed on read-only buyer endpoints (discovery, pricing, avails, media-kit search, agentic audience match) and receive public-tier access only. Routes that create or change state need a key; see [Buyer Write Surface](#buyer-write-surface).

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
KEY=$(ad-seller create-operator-key --label ci --quiet)
```

Run this with the same storage config (`.env`) as the server so the key lands in the backend the server reads. The full key is printed **once** — store it securely. `--quiet` / `-q` prints only the key, for scripting.

List operator keys (metadata only — secrets are never re-shown):

```bash
ad-seller list-operator-keys
ad-seller list-operator-keys --include-inactive   # include revoked/expired
```

To revoke an operator key out-of-band (frees the label for reuse):

```bash
ad-seller delete-operator-key --label "Primary operator"
# or
ad-seller delete-operator-key --key-id key-a1b2c3d4
```

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
- `POST /api/v1/deals/curated`, `POST /api/v1/deals/{id}/deprecate`, `POST /api/v1/deals/{id}/migrate`, `POST /api/v1/deals/bulk`
- `GET /api/v1/deals/{id}/buyer-status`, `GET /api/v1/deals/{id}/ssp-troubleshoot` (they call external systems)

## Buyer Write Surface

These routes need an API key (anonymous → 401 `authentication_required`):

- `POST /api/v1/quotes`
- `POST /proposals`, `POST /proposals/{id}/counter`, `POST /api/v1/negotiations/messages`
- `POST /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/messages`, `POST /sessions/{id}/close`
- `POST /api/v1/orders`, `POST /api/v1/change-requests`

`ALLOW_ANONYMOUS_BUYER_WRITES=true` lets anonymous callers use these routes again. It does not mean they are always priced at the public tier: on quotes, proposals and negotiation, identity fields claimed in the body are capped by the registry trust of the caller's `agent_url` (public without one), while `POST /sessions` still prices on body identity claims. Treat the flag as a compatibility switch, not a pricing control.

Deal booking through `POST /api/v1/deals` and `POST /api/v1/deals/from-template` checks for a verified buyer key in the route itself, so it needs a key whether or not the flag is set. The legacy `POST /deals` (deal from an accepted proposal) is not changed here and still accepts anonymous callers.

### Session ownership

A session opened with an API key records that key's id and belongs to that key alone. Two keys with the same advertiser, agency or seat (or with no identity at all) do not share sessions. `GET /sessions/{id}`, `POST /sessions/{id}/messages` and `POST /sessions/{id}/close` accept only the opening key or an operator key; any other key gets 404, the same as for an unknown id. `GET /sessions` lists the caller's own sessions; operators may list or filter all of them.

- Sessions opened with a key before key ids were recorded fall back to matching the key's buyer identity (advertiser, agency or seat). Ones opened with an identity-less key can't be attributed and are operator-only.
- Sessions opened without a key (on an earlier release, or while `ALLOW_ANONYMOUS_BUYER_WRITES` was on) have no owner. With the flag off they are operator-only: anonymous callers get 401 and buyer keys get 404. With the flag on, anyone the route admits, anonymous callers included, can read, message and close them.

### MCP tools

Over HTTP transports (Streamable HTTP at `/mcp`, SSE), the MCP twins of these routes apply the same checks, using the same `Authorization` / `X-Api-Key` headers:

- `bulk_deal_operations`, `create_curated_deal`, `migrate_deal`, `deprecate_deal` and `troubleshoot_deal` need an operator key.
- `request_quote` and `create_order` need a valid API key (buyer or operator) unless `ALLOW_ANONYMOUS_BUYER_WRITES=true`. They still price at the public tier, key or not.

A denied tool call returns a JSON error (`authentication_required`, `invalid_credential` or `operator_required`) instead of running. Local stdio access stays trusted.

### Upgrading

Buyer agents need a seller-issued API key before you upgrade. Without one, quotes, proposals, negotiation, sessions, orders and change requests fail with 401 `authentication_required`, and a buyer agent that has no key stored stops at the quote step. Mint keys with `POST /auth/api-keys` (operator key required) and hand them to each buyer before rolling out, or set `ALLOW_ANONYMOUS_BUYER_WRITES=true` for the transition.

`GET /health`, `GET /.well-known/agent.json` and the read-only discovery routes stay public.

Buyer-facing reads (`GET /packages`, `GET /registry/agents`, `GET /api/v1/rate-card`, media-kit search, etc.) stay public or buyer-authenticated as before.

Admin MCP tools over HTTP (Streamable HTTP / SSE) enforce the same operator check via `Authorization` / `X-Api-Key`, and buyer write tools need a key (see [MCP tools](#mcp-tools)). Local stdio MCP access is trusted like the CLI.

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

Sessions don't follow key rotation. A session belongs to the key that opened it, matched on `key_id`, not on buyer identity (see [Session ownership](#session-ownership)). A rotated or replacement key starts with no sessions and gets 404 on the old key's sessions, even if it has the same seat, agency and advertiser. Finish or close sessions before retiring a key, or open new sessions with the new key. Keyed sessions created before `owner_key_id` was recorded still fall back to matching the buyer identity until they expire (`SESSION_TTL_SECONDS`, 7 days by default).

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
