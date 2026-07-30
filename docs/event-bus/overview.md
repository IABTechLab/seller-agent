# Event Bus

The event bus provides full observability of system activity. Every significant action emits an event that is stored and queryable via the API.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/events` | List events with optional filters (`flow_id`, `event_type`, `session_id`, `limit`) |
| GET | `/events/{event_id}` | Get a specific event by ID |

## Event Types

All event types are defined in `src/ad_seller/events/models.py` as the `EventType` enum.

### Proposal Lifecycle

| Event Type | Value | Description |
|-----------|-------|-------------|
| `PROPOSAL_RECEIVED` | `proposal.received` | A buyer proposal was submitted |
| `PROPOSAL_EVALUATED` | `proposal.evaluated` | The proposal was evaluated by the seller |
| `PROPOSAL_ACCEPTED` | `proposal.accepted` | The proposal was accepted |
| `PROPOSAL_REJECTED` | `proposal.rejected` | The proposal was rejected |
| `PROPOSAL_COUNTERED` | `proposal.countered` | A counter-offer was generated |

### Deal Lifecycle

| Event Type | Value | Description |
|-----------|-------|-------------|
| `DEAL_CREATED` | `deal.created` | A deal was generated from an accepted proposal |
| `DEAL_REGISTERED` | `deal.registered` | The deal was registered in the system |
| `DEAL_SYNCED` | `deal.synced` | The deal was synced to the ad server |

### Execution Lifecycle

| Event Type | Value | Description |
|-----------|-------|-------------|
| `EXECUTION_COMPLETED` | `execution.completed` | The full execution workflow completed |

### Approval Gates

| Event Type | Value | Description |
|-----------|-------|-------------|
| `APPROVAL_REQUESTED` | `approval.requested` | A human approval was requested |
| `APPROVAL_GRANTED` | `approval.granted` | The approval was granted |
| `APPROVAL_DENIED` | `approval.denied` | The approval was denied |
| `APPROVAL_TIMED_OUT` | `approval.timed_out` | The approval request expired |

### Session Lifecycle

| Event Type | Value | Description |
|-----------|-------|-------------|
| `SESSION_CREATED` | `session.created` | A new buyer conversation session was created |
| `SESSION_RESUMED` | `session.resumed` | An existing session was resumed |
| `SESSION_CLOSED` | `session.closed` | A session was closed |

### Package Lifecycle

| Event Type | Value | Description |
|-----------|-------|-------------|
| `PACKAGE_CREATED` | `package.created` | A new package was created |
| `PACKAGE_UPDATED` | `package.updated` | A package was modified |
| `PACKAGE_SYNCED` | `package.synced` | Packages were synced from the ad server |

### Negotiation Lifecycle

| Event Type | Value | Description |
|-----------|-------|-------------|
| `NEGOTIATION_STARTED` | `negotiation.started` | A multi-round negotiation was initiated |
| `NEGOTIATION_ROUND` | `negotiation.round` | A negotiation round was completed |
| `NEGOTIATION_CONCLUDED` | `negotiation.concluded` | The negotiation reached a terminal state (accepted or rejected) |

## Event Model

Each event contains:

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | string | UUID |
| `event_type` | EventType | One of the 22 event types above |
| `timestamp` | datetime | When the event occurred |
| `flow_id` | string | Associated workflow flow ID |
| `flow_type` | string | Type of flow (e.g., `proposal_handling`) |
| `proposal_id` | string | Associated proposal ID (if applicable) |
| `deal_id` | string | Associated deal ID (if applicable) |
| `session_id` | string | Associated session ID (if applicable) |
| `payload` | dict | Event-specific data |
| `metadata` | dict | Additional context |

## How the Event Bus Works

The event bus provides three operations:

1. **`emit_event`** --- Creates and stores a new event. Called internally by flows, engines, and API endpoints whenever a significant action occurs.

2. **`list_events`** --- Retrieves events with optional filters. Supports filtering by `flow_id`, `event_type`, `session_id`, and a `limit` parameter (default 50).

3. **`get_event`** --- Retrieves a single event by its `event_id`.

Events are stored in the configured storage backend under the standard key prefix convention. They are immutable once created.

## Example: Querying Events

List all negotiation events:

```bash
curl "http://localhost:8000/events?event_type=negotiation.round&limit=10"
```

List events for a specific session:

```bash
curl "http://localhost:8000/events?session_id=550e8400-e29b-41d4-a716-446655440000"
```

Get a specific event:

```bash
curl http://localhost:8000/events/550e8400-e29b-41d4-a716-446655440001
```
