# A2A (Agent-to-Agent Protocol)

!!! warning "Status: designed, not implemented"
    The A2A endpoints described on this page are **not served by the current
    release** — this page documents the planned interface. The agent card no
    longer advertises `a2a`; it will again once an inbound A2A server ships.
    Use [MCP](mcp.md) or the OpenDirect REST surface today.

A2A is the **conversational agentic interface** for the seller agent. It enables natural language communication between buyer and seller agents over JSON-RPC 2.0. The seller agent interprets requests, selects appropriate tools, and responds with mixed text and structured data.

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /a2a/{agent_type}/jsonrpc` | POST | JSON-RPC 2.0 main endpoint for sending messages |
| `GET /a2a/{agent_type}/.well-known/agent-card.json` | GET | Agent capabilities discovery (agent card) |

For this seller agent, `{agent_type}` is `seller`, so the endpoints are:

- `POST /a2a/seller/jsonrpc`
- `GET /a2a/seller/.well-known/agent-card.json`

## JSON-RPC Request Format

All A2A requests use JSON-RPC 2.0:

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "messageId": "uuid",
      "role": "user",
      "parts": [{"kind": "text", "text": "What premium video inventory do you have for Q2?"}]
    },
    "contextId": "optional-for-multi-turn"
  },
  "id": "req-1"
}
```

### Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `jsonrpc` | Yes | Always `"2.0"` |
| `method` | Yes | `"message/send"` for standard requests |
| `params.message.messageId` | Yes | Unique message identifier (UUID) |
| `params.message.role` | Yes | `"user"` for buyer-initiated messages |
| `params.message.parts` | Yes | Array of message parts (text, data) |
| `params.contextId` | No | Context ID for multi-turn conversation continuity |
| `id` | Yes | JSON-RPC request ID for correlating responses |

## JSON-RPC Response Format

```json
{
  "jsonrpc": "2.0",
  "result": {
    "taskId": "task-id",
    "contextId": "ctx-id",
    "parts": [
      {"kind": "text", "text": "We have premium pre-roll inventory..."},
      {"kind": "data", "data": {"products": [...]}}
    ],
    "status": "completed"
  },
  "id": "req-1"
}
```

### Response Fields

| Field | Description |
|-------|-------------|
| `result.taskId` | Unique identifier for the task created by this request |
| `result.contextId` | Context ID to use for follow-up messages in this conversation |
| `result.parts` | Array of response parts --- `text` for natural language, `data` for structured payloads |
| `result.status` | Task status: `completed`, `in_progress`, or `failed` |

## A2A Capabilities

The A2A interface supports the following interaction types:

| Capability | Description | Example |
|------------|-------------|---------|
| **Discovery queries** | Explore available inventory | "What CTV inventory do you have available for Q1?" |
| **Pricing inquiries** | Get pricing for specific products | "What is the pricing for premium-video for 5,000,000 impressions?" |
| **Availability checks** | Check avails for date ranges | "Is premium-video available for 1M impressions from Jan 1 to Mar 31?" |
| **Proposal submission** | Submit proposals with structured context | "I would like to submit a proposal for the following inventory" |
| **Negotiation** | Multi-turn price negotiation | "I would like to counter proposal PROP-123" |
| **Deal requests** | Request Deal IDs for DSP activation | "Please generate a Deal ID for accepted proposal PROP-456" |

## Multi-Turn Conversations

A2A supports multi-turn conversations via the `contextId` field. The seller agent returns a `contextId` in the first response; include it in subsequent requests to maintain conversation state.

```
Buyer: "What CTV inventory do you have?"         → contextId: null
Seller: "We have premium pre-roll..."             → contextId: "ctx-abc123"
Buyer: "What's the pricing for 2M impressions?"   → contextId: "ctx-abc123"
Seller: "For premium pre-roll at 2M impressions..." → contextId: "ctx-abc123"
```

This enables the seller agent to maintain context about the buyer's interests, previous queries, and negotiation history within a conversation.

## When to Use A2A vs MCP

| Scenario | Recommended Protocol |
|----------|---------------------|
| Browse inventory with vague criteria | **A2A** --- natural language flexibility |
| Complex multi-part questions | **A2A** --- agent interprets and routes |
| Price negotiation with back-and-forth | **A2A** --- multi-turn context |
| Initial discovery of seller capabilities | **A2A** --- conversational exploration |
| Book a PG deal with known parameters | **MCP** --- direct structured tool call |
| Check availability for specific dates | **MCP** --- deterministic, fast |
| Batch operations in automated workflows | **MCP** --- no LLM overhead |
| Retrieve a specific product by ID | **MCP** --- simple structured lookup |

## Client Example

```python
from ad_seller.clients.a2a_client import A2AClient

async with A2AClient(base_url="http://localhost:8000") as client:
    # Discovery query
    response = await client.discovery_query(
        "What CTV inventory do you have available for Q1?"
    )
    print(response.text)

    # Pricing inquiry
    response = await client.pricing_inquiry(
        product_ids=["premium-video"],
        volume=5_000_000,
    )
    print(response.text)
    print(response.data)  # Structured pricing data
```

## See Also

- [MCP Protocol](mcp.md) --- structured tool call interface
- [Agent Discovery](agent-discovery.md) --- how buyer agents discover this seller
- [Negotiation Protocol](../integration/negotiation.md) --- multi-round negotiation mechanics
- [Buyer Agent A2A Client](https://iabtechlab.github.io/buyer-agent/) --- buyer-side A2A client documentation
