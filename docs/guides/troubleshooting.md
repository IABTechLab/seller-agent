# Troubleshooting Guide

This guide covers common problems you may encounter running the IAB Tech Lab Seller Agent, what the errors mean, and how to fix them.

---

## Health Check Failures

The `/health` endpoint and the `/status` slash command run a suite of checks. Each check and what a failure means:

| Check | What failure means | How to fix |
|---|---|---|
| `database` | Can't read/write to storage | See [Storage Issues](#storage-issues) |
| `ad_server` | GAM or FreeWheel connection lost | See [Ad Server Connection Errors](#ad-server-connection-errors) |
| `ssp_sync` | One or more SSP connectors unreachable | See [SSP Sync Failures](#ssp-sync-failures) |
| `agent_registry` | IAB registry unreachable (non-fatal) | Check internet connectivity; safe to ignore if registry is down |
| `event_bus` | Internal event pipeline stalled | Restart the server; check logs for `EventBus` errors |
| `session_store` | Redis unreachable (if configured) | See [Storage Issues](#storage-issues) |

### Running health checks manually

```bash
curl http://localhost:8000/health
```

For a detailed breakdown of each subsystem:

```bash
curl http://localhost:8000/health/detailed
```

---

## Ad Server Connection Errors

### Google Ad Manager (GAM)

**Error: `GAM auth failed: Invalid JWT signature`**

The service account key is invalid, revoked, or pointing to the wrong file.

1. Verify `GAM_JSON_KEY_PATH` in your `.env` points to the correct file.
2. Open the JSON file and confirm the `client_email` field matches the service account linked in GAM Admin.
3. If the key is stale, create a new one in Google Cloud Console (IAM & Admin > Service Accounts).
4. Restart the seller agent after updating the key.

**Error: `GAM auth failed: Network code not found`**

`GAM_NETWORK_CODE` is wrong or the service account hasn't been granted API access.

1. Confirm your network code in GAM Admin > Global Settings.
2. In GAM, go to Admin > Global Settings > API access. Verify the service account is listed.

**Error: `GAM API quota exceeded`**

You've hit Google's API rate limit (typically 2,000 calls/day for SOAP).

- Reduce `INVENTORY_SYNC_INTERVAL_MINUTES` to sync less frequently.
- Use `INVENTORY_SYNC_ENABLED=false` and trigger syncs manually when needed.

---

### FreeWheel MCP Connection Failures

**Error: `FreeWheel SH MCP: connection refused`**

The Streaming Hub MCP URL is unreachable.

1. Confirm `FREEWHEEL_SH_MCP_URL` is set correctly (e.g. `https://shmcp.freewheel.com`).
2. Verify your network allows outbound HTTPS to that host.
3. Check FreeWheel status at their support portal.

**Error: `FreeWheel BC MCP: 401 Unauthorized`**

Beeswax session credentials for Buyer Cloud are invalid or expired.

1. Verify `FREEWHEEL_BC_EMAIL` and `FREEWHEEL_BC_PASSWORD` are correct.
2. Verify `FREEWHEEL_BC_BUZZ_KEY` matches your Beeswax account (it determines the API hostname: `https://<buzz_key>.api.beeswax.com`).
3. Sessions last 100 hours (30 days with `keep_logged_in`). The adapter requests extended sessions automatically. If issues persist, restart the seller agent to force a fresh login.

**Error: `FreeWheel SH MCP: 401 Unauthorized` or `session expired`**

Streaming Hub OAuth token has expired or credentials are invalid.

1. Verify `FREEWHEEL_SH_USERNAME` and `FREEWHEEL_SH_PASSWORD` are correct.
2. SH tokens are valid for 7 days. The adapter will auto-reconnect on expiry.
3. Rate limit on the token endpoint is 3 req/s — avoid rapid restarts.

---

## SSP Sync Failures

### PubMatic MCP Timeout

**Error: `PubMatic MCP SSE: connection timeout after 30s`**

1. Verify `PUBMATIC_MCP_URL` is set to `https://mcp.pubmatic.com/sses`.
2. Check that `PUBMATIC_API_KEY` is valid and not expired.
3. PubMatic MCP uses Server-Sent Events (SSE). If your load balancer or proxy has a short timeout, increase it to at least 90 seconds.
4. Confirm `pubmatic` is listed in `SSP_CONNECTORS`.

**Error: `PubMatic: deal not found in SSP`**

The deal was created in the seller agent but not yet pushed to PubMatic.

- Use the `/deals` command and look for deals with status `pending_ssp_push`.
- Run: "Push deal [deal-id] to PubMatic" to retry distribution.

---

### Index Exchange API Errors

**Error: `Index Exchange: 403 Forbidden`**

Your API key doesn't have permission for that operation.

1. Verify `INDEX_EXCHANGE_API_KEY` in your `.env`.
2. Contact your Index Exchange account manager to confirm the key has deal management scope.

**Error: `Index Exchange: 422 Unprocessable Entity`**

Deal data failed validation on the Index Exchange side.

- Check the response body for a `errors` field — it will name the invalid field.
- Common causes: floor price below IX minimum, unsupported ad format, missing `seller_id`.

---

## Storage Issues

### SQLite Lock Errors

**Error: `database is locked`**

SQLite allows only one writer at a time. This happens under high concurrency.

1. For development, this is usually safe to ignore — the retry will succeed.
2. For production with concurrent requests, switch to PostgreSQL:
   ```
   DATABASE_URL=postgresql://user:pass@localhost:5432/ad_seller
   STORAGE_TYPE=hybrid
   ```

**Error: `unable to open database file`**

The path in `DATABASE_URL` doesn't exist or isn't writable.

1. Check that the directory exists: `ls -la $(dirname your_db_path)`.
2. Ensure the process user has write permission.

---

### PostgreSQL Pool Exhaustion

**Error: `QueuePool limit of size 10 overflow 10 reached, connection timed out`**

All database connections are in use. This means you're either hitting very high traffic or connections are leaking.

1. Increase pool limits temporarily: `POSTGRES_POOL_MAX=20`.
2. Check for slow queries holding connections open (use `pg_stat_activity`).
3. Verify the seller agent process isn't crashing and creating orphaned connections.

---

### Redis Connection Refused

**Error: `Redis connection refused: localhost:6379`**

Redis isn't running or `REDIS_URL` points to the wrong host.

1. If you intended to use SQLite, set `STORAGE_TYPE=sqlite` and remove or comment out `REDIS_URL`.
2. If you need Redis: `docker run -d -p 6379:6379 redis:7-alpine` for local testing.
3. For production, confirm `REDIS_URL=redis://your-host:6379/0` is correct.

**Error: `Redis: NOAUTH Authentication required`**

Your Redis server requires a password but none was provided.

- Update `REDIS_URL=redis://:yourpassword@your-host:6379/0`.

---

## MCP Connection Problems

### SSE Timeout

**Error: `MCP SSE stream closed unexpectedly`**

The seller agent's MCP endpoint uses Server-Sent Events. Connections must stay open.

1. If behind a proxy (nginx, AWS ALB), set idle timeout to at least 120 seconds.
2. nginx example: `proxy_read_timeout 120s;`
3. AWS ALB: set idle timeout to 120 seconds in target group settings.

---

### Claude Desktop Can't Connect

**Symptom**: Tools don't appear in Claude Desktop; "Failed to connect to MCP server" shown.

1. Confirm the seller agent is running: `curl http://localhost:8000/health`.
2. For local stdio config, check the `claude_desktop_config.json` — the `command` must be on your `PATH`.
3. Open Claude Desktop's MCP log: **Settings > Developer > View Logs**.
4. Look for lines starting with `[seller-agent]` — they will show the startup error.
5. Common cause: missing `ANTHROPIC_API_KEY` in the `env` block of the config.

---

### ChatGPT Connector Errors

**Symptom**: ChatGPT says "Could not connect to seller-agent" when a tool is called.

1. Verify the MCP URL is publicly reachable: `curl https://your-publisher.example.com/mcp` (Streamable HTTP) or `/mcp-sse/sse` (legacy SSE).
2. ChatGPT requires a valid TLS certificate. Self-signed certs are rejected.
3. Confirm your API key is entered correctly in the connector settings (no leading/trailing spaces).
4. Check that `API_KEY_AUTH_ENABLED=true` and the key hasn't expired.

---

## Common HTTP Error Codes

| Code | Likely cause | What to do |
|------|-------------|-----------|
| `400 Bad Request` | Malformed request body or missing required field | Check the response `detail` field for the specific validation error |
| `401 Unauthorized` | Missing or invalid API key | Pass `Authorization: Bearer sk-...` header; verify the key is active |
| `403 Forbidden` | Key exists but lacks permission for this operation | Use a key with operator or admin tier; check `api_key_auth_enabled` |
| `404 Not Found` | Resource (deal, product, order) doesn't exist | Confirm the ID; it may have been deleted or was never created |
| `409 Conflict` | Duplicate resource or state conflict | The deal ID or product ID already exists; use a unique identifier |
| `422 Unprocessable Entity` | Business rule validation failed | Read the `detail` field — it names the failing rule (e.g., price below floor) |
| `429 Too Many Requests` | Rate limit exceeded | Back off and retry; check headers for `Retry-After` |
| `500 Internal Server Error` | Unhandled exception in seller agent | Check server logs immediately; likely a bug or misconfiguration |
| `503 Service Unavailable` | Ad server or SSP is unreachable | Check upstream service health; seller agent will retry automatically |

---

## Logs

### Where to find logs

| Deployment | Log location |
|---|---|
| Local dev (`uvicorn`) | stdout / terminal where you ran the server |
| Docker | `docker logs ad_seller_system-seller_agent-1` |
| Docker Compose | `docker compose logs -f seller_agent` |
| Systemd | `journalctl -u ad-seller-agent -f` |
| AWS ECS | CloudWatch Logs > `/ecs/ad-seller-agent` |

### What to look for

**Startup errors** — look for lines with `ERROR` before the first `Application startup complete` message. These are usually misconfiguration (bad env vars, unreachable services).

**Auth failures** — search for `401` or `403` in the logs to find authentication problems.

**Slow operations** — lines containing `WARNING` and `latency` indicate operations taking longer than expected.

**MCP tool calls** — search for `tool_call` to see which MCP tools are being invoked and their results.

**Deal flow events** — search for `EventBus` to trace the full lifecycle of a deal through the system.

### Increasing log verbosity

Set `CREW_VERBOSE=true` in your `.env` for detailed CrewAI agent reasoning logs. This is verbose — use it for debugging specific issues, not in production.
