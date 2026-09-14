# Orders

Orders track the execution lifecycle of a deal. Each order has a formal state machine with 12 states and 21 allowed transitions. See [Order Lifecycle](../state-machines/order-lifecycle.md) for the full state diagram.

## Create an Order

**POST** `/api/v1/orders`

Creates a new order in `draft` status and persists its state machine.

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `deal_id` | string | No | Associated deal ID |
| `quote_id` | string | No | Associated quote ID |
| `metadata` | object | No | Arbitrary key-value metadata |

```bash
curl -X POST http://localhost:8000/api/v1/orders \
  -H "Content-Type: application/json" \
  -d '{
    "deal_id": "DEMO-A1B2C3D4E5F6",
    "quote_id": "qt-a1b2c3d4e5f6",
    "metadata": {"campaign": "Q2 Brand Awareness"}
  }'
```

Response:

```json
{
  "order_id": "ORD-A1B2C3D4E5F6",
  "status": "draft",
  "audit_log": {"order_id": "ORD-A1B2C3D4E5F6", "transitions": []},
  "deal_id": "DEMO-A1B2C3D4E5F6",
  "quote_id": "qt-a1b2c3d4e5f6",
  "created_at": "2026-04-01T12:00:00Z",
  "metadata": {"campaign": "Q2 Brand Awareness"}
}
```

## List Orders

**GET** `/api/v1/orders`

| Query Param | Type | Description |
|-------------|------|-------------|
| `status` | string | Filter by order status (e.g., `draft`, `approved`, `booked`) |

```bash
curl "http://localhost:8000/api/v1/orders?status=in_progress"
```

## Get Order

**GET** `/api/v1/orders/{order_id}`

Returns the current order state including status, audit log, deal/quote IDs, and metadata.

```bash
curl http://localhost:8000/api/v1/orders/ORD-A1B2C3D4E5F6
```

## Transition an Order

**POST** `/api/v1/orders/{order_id}/transition`

Validates the transition against the state machine rules and records it in the audit log. Requires an **operator** credential — state transitions drive billing-relevant lifecycle (anonymous → 401, buyer key → 403).

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `to_status` | string | Yes | Target status (see [Order Lifecycle](../state-machines/order-lifecycle.md)) |
| `actor` | string | No | Who initiated: `system`, `human:<user_id>`, or `agent:<agent_id>` |
| `reason` | string | No | Why the transition happened |
| `metadata` | object | No | Additional context |

### Valid Statuses

`draft`, `submitted`, `pending_approval`, `approved`, `rejected`, `in_progress`, `syncing`, `completed`, `failed`, `cancelled`, `booked`, `unbooked`

```bash
curl -X POST http://localhost:8000/api/v1/orders/ORD-A1B2C3D4E5F6/transition \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "to_status": "submitted",
    "actor": "agent:buyer-001",
    "reason": "Ready for review"
  }'
```

Response:

```json
{
  "order_id": "ORD-A1B2C3D4E5F6",
  "status": "submitted",
  "transition": {
    "transition_id": "...",
    "from_status": "draft",
    "to_status": "submitted",
    "timestamp": "2026-04-01T12:05:00",
    "actor": "agent:buyer-001",
    "reason": "Ready for review",
    "metadata": {}
  },
  "allowed_next": ["pending_approval", "approved", "cancelled", "failed"]
}
```

**Error (409)** --- Invalid transition:

```json
{
  "error": "invalid_transition",
  "message": "Cannot transition order ORD-... from draft to booked: no matching transition rule",
  "current_status": "draft",
  "allowed_transitions": ["submitted", "cancelled"]
}
```

## Get Transition History

**GET** `/api/v1/orders/{order_id}/history`

Returns the full ordered list of state transitions.

```bash
curl http://localhost:8000/api/v1/orders/ORD-A1B2C3D4E5F6/history
```

Response:

```json
{
  "order_id": "ORD-A1B2C3D4E5F6",
  "current_status": "approved",
  "transitions": [
    {"from_status": "draft", "to_status": "submitted", "actor": "agent:buyer-001", "...": "..."},
    {"from_status": "submitted", "to_status": "approved", "actor": "human:ops-jane", "...": "..."}
  ],
  "transition_count": 2
}
```

## Get Order Audit

**GET** `/api/v1/orders/{order_id}/audit`

Detailed audit log including transitions and associated change requests. Supports filters:

| Query Param | Type | Description |
|-------------|------|-------------|
| `actor` | string | Filter by actor (exact or prefix match) |
| `from_date` | string | ISO date, transitions on or after |
| `to_date` | string | ISO date, transitions on or before |

```bash
curl "http://localhost:8000/api/v1/orders/ORD-A1B2C3D4E5F6/audit?actor=human&from_date=2026-04-01"
```

## Orders Report

**GET** `/api/v1/orders/report`

Aggregate statistics across all orders.

| Query Param | Type | Description |
|-------------|------|-------------|
| `from_date` | string | ISO date, filter orders created on or after |
| `to_date` | string | ISO date, filter orders created on or before |

```bash
curl http://localhost:8000/api/v1/orders/report
```

Response:

```json
{
  "total_orders": 42,
  "status_counts": {"draft": 5, "booked": 20, "completed": 15, "cancelled": 2},
  "total_transitions": 168,
  "avg_transitions_per_order": 4.0,
  "actor_type_counts": {"system": 100, "human": 50, "agent": 18},
  "change_requests": {
    "total": 12,
    "by_status": {"applied": 8, "rejected": 2, "pending_approval": 2}
  }
}
```
