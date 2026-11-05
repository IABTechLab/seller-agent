# AgentCore Deployment

Deploy the seller agent to Amazon Bedrock AgentCore as a managed runtime. AgentCore handles container orchestration, scaling, and IAM — you deploy with a single CLI command.

---

## Prerequisites

- **AWS CLI** configured with credentials (`aws configure` or `--profile`)
- **Python 3.12+** with `pip install bedrock-agentcore`
- **No Docker required** — CodeBuild builds ARM64 containers in the cloud

---

## Quick Start

```bash
# Deploy the HTTP runtime (CrewAI + ChatInterface)
bash infra/aws/agentcore/deploy.sh \
  --mode http \
  --name my-seller-agent \
  --profile my-aws-profile \
  --test
```

This:
1. Runs `agentcore configure` to set up ECR, IAM roles, and memory
2. Uploads source to S3, builds via CodeBuild (ARM64)
3. Deploys the container to AgentCore
4. Runs integration tests against the live runtime

---

## Deployment Runtimes (HTTP / MCP / A2A)

`--mode` selects **which protocol runtime(s)** to deploy. This is a different
axis from the crew/chat *routing modes* (below), which live *inside* the HTTP
runtime. `--mode all` (the default) deploys all three as **separate AgentCore
runtimes**, each with its own ARN, entrypoint, and protocol — mirroring the
"all protocols up" ergonomics of the ECS deployment.

| Runtime | `--mode` | Entrypoint | AgentCore protocol | What it serves | Who calls it |
|---------|----------|-----------|--------------------|----------------|--------------|
| **HTTP** | `http` (or `crew`/`chat`) | `http_main.py` | `HTTP` | The `BedrockAgentCoreApp` invoke endpoint running the CrewAI PublisherCrew (crew mode) or the keyword ChatInterface (chat mode) | Interactive clients / demos sending a natural-language `prompt` |
| **MCP** | `mcp` | `mcp_main.py` | `MCP` | The seller's tool surface (`list_products`, `get_product_details`, `check_avails`, `create_deal`, …) over the Model Context Protocol | **Buyer agents** — the primary cross-org integration path; the buyer's MCP client discovers and calls these tools |
| **A2A** | `a2a` | `a2a_main.py` (`AGENTCORE_MODE=a2a`) | `HTTP`¹ | The A2A Starlette app + its own `/.well-known/agent-card.json` | Peer **agent-to-agent** callers that speak the A2A protocol |

¹ AgentCore has no native A2A protocol value, so the A2A server is deployed as a
plain `HTTP`-protocol runtime whose entrypoint (`a2a_main.py`) and
`AGENTCORE_MODE=a2a` select the A2A app in `main.py`.

### Why three separate runtimes

Each protocol has a distinct client contract and lifecycle, so they are
deployed as independent runtimes rather than multiplexed behind one:

- **Isolation & scaling** — a burst of buyer MCP tool calls scales and fails
  independently of interactive HTTP traffic or A2A peer traffic. One runtime
  crashing or cold-starting doesn't take the others down.
- **Distinct wire protocols** — MCP (JSON-RPC tool calls over SSE) and A2A
  (agent-card + task protocol) are not the same surface as the HTTP
  prompt/invoke API; each gets the entrypoint and AgentCore protocol value it
  needs.
- **Discovery** — the MCP and A2A runtimes register in the AAMP registry with
  their own `protocol_type` (`mcp` / `a2a`) so buyers resolve the right endpoint
  for the integration they want. (The HTTP runtime is the human/demo surface and
  is not the cross-org discovery target.)
- **Per-runtime auth** — the same CUSTOM_JWT authorizer is attached to all
  three, so one buyer JWT works everywhere, but each runtime enforces it at its
  own edge.

### Which runtime to use when

| You want to… | Use | Deploy with |
|--------------|-----|-------------|
| Let a **buyer agent** discover and call seller tools programmatically (the real cross-org path) | **MCP** | `--mode mcp` |
| Do **agent-to-agent** orchestration with a peer that speaks A2A | **A2A** | `--mode a2a` |
| Drive the agent interactively with a natural-language prompt (demos, the chat UI, `curl`, manual testing) | **HTTP** | `--mode http` (add `--mode crew` / `--mode chat` to fix the default routing mode) |
| Stand up the full seller for an end-to-end environment (buyer integration + demos + peer agents) | **all three** | `--mode all` (default) |

> **Rule of thumb:** cross-org buyer integration → **MCP**; peer-agent
> orchestration → **A2A**; humans/demos → **HTTP**. When in doubt, `--mode all`
> deploys everything and costs nothing extra until each runtime is invoked
> (AgentCore bills per invocation, not per idle runtime).

---

## Routing Modes (within the HTTP runtime)

The HTTP runtime supports two routing modes, selected per-request or by default:

| Mode | `routing_mode` | LLM | Tools | Best For |
|------|---------------|-----|-------|----------|
| **crew** | `"crew"` | Claude (Bedrock Anthropic Messages) | CrewAI PublisherCrew with MCP + BaseTool | Full agentic behavior — inventory, pricing, deals |
| **chat** | `"chat"` | None (keyword-based) | ChatInterface (5 intents, ~10 tools) | Fast deterministic responses |

Set the default via `ROUTING_MODE` env var, or override per-request with `routing_mode` in the payload.

### Crew Mode (Default for AgentCore)

The CrewAI PublisherCrew runs Claude on Bedrock's **Anthropic Messages** endpoint (`bedrock-runtime.<region>.amazonaws.com/anthropic`) via CrewAI's native `anthropic` provider — not the legacy Bedrock Converse provider. The Inventory Manager agent has access to real inventory data via MCP tools (read operations) and a BaseTool for deal creation (write operations).

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "show me CTV sports inventory", "routing_mode": "crew"}'
```

### Chat Mode

The existing ChatInterface keyword router. No LLM calls — routes by keyword matching to one of 5 intents.

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "list products"}'
```

---

## Architecture

```
┌────────────────────────────────────────────────┐
│              AgentCore Container               │
│                                                │
│  ┌─────────────────────────────────────────┐   │
│  │  BedrockAgentCoreApp (port 8080)        │   │
│  │  http_main.py                           │   │
│  │                                         │   │
│  │  ┌─────────┐    ┌────────────────────┐  │   │
│  │  │  crew   │    │      chat          │  │   │
│  │  │  mode   │    │      mode          │  │   │
│  │  └────┬────┘    └────────┬───────────┘  │   │
│  │       │                  │              │   │
│  │       ▼                  ▼              │   │
│  │  PublisherCrew      ChatInterface       │   │
│  │  (Anthropic         (keyword router)    │   │
│  │   Messages LLM)                         │   │
│  │       │                  │              │   │
│  │       ▼                  │              │   │
│  │  MCP Tools + CreateDealTool             │   │
│  │       │                  │              │   │
│  └───────┼──────────────────┼──────────────┘   │
│          │                  │                  │
│  ┌───────▼──────────────────▼──────────────┐   │
│  │  FastAPI + MCP Server (port 8001)       │   │
│  │  Background thread — REST API + MCP     │   │
│  │  Storage: SQLite (default) | Aurora     │   │
│  │  CSV product catalog                    │   │
│  └─────────────────────────────────────────┘   │
└────────────────────────────────────────────────┘
```

The HTTP runtime runs two servers in one container:
- **Port 8080**: AgentCore entrypoint (`BedrockAgentCoreApp`)
- **Port 8001**: Background FastAPI+MCP server (started on first crew request)

The background server provides:
- REST API endpoints for tool callbacks (products, pricing, deals)
- MCP server for CrewAI tool discovery via SSE transport
- Deal/order storage: SQLite in-memory by default, or Aurora PostgreSQL when deployed with `--storage postgres` (see [Durable Storage](#durable-storage) below)
- CSV product catalog (the catalog is always CSV/in-memory — it is never stored in Postgres)

---

## Deploy Script Reference

```bash
bash infra/aws/agentcore/deploy.sh [OPTIONS]

Options:
  --mode all|http|mcp|a2a|crew|chat  Runtime(s) to deploy (default: all — HTTP+MCP+A2A)
  --name NAME           Agent name (default: auto-generated)
  --storage sqlite|postgres  Deal/order persistence (default: sqlite).
                             postgres deploys Aurora + Redis in VPC network mode.
  --profile PROFILE     AWS CLI profile
  --region REGION       AWS region (default: us-west-2)
  --test                Run integration tests after deploy
  --help                Show usage
```

### Environment Variables

Set these in the AgentCore runtime configuration:

| Variable | Default | Description |
|----------|---------|-------------|
| `ROUTING_MODE` | `chat` | Default routing mode (`crew` or `chat`) |
| `DEFAULT_LLM_MODEL` | `us.anthropic.claude-sonnet-5` | Claude model, run on Bedrock's Anthropic Messages endpoint |
| `INTERNAL_API_PORT` | `8001` | Port for background FastAPI server |
| `CREW_MCP_TOOLS` | `list_products,get_product_details,...` | Comma-separated MCP tool filter |
| `CREW_MAX_ITER` | `0` (unlimited) | Max CrewAI iterations per task |
| `STORAGE_TYPE` | `sqlite` | Storage backend. `--storage postgres` sets this to `hybrid` (Aurora + Redis) |
| `DATABASE_URL` | _(unset)_ | Aurora connection string; injected by deploy.sh in postgres mode |
| `AD_SERVER_TYPE` | `csv` | Ad server adapter (`csv`, `gam`, `freewheel`) |
| `CSV_DATA_DIR` | `./data/csv/samples/aws_workshop` | Path to CSV inventory data |

---

## Durable Storage

By default the deal/order stores use **in-memory SQLite** — state is ephemeral
per container and lost on restart. This is fine for dev and stateless demos.

For durable persistence, deploy with `--storage postgres`:

```bash
bash infra/aws/agentcore/deploy.sh --mode http --storage postgres --profile genai
```

This:
1. Deploys the CloudFormation infra stack (Aurora Serverless v2 PostgreSQL +
   Redis) and runs the runtime in **VPC network mode**.
2. Sets `STORAGE_TYPE=hybrid` and injects `DATABASE_URL` (Aurora endpoint) into
   the runtime env.

**VPC reachability requirement:** in VPC mode the runtime reaches Aurora over
the shared security group, which needs a **443 self-ingress** rule plus the VPC
interface endpoints for the AWS APIs the container calls (Bedrock, Secrets
Manager, ECR, CloudWatch, S3). Without the 443 self-ingress the container starts
but silently cannot reach the endpoints.

**The product catalog is never stored in Postgres** — it is always loaded from
CSV/S3 into memory. Postgres backs only the deal/order/pricing state.

---

## Register + Authenticate (Cross-Org)

By default, every runtime is deployed behind a **CUSTOM_JWT authorizer** so that
cross-organization buyers authenticate with a short-lived OAuth
`client_credentials` bearer token rather than SigV4. Discovery is
**registry-based**: each runtime registers its OAuth HTTPS invocation endpoint
(plus the token endpoint and scope) in the AAMP registry, and buyers
resolve it from there.

### 1. Deploy the Cognito auth stack

`--auth` (the default) deploys `infra/aws/agentcore/auth-agentcore.yaml`, a
Cognito pool with a resource server (`seller-agent/invoke`), a
`client_credentials` app client, and a hosted domain. deploy.sh reads the stack
outputs and wires them into the runtime's authorizer:

```bash
bash infra/aws/agentcore/deploy.sh --mode all --profile genai
# Prints:  DiscoveryUrl / AppClientId / InvokeScope / TokenEndpoint
```

The authorizer is validated against **`allowedClients`** (the app client id) +
**`allowedScopes`** (`seller-agent/invoke`) — not `allowedAudience`, because a
Cognito `client_credentials` token carries `client_id` + `scope` but no `aud`.
The same authorizer config is applied to all three runtimes (http / mcp /
a2a), so one buyer JWT works across every surface.

Opt out for same-account/dev with `--no-auth` (reverts to legacy SigV4/PUBLIC).

### 2. BYO-IdP (skip Cognito)

To front the runtimes with your own OIDC issuer instead of the bundled Cognito
pool, pass the BYO-IdP flags — the Cognito stack is skipped and the supplied
issuer is used directly:

```bash
bash infra/aws/agentcore/deploy.sh --mode all \
  --idp-discovery-url https://idp.example.com/.well-known/openid-configuration \
  --idp-allowed-clients client-abc,client-def \
  --idp-allowed-scopes seller-agent/invoke
```

deploy.sh curls the discovery URL and asserts a `token_endpoint` before
configuring any runtime.

### 3. Register each runtime + print the buyer curl

After deploy, the post-deploy step registers each runtime in the AAMP registry
and prints the connection details. The registry record's `endpoint_url` is the
runtime's OAuth HTTPS invocations URL:

```
https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<ENCODED_ARN>/invocations?qualifier=DEFAULT
```

with `protocol_type` (`mcp`|`a2a`), `type=remote`, `auth_required=true`, and an
`authentication` block advertising the token endpoint + scope. The step
also prints an example `client_credentials` curl a buyer can use to mint a JWT.

### 4. Buyer side (for reference)

A buyer discovers the runtime via the registry (`AAMP_REGISTRY_URL`), mints a
`client_credentials` token from the advertised token endpoint (HTTP Basic with
its OWN client id/secret — never taken from the registry), and calls the
runtime's HTTPS `/invocations` URL with `Authorization: Bearer <jwt>`. Because
OAuth runtimes reject SigV4, the AWS CLI/boto3 cannot invoke them — the buyer
uses a JWT/HTTPS MCP client. See the buyer-agent docs for the `OAuthTokenProvider`
and transport selector.

### 5. Per-tier authorization (buyer access tiers)

The seller applies a **buyer access tier** — `public` < `registered_buyer`
(seat) < `preferred_agency` (agency) < `strategic_advertiser` (advertiser) —
that gates pricing and yield behavior (exact price vs ranges, tier discount,
negotiation, premium inventory, volume discounts, avails granularity, yield
relationship score). The tier is **Cognito-verified**, not self-declared:

- **Cognito owns *which* tier.** The resource server carries per-tier scopes
  (`seller-agent/seat`, `/agency`, `/advertiser`) alongside `seller-agent/invoke`.
  Each buyer is provisioned on a **per-tier app client** that is granted ONLY its
  tier scope + `invoke` (the auth stack outputs `SeatAppClientId` /
  `AgencyAppClientId` / `AdvertiserAppClientId`). "This buyer is an agency" =
  "hand them the agency client's id + secret." `public` = the base client
  (invoke only, no tier scope).
- **The edge enforces + forwards the tier.** The runtime's CUSTOM_JWT authorizer
  validates the scope, then deploy.sh forwards the verified tier to the container
  as the allowlisted header
  `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Tier`
  (`--request-header-allowlist`, applied automatically whenever auth is on). This
  header-allowlist is **required** — without it AgentCore strips the header at
  the edge and the tier never reaches the app (verified live 2026-09-19).
- **The app owns *what the tier means*.** The `claims.py` seam maps the forwarded
  scope/tier → `AccessTier` and feeds it through the SAME
  `verify_buyer_context` registry-ceiling cap the rest of the surface uses. Set
  `SCOPE_TIER_MAP` in `.env` so the seam recognizes the scope strings, e.g.:

  ```bash
  SCOPE_TIER_MAP=seller-agent/seat:registered_buyer,seller-agent/agency:preferred_agency,seller-agent/advertiser:strategic_advertiser
  ```

**Trust model:** the buyer transmits the tier header, but the **edge is the
source of truth** — a buyer cannot mint a token for a scope its client was not
granted, so a forged header cannot escalate the tier, and the registry ceiling
still caps it (`effective_tier = min(scope-bounded header, registry_ceiling)`).

**Rolling it out.** The per-tier scopes/clients are an auth-stack change; apply
it to an already-deployed stack with `--auth-update` (the default reuse path
skips a template change), then redeploy the runtimes so the header allowlist is
attached:

```bash
bash infra/aws/agentcore/deploy.sh --mode all --auth-update --profile genai
```

**Upgrade route (dynamic tiers).** Scopes fit a static, per-buyer tier ladder.
If tier ever becomes *dynamic* (spend- or contract-driven) or you want a single
shared app client, switch to a `tier` **custom claim** stamped by a Pre-Token-
Generation Lambda and enforced with the authorizer's `RequiredCustomClaims`
(supported by `authorizer_config.py`'s `--required-custom-claims`). Not needed
for the current model.

---

## LLM Transport & Compatibility Patches

Claude runs on Bedrock's **Anthropic Messages** endpoint (the `anthropic`
provider with a custom `base_url`), **not** the legacy Bedrock Converse
provider. Two patches exist in `patches/`, and only one is active on the default
path:

- **`crewai_bedrock_anthropic_fix.py` (ACTIVE — Messages path).** CrewAI (>=1.15)
  unconditionally stamps `"strict": True` on every tool schema, which Bedrock's
  Anthropic endpoint rejects (`tools.0.custom.strict: Extra inputs are not
  permitted`). This patch strips `strict` when the `base_url` is a Bedrock
  endpoint. Real Anthropic is unaffected.

- **`crewai_bedrock_fix.py` (DORMANT — legacy Converse path).** The old Converse
  orphaned-toolUse/toolResult sanitizer + tool-argument extraction fix. It is
  applied **only** when running on a `bedrock/…` Converse model with no
  Anthropic Messages `base_url` configured — which the default Sonnet 5 config
  does not do. Kept for backward compatibility with legacy Converse deployments;
  removal is tracked as a separate change.

`http_main.py` selects which patch to apply at first crew invocation based on
whether an Anthropic Messages `base_url` is set (`_on_converse_path`). Both are
idempotent and safe to call multiple times.

---

## Testing

### Unit Tests

```bash
# AgentCore-specific tests (209 tests)
pytest tests/unit/agentcore/ -v

# Full regression (includes community tests)
pytest tests/unit/ -v
```

### Integration Tests

Require a deployed runtime:

```bash
# Run against deployed runtime
pytest tests/integration/agentcore/test_runtime.py \
  --profile genai \
  --agent-name my-seller-agent \
  -v
```

The integration tests cover:
- Chat mode: list products
- Crew mode: list products, get pricing, rate card, discover inventory, product details
- Deal creation: above floor (success), below floor (rejection)
- Complex scenario: inventory + pricing recommendation

---

## Workshop Demo Data

The `data/csv/samples/aws_workshop/` directory contains synthetic inventory for Meridian Media Group — a fictional publisher with four properties:

| Property | Channels | Products |
|----------|----------|----------|
| Apex Sports | CTV, Linear | NBA, NHL, Premium Series |
| GNN (Global News Network) | Digital Video, Linear | Pre-roll, Outstream, Primetime |
| SportsPulse | Digital Video, Linear, Audio | Mid-roll, Live Broadcasts, Podcasts |
| Crestline Entertainment | CTV | Reality TV |

15 products across 5 channels (CTV, Linear TV, Digital Video, Audio, Display) with tiered pricing, audience data, and deal type support.

---

## Troubleshooting

### Cold Start Timeout

AgentCore containers have a 30-second initialization window. If the background FastAPI server takes too long to start:

- Check CloudWatch logs for `FastAPI+MCP failed to start on port 8001`
- The health check loop retries 30 times × 0.5s = 15s
- If consistently timing out, check if `requirements.txt` has heavy dependencies

### CrewAI Tool Execution

If the crew describes what it would do but doesn't call tools:

- Check the agent backstory includes authorization language
- Verify `create_deal` tool has the enriched description
- Check `CREW_MCP_TOOLS` env var includes the needed tools
- Review CloudWatch logs for `Bedrock: Successfully validated tool` messages

### Deal Creation Returns 401

The internal API key is created at startup. If it's missing:

- Check logs for `Internal API key created for tool auth`
- The `CreateDealTool` falls back to direct in-process creation (bypasses REST auth)
- This fallback is expected on AgentCore where storage instances don't persist
