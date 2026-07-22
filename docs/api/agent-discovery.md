# Agent Discovery

Agent discovery enables buyer agents to find seller agents and understand their capabilities before initiating transactions. The seller agent implements the A2A protocol standard for discovery via the `/.well-known/agent.json` endpoint.

## The Agent Card Endpoint

```
GET /.well-known/agent.json
```

This public endpoint returns an **agent card** --- a machine-readable description of the seller agent's identity, capabilities, skills, supported deal types, and authentication requirements. No authentication is required.

### Example Response

```json
{
  "name": "Premium Publisher Seller Agent",
  "description": "IAB OpenDirect 2.1 compliant seller agent for programmatic advertising. Supports product discovery, tiered pricing, proposal evaluation, multi-round negotiation, and deal execution.",
  "url": "https://seller.example.com",
  "version": "0.1.0",
  "provider": {
    "name": "Premium Publisher Inc.",
    "url": "https://seller.example.com"
  },
  "capabilities": {
    "protocols": ["opendirect21"],
    "streaming": false,
    "push_notifications": false
  },
  "skills": [
    {
      "id": "discovery",
      "name": "Inventory Discovery",
      "description": "Search and browse available inventory, media kits, and packages",
      "tags": ["inventory", "search", "media-kit"]
    },
    {
      "id": "pricing",
      "name": "Tiered Pricing",
      "description": "Get pricing based on buyer identity with volume discounts",
      "tags": ["pricing", "cpm", "negotiation"]
    },
    {
      "id": "proposals",
      "name": "Proposal Evaluation",
      "description": "Submit and evaluate advertising proposals",
      "tags": ["proposals", "evaluation", "counter-offers"]
    },
    {
      "id": "negotiation",
      "name": "Multi-Round Negotiation",
      "description": "Engage in automated price negotiation with strategy-based responses",
      "tags": ["negotiation", "deals"]
    },
    {
      "id": "deals",
      "name": "Deal Execution",
      "description": "Generate OpenRTB-compatible deal IDs for DSP activation",
      "tags": ["deals", "openrtb", "execution"]
    }
  ],
  "authentication": {
    "schemes": ["api_key", "bearer"]
  },
  "inventory_types": ["ctv", "display", "mobile_app", "native", "video"],
  "supported_deal_types": ["pg", "pmp", "preferred_deal", "private_auction"]
}
```

### Agent Card Fields

| Field | Description |
|-------|-------------|
| `name` | Human-readable seller agent name |
| `description` | What this seller offers |
| `url` | Base URL for all protocol endpoints |
| `version` | Agent version |
| `provider.name` | Organization operating the seller agent |
| `capabilities.protocols` | Supported protocols (`opendirect21`; `a2a` will be listed once an inbound A2A server ships) |
| `skills` | List of capabilities the agent supports (discovery, pricing, proposals, negotiation, deals) |
| `authentication.schemes` | Accepted auth methods (`api_key`, `bearer`) |
| `inventory_types` | Types of inventory available (dynamically populated from the product catalog) |
| `supported_deal_types` | Deal types the seller can execute (`pg`, `pmp`, `preferred_deal`, `private_auction`) |

## Agent Registry

The seller agent maintains a local registry of known buyer agents. When a buyer agent first connects, it is registered and assigned a trust status that determines its access level.

### Trust Status Lifecycle

```
unknown → registered → approved → preferred
                ↘ blocked
```

| Status | Description | How it happens |
|--------|-------------|----------------|
| `unknown` | Never seen before, not in any registry | Default for first contact |
| `registered` | Found in the IAB AAMP registry | Automatic verification against AAMP |
| `approved` | Manually approved by the seller operator | Operator action |
| `preferred` | Strategic partner with premium access | Operator action |
| `blocked` | Explicitly blocked --- zero data access | Operator action |

### Trust-to-Tier Mapping

Each trust status maps to a maximum access tier, which caps the buyer's pricing and data visibility:

| Trust Status | Maximum Access Tier | Pricing Visibility |
|--------------|--------------------|--------------------|
| `unknown` | `public` | Published rate card only |
| `registered` | `seat` | Seat-level pricing |
| `approved` | `advertiser` | Full advertiser-level pricing |
| `preferred` | `advertiser` | Full pricing + priority access |
| `blocked` | None (rejected) | No access |

The buyer's **effective tier** is the minimum of their trust-based ceiling and their claimed identity tier. For example, a buyer claiming `advertiser` tier but with `registered` trust status is capped at `seat` tier.

## Discovering a Seller Agent

A buyer agent discovers a seller through the following steps:

1. **Fetch the agent card** --- `GET https://seller.example.com/.well-known/agent.json`
2. **Inspect capabilities** --- Check supported protocols, skills, inventory types, and deal types
3. **Obtain an API key** --- `POST /auth/api-keys` with buyer identity
4. **Choose a protocol** --- Use [MCP](mcp.md) for structured operations ([A2A](a2a.md) is designed but not yet served)
5. **Start transacting** --- Browse products, request pricing, submit proposals, book deals

### Registry Endpoints (Operator-Facing)

Seller operators manage the agent registry through these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/registry/agents` | GET | List registered agents (filterable by type and trust status) |
| `/registry/agents/{agent_id}` | GET | Get details for a specific agent |
| `/registry/agents/{agent_id}/trust` | PUT | Update an agent's trust status |
| `/registry/agents/discover` | POST | Discover an agent by URL (fetches their agent card) |

## See Also

- [MCP Protocol](mcp.md) --- structured tool call interface
- [A2A Protocol](a2a.md) --- conversational agent-to-agent interface (designed, not yet implemented)
- [Authentication](authentication.md) --- API keys and access tiers
- [Buyer Agent Discovery](https://iabtechlab.github.io/buyer-agent/) --- buyer-side discovery documentation
