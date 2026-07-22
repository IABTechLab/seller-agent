# API Overview

The Ad Seller System API exposes **59 endpoints** across **19 tags**. All endpoints are served from a single FastAPI application.

**Base URL:** `http://localhost:8000`
**OpenAPI docs:** `http://localhost:8000/docs`

---

## Core

| Method | Path | Summary |
|--------|------|---------|
| GET | `/` | API root |
| GET | `/health` | Health check endpoint |

## Products

| Method | Path | Summary |
|--------|------|---------|
| GET | `/products` | List all products in the catalog |
| POST | `/products/avails` | OpenDirect availability check for a product (shared `iab_agentic_primitives.protocol` avails contract). Availability is derived from the catalog (`requestedImpressions`, else budget-derived at the product CPM, else `minimum_impressions`; capped by `maximum_impressions` when set); unpriceable products are a 422, `deliveryConfidence` is omitted (no forecast data source), and `guaranteedImpressions` appears only for PG-capable products — nothing is fabricated, nothing is null-padded |
| GET | `/products/{product_id}` | Get a specific product |

## Pricing

| Method | Path | Summary |
|--------|------|---------|
| POST | `/pricing` | Get pricing for a product based on buyer context |

## Proposals

| Method | Path | Summary |
|--------|------|---------|
| POST | `/proposals` | Submit a proposal for review |

## Deals

| Method | Path | Summary |
|--------|------|---------|
| POST | `/deals` | Generate a deal from an accepted proposal |

## Discovery

| Method | Path | Summary |
|--------|------|---------|
| POST | `/discovery` | Process a discovery query about inventory |

## Events

| Method | Path | Summary |
|--------|------|---------|
| GET | `/events` | List events, optionally filtered by flow_id, event_type, or session_id |
| GET | `/events/{event_id}` | Get a specific event by ID |

## Approvals

| Method | Path | Summary |
|--------|------|---------|
| GET | `/approvals` | List all pending approval requests |
| GET | `/approvals/{approval_id}` | Get a specific approval request and its response |
| POST | `/approvals/{approval_id}/decide` | Submit a human decision for a pending approval |
| POST | `/approvals/{approval_id}/resume` | Resume a flow after an approval decision has been submitted |

## Sessions

| Method | Path | Summary |
|--------|------|---------|
| POST | `/sessions` | Create a new buyer conversation session |
| GET | `/sessions` | List sessions, optionally filtered by buyer identity or status |
| GET | `/sessions/{session_id}` | Get session details and conversation history |
| POST | `/sessions/{session_id}/messages` | Send a message within a session and get a response |
| POST | `/sessions/{session_id}/close` | Close a session |

## Negotiation

| Method | Path | Summary |
|--------|------|---------|
| POST | `/proposals/{proposal_id}/counter` | Submit a counter-offer in an ongoing negotiation |
| GET | `/proposals/{proposal_id}/negotiation` | Get full negotiation history for a proposal |

## Media Kit

| Method | Path | Summary |
|--------|------|---------|
| GET | `/media-kit` | Public media kit catalog overview |
| GET | `/media-kit/packages` | List packages with public view (price ranges, no exact pricing) |
| GET | `/media-kit/packages/{package_id}` | Get a single package with public view |
| POST | `/media-kit/search` | Search packages by keyword |

## Packages

| Method | Path | Summary |
|--------|------|---------|
| GET | `/packages` | List packages with tier-gated view |
| GET | `/packages/{package_id}` | Get a single package with tier-gated view |
| POST | `/packages` | Create a curated package (Layer 2) |
| PUT | `/packages/{package_id}` | Update an existing package |
| DELETE | `/packages/{package_id}` | Archive a package (soft delete) |
| POST | `/packages/assemble` | Assemble a dynamic package (Layer 3) from product IDs |
| POST | `/packages/sync` | Trigger ad server inventory sync (Layer 1) |

## Authentication

| Method | Path | Summary |
|--------|------|---------|
| POST | `/auth/api-keys` | Create a new API key for a buyer |
| GET | `/auth/api-keys` | List all API keys (metadata only, no secrets) |
| GET | `/auth/api-keys/{key_id}` | Get details for a specific API key |
| DELETE | `/auth/api-keys/{key_id}` | Revoke an API key |

## Agent Registry

| Method | Path | Summary |
|--------|------|---------|
| GET | `/.well-known/agent.json` | Serve this seller agent's card for A2A discovery |
| GET | `/registry/agents` | List agents in the local registry |
| GET | `/registry/agents/{agent_id}` | Get details for a specific registered agent |
| POST | `/registry/agents/discover` | Discover an agent by URL |
| PUT | `/registry/agents/{agent_id}/trust` | Update an agent's trust status |
| DELETE | `/registry/agents/{agent_id}` | Remove an agent from the local registry |

## Quotes

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/v1/quotes` | Request a non-binding price quote from the seller |
| GET | `/api/v1/quotes/{quote_id}` | Retrieve a previously issued quote |

## Deal Booking

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/v1/deals` | Book a deal from a previously issued quote |
| GET | `/api/v1/deals/{deal_id}` | Get the current status of a deal |

## Orders

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/v1/orders` | Create a new order and persist its state machine |
| GET | `/api/v1/orders` | List orders, optionally filtered by status |
| GET | `/api/v1/orders/report` | Summary report across all orders |
| GET | `/api/v1/orders/{order_id}` | Get order current status and audit trail |
| GET | `/api/v1/orders/{order_id}/history` | Get the full transition history for an order |
| POST | `/api/v1/orders/{order_id}/transition` | Transition an order to a new state |

## Change Requests

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/v1/change-requests` | Submit a change request for an existing order |
| GET | `/api/v1/change-requests` | List change requests, optionally filtered by order or status |
| GET | `/api/v1/change-requests/{cr_id}` | Get a change request by ID |
| POST | `/api/v1/change-requests/{cr_id}/review` | Approve or reject a pending change request |
| POST | `/api/v1/change-requests/{cr_id}/apply` | Apply an approved change request to the order |

## Audit

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/v1/orders/{order_id}/audit` | Detailed audit log for an order with optional filters |
