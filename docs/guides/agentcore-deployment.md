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

## Runtime Modes

The seller agent supports two routing modes within a single HTTP runtime:

| Mode | `routing_mode` | LLM | Tools | Best For |
|------|---------------|-----|-------|----------|
| **crew** | `"crew"` | Bedrock Converse (Sonnet) | CrewAI PublisherCrew with MCP + BaseTool | Full agentic behavior — inventory, pricing, deals |
| **chat** | `"chat"` | None (keyword-based) | ChatInterface (5 intents, ~10 tools) | Fast deterministic responses |

Set the default via `ROUTING_MODE` env var, or override per-request with `routing_mode` in the payload.

### Crew Mode (Default for AgentCore)

The CrewAI PublisherCrew runs with native Bedrock Converse. The Inventory Manager agent has access to real inventory data via MCP tools (read operations) and a BaseTool for deal creation (write operations).

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
│  │  (Bedrock LLM)      (keyword router)    │   │
│  │       │                  │              │   │
│  │       ▼                  │              │   │
│  │  MCP Tools + CreateDealTool             │   │
│  │       │                  │              │   │
│  └───────┼──────────────────┼──────────────┘   │
│          │                  │                  │
│  ┌───────▼──────────────────▼──────────────┐   │
│  │  FastAPI + MCP Server (port 8001)       │   │
│  │  Background thread — REST API + MCP     │   │
│  │  SQLite in-memory storage               │   │
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
- SQLite in-memory storage with CSV product catalog

---

## Deploy Script Reference

```bash
bash infra/aws/agentcore/deploy.sh [OPTIONS]

Options:
  --mode http|mcp       Runtime mode (default: http)
  --name NAME           Agent name (default: auto-generated)
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
| `DEFAULT_LLM_MODEL` | `bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Bedrock model for CrewAI |
| `INTERNAL_API_PORT` | `8001` | Port for background FastAPI server |
| `CREW_MCP_TOOLS` | `list_products,get_product_details,...` | Comma-separated MCP tool filter |
| `CREW_MAX_ITER` | `0` (unlimited) | Max CrewAI iterations per task |
| `STORAGE_TYPE` | `sqlite` | Storage backend (`sqlite` or `hybrid`) |
| `AD_SERVER_TYPE` | `csv` | Ad server adapter (`csv`, `gam`, `freewheel`) |
| `CSV_DATA_DIR` | `./data/csv/samples/aws_workshop` | Path to CSV inventory data |

---

## Bedrock Converse Patch

The `patches/crewai_bedrock_fix.py` module fixes two bugs in CrewAI's native Bedrock Converse provider:

1. **Orphaned toolUse/toolResult sanitization** — Bedrock rejects message histories with unmatched tool blocks. The patch strips orphaned blocks before each API call.

2. **Tool argument extraction** — `_parse_native_tool_call` reads `arguments` (empty string `"{}"`) instead of `input` (actual args). The patch intercepts Bedrock-format dicts and reads `input` directly.

The patch is applied automatically on first crew invocation. It's idempotent and safe to call multiple times. Cherry-pickable as a standalone commit for other CrewAI + Bedrock projects.

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
