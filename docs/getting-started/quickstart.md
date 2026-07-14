# Quickstart

Get the seller agent running locally and make your first API calls.

## Prerequisites

- Python 3.11 or later
- pip
- An Anthropic API key (required — the server will not start without it; see [Configure](#configure) below)

## Installation

Clone the repository and install in editable mode:

```bash
git clone https://github.com/IABTechLab/seller-agent.git
cd seller-agent
pip install -e .
```

For the full install with optional dependencies (dev tools, docs, GAM, Redis/Postgres):

```bash
pip install -e ".[all]"
```

## Configure

The seller agent reads settings from a `.env` file in the repo root. **`ANTHROPIC_API_KEY` is required** — the app fails to start with a `ValidationError` if it is missing. Copy the template and fill it in:

```bash
cp .env.example .env
# then edit .env and set:
#   ANTHROPIC_API_KEY=sk-ant-...
```

Every other setting has a sensible default (SQLite storage, CSV ad-server samples, no SSPs), so a single API key is enough to boot locally. See [Configuration](../guides/configuration.md) for the full list.

## Run the Server

Start the FastAPI server with auto-reload for development:

```bash
uvicorn ad_seller.interfaces.api.main:app --reload --port 8000
```

The server starts at `http://localhost:8000`.

## Verify It Works

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "healthy"}
```

> **This quickstart is tested.** `tests/smoke/test_quickstart_smoke.py` boots the app at the exact module path documented above (`ad_seller.interfaces.api.main:app`) through its real startup lifecycle and asserts `/health`, `/`, and `/products` respond — no network or LLM calls. Run it with `pytest tests/smoke/test_quickstart_smoke.py`. If it fails, the entrypoint on this page is wrong.

## Browse the API Docs

Open `http://localhost:8000/docs` in a browser for the auto-generated Swagger UI with all 58 endpoints.

## First API Calls

### List Products

```bash
curl http://localhost:8000/products
```

Returns the full product catalog with product IDs, names, base CPMs, floor CPMs, and supported deal types.

### Get Pricing

```bash
curl -X POST http://localhost:8000/pricing \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "display",
    "buyer_tier": "agency",
    "volume": 500000
  }'
```

Returns tiered pricing with base price, tier discount, volume discount, final price, and pricing rationale.

### Create a Quote

```bash
curl -X POST http://localhost:8000/api/v1/quotes \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "display",
    "deal_type": "PG",
    "impressions": 1000000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-04-30"
  }'
```

Returns a non-binding price quote with a 24-hour TTL. Use the `quote_id` from the response to book a deal.

### Book a Deal from a Quote

```bash
curl -X POST http://localhost:8000/api/v1/deals \
  -H "Content-Type: application/json" \
  -d '{
    "quote_id": "<quote_id from previous step>"
  }'
```

Returns a confirmed deal with a Deal ID, OpenRTB parameters, and DSP activation instructions.

## Next Steps

- [API Overview](../api/overview.md) --- see all 58 endpoints
- [Authentication](../api/authentication.md) --- set up API keys for authenticated access
- [Buyer Agent Integration](../integration/buyer-agent.md) --- connect a buyer agent
