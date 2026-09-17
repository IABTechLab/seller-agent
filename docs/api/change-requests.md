# Change Requests

Change requests handle post-deal modifications to orders. Each request is validated against the current order state, assigned a severity level, and routed through the appropriate approval path.

## Authentication and scoping

**Every route on this surface requires a credential.** Anonymous callers get `401`.

- **Submission and reads** (`POST /api/v1/change-requests`, `GET /api/v1/change-requests`, `GET /api/v1/change-requests/{cr_id}`) accept a **buyer** key.
- **Review and apply** require an **operator** key (buyer key → `403`).

Reads are additionally **scoped to the caller**. A buyer credential sees only the change requests it filed; another actor's record reads `404` (not `403`, so the by-id route cannot be walked as an existence oracle). An operator credential sees the whole queue — that is who reviews it.

The requesting actor is **stamped by the seller** from the presented credential and is stable across key rotation for a buyer with a seat/agency/advertiser identity. It is never taken from the request body: `requested_by` is the field a reviewer trusts when approving, so a requester must not be able to name itself.

Change request records embed a server-side `rollback_snapshot` (a full copy of the order, audit log included). It is not part of any response on this surface and never crosses the wire.

## Severity Levels

Severity is auto-classified based on change type and magnitude:

| Severity | Approval | Description |
|----------|----------|-------------|
| `minor` | Auto-approved | Low-impact changes (e.g., creative swap, flight date shift of 3 days or less) |
| `material` | Human review required | Significant changes (e.g., impression adjustment, targeting change) |
| `critical` | Senior review required | High-impact changes (e.g., pricing change >20%, cancellation) |

### Default Severity by Change Type

| Change Type | Default Severity | Notes |
|-------------|-----------------|-------|
| `flight_dates` | `material` | Downgraded to `minor` if shift is 3 days or less |
| `impressions` | `material` | |
| `pricing` | `critical` | Upgraded to `critical` if price change >20% |
| `creative` | `minor` | Auto-approved |
| `targeting` | `material` | |
| `cancellation` | `critical` | |
| `other` | `material` | |

## Create a Change Request

**POST** `/api/v1/change-requests`

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `idempotency_key` | string | **Yes** | Requester-minted opaque key (UUID recommended). A replay with the same key and body returns the original change request instead of creating a second one; the same key reused with a different body is an `idempotency_conflict` (HTTP 409). Required since [#64](https://github.com/IABTechLab/seller-agent/pull/64). |
| `order_id` | string | Yes | The order to modify |
| `change_type` | string | Yes | One of: `flight_dates`, `impressions`, `pricing`, `creative`, `targeting`, `cancellation`, `other` |
| `diffs` | array | No | List of field-level changes: `{field, old_value, new_value}` |
| `proposed_values` | object | No | Key-value pairs of proposed new values |
| `reason` | string | No | Explanation for the change |

`requested_by` is **not** a request field. It is derived from the presented credential. A body that still sends it is accepted and the value ignored.

### Example: Shift Flight Dates (Minor --- Auto-Approved)

```bash
curl -X POST http://localhost:8000/api/v1/change-requests \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "<idempotency_key>",
    "order_id": "ORD-A1B2C3D4E5F6",
    "change_type": "flight_dates",
    "diffs": [
      {"field": "flight_start", "old_value": "2026-04-01", "new_value": "2026-04-03"},
      {"field": "flight_end", "old_value": "2026-04-30", "new_value": "2026-05-02"}
    ],
    "reason": "Campaign launch delayed by 2 days"
  }'
```

Because the date shift is 2 days (within the 3-day threshold), this is classified as `minor` and auto-approved.

### Example: Modify Pricing (Critical --- Requires Review)

```bash
curl -X POST http://localhost:8000/api/v1/change-requests \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "<idempotency_key>",
    "order_id": "ORD-A1B2C3D4E5F6",
    "change_type": "pricing",
    "diffs": [
      {"field": "final_cpm", "old_value": 10.00, "new_value": 8.50}
    ],
    "proposed_values": {"final_cpm": 8.50},
    "reason": "Buyer requested discount after volume commitment"
  }'
```

This is classified as `critical` (pricing change of 15%) and enters `pending_approval` status.

### Validation Errors (422)

The system validates change requests against the current order state:

- Cannot modify orders in `completed`, `cancelled`, or `failed` status
- Cancellation is only allowed from active states (`draft`, `submitted`, `pending_approval`, `approved`, `in_progress`, `booked`)
- Impression changes must be positive integers

Failed validation returns HTTP 422 with the change request ID and error list.

## List Change Requests

**GET** `/api/v1/change-requests`

| Query Param | Type | Description |
|-------------|------|-------------|
| `order_id` | string | Filter by order |
| `status` | string | Filter by status |

```bash
curl -H "Authorization: Bearer <api_key>" \
  "http://localhost:8000/api/v1/change-requests?order_id=ORD-A1B2C3D4E5F6&status=pending_approval"
```

A buyer credential sees only its own change requests; an operator credential sees all of them.

## Get Change Request

**GET** `/api/v1/change-requests/{cr_id}`

```bash
curl -H "Authorization: Bearer <api_key>" \
  http://localhost:8000/api/v1/change-requests/CR-A1B2C3D4E5F6
```

Returns **404** for a change request filed by a different actor.

## Review a Change Request

**POST** `/api/v1/change-requests/{cr_id}/review`

Approve or reject a change request that is in `pending_approval` status. Requires an **operator** credential — the decision is the seller's, not the requesting buyer's (anonymous → 401, buyer key → 403).

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `decision` | string | Yes | `approve` or `reject` |
| `decided_by` | string | No | Reviewer identity (default: `system`) |
| `reason` | string | No | Reason for the decision |

```bash
curl -X POST http://localhost:8000/api/v1/change-requests/CR-A1B2C3D4E5F6/review \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "decision": "approve",
    "decided_by": "human:ops-manager",
    "reason": "Approved per client relationship policy"
  }'
```

Returns **409** if the change request is not in `pending_approval` status.

## Apply a Change Request

**POST** `/api/v1/change-requests/{cr_id}/apply`

Applies an approved change request to the order. Updates the order metadata with the proposed values and diffs. Requires an **operator** credential (order mutation) — anonymous → 401, buyer key → 403.

```bash
curl -X POST http://localhost:8000/api/v1/change-requests/CR-A1B2C3D4E5F6/apply \
  -H "Authorization: Bearer <operator_api_key>"
```

Returns **409** if the change request is not in `approved` status.

Response:

```json
{
  "change_request_id": "CR-A1B2C3D4E5F6",
  "status": "applied",
  "order_id": "ORD-A1B2C3D4E5F6"
}
```

## Change Request Lifecycle

See [Change Request Flow](../state-machines/change-request-flow.md) for the full state diagram.

1. **pending** --- Created, awaiting validation
2. **validating** --- Being validated against order state
3. **pending_approval** --- Material/critical changes awaiting human review
4. **approved** --- Approved (auto or manual), ready to apply
5. **rejected** --- Rejected by reviewer
6. **applied** --- Changes applied to the order
7. **failed** --- Validation failed
