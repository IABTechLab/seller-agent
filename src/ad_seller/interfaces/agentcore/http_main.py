"""AgentCore entrypoint for the IAB AAMP Seller Agent.

Uses the BedrockAgentCoreApp wrapper required by Amazon Bedrock AgentCore.
Deploy via the ``agentcore`` CLI — see ``infra/aws/agentcore/deploy.sh``.

Architecture:
- ``crew`` mode runs the full CrewAI PublisherCrew with native Bedrock
  Converse.  ``patches/crewai_bedrock_fix.py`` handles Bedrock Converse API
  compatibility (orphaned toolUse/toolResult sanitization, raw-output type
  coercion, etc.).
- ``chat`` mode routes through ChatInterface — keyword-based, 5 intents,
  ~10 of 41 tools.  Good for deterministic, fast responses.

Routing modes (``ROUTING_MODE`` env var or ``routing_mode`` payload field):
- ``chat`` (default): ChatInterface keyword router.
- ``crew``: PublisherCrew CrewAI hierarchical agents — Inventory Manager +
  channel specialists with LLM reasoning and access to all 41 tools.

Full state management:
- Storage backend (SQLite) for session persistence
- Product catalog loaded on startup via ProductSetupFlow
- ``process_message_async()`` with session-scoped negotiation state
- Buyer identity from ``buyer_tier`` payload field

Local testing::

    pip install bedrock-agentcore
    python src/ad_seller/interfaces/agentcore/http_main.py
    # In another terminal:
    curl -X POST http://localhost:8080/invocations \\
      -H "Content-Type: application/json" \\
      -d '{"prompt": "list products"}'

    # CrewAI mode:
    curl -X POST http://localhost:8080/invocations \\
      -H "Content-Type: application/json" \\
      -d '{"prompt": "list products", "routing_mode": "crew"}'
"""

import asyncio
import json
import logging
import os
import re
import sys

# Add the src directory to Python path so ad_seller is importable
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)

# Environment defaults for AgentCore / workshop demo mode
os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-with-bedrock")
os.environ.setdefault("STORAGE_TYPE", "sqlite")
os.environ.setdefault("AD_SERVER_TYPE", "csv")
os.environ.setdefault("CSV_DATA_DIR", "./data/csv/samples/aws_workshop")
os.environ.setdefault(
    "SELLER_AGENT_URL", f"http://localhost:{os.environ.get('INTERNAL_API_PORT', '8001')}"
)

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# Internal port for FastAPI+MCP background server (for CrewAI tool callbacks)
_INTERNAL_PORT = int(os.environ.get("INTERNAL_API_PORT", "8001"))

# Track whether the background FastAPI server has been started
_fastapi_started = False


def _start_fastapi_background():
    """Start FastAPI+MCP on internal port in a background thread.

    Required for CrewAI mode where tools call back to REST API via httpx.
    Uses uvicorn.Server with a dedicated asyncio event loop in a daemon thread.
    Health check loop: 30 attempts × 0.5s = 15s timeout.

    Idempotent — safe to call multiple times; only starts once.
    """
    global _fastapi_started

    if _fastapi_started:
        return

    import threading
    import time

    import uvicorn

    from ad_seller.interfaces.api.main import app as fastapi_app

    # Set the SELLER_AGENT_URL so tools know where to call
    os.environ["SELLER_AGENT_URL"] = f"http://localhost:{_INTERNAL_PORT}"

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=_INTERNAL_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=_run, daemon=True, name="fastapi-mcp-bg")
    thread.start()

    # Wait for server to be ready
    for _ in range(30):
        try:
            import httpx

            resp = httpx.get(f"http://localhost:{_INTERNAL_PORT}/health", timeout=1.0)
            if resp.status_code == 200:
                logger.info(
                    "FastAPI+MCP background server ready on port %d",
                    _INTERNAL_PORT,
                )
                _fastapi_started = True

                # Create an internal API key for tool calls that require auth
                _create_internal_api_key()

                return
        except Exception:
            time.sleep(0.5)

    logger.error("FastAPI+MCP failed to start on port %d within 15s", _INTERNAL_PORT)
    # Don't sys.exit — let the crew invocation fail gracefully
    raise RuntimeError(f"FastAPI background server failed to start on port {_INTERNAL_PORT}")


# Internal API key for tool calls that require authentication (e.g., create_deal)
_INTERNAL_API_KEY = None


def _create_internal_api_key():
    """Create an internal API key by calling the seller's /auth/api-keys endpoint.

    This key is used by tools like CreateDealTool that call endpoints
    requiring authentication. The key is stored in the module-level
    _INTERNAL_API_KEY variable and in the INTERNAL_API_KEY env var
    so crew_tools.py can access it.
    """
    global _INTERNAL_API_KEY
    import httpx

    try:
        resp = httpx.post(
            f"http://localhost:{_INTERNAL_PORT}/auth/api-keys",
            json={
                "buyer_tier": "preferred_agency",
                "seat_id": "INTERNAL-AGENTCORE",
                "seat_name": "AgentCore Internal",
                "agency_id": "AGY-INTERNAL",
                "agency_name": "AgentCore Runtime",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            _INTERNAL_API_KEY = data.get("api_key", data.get("key", ""))
            if _INTERNAL_API_KEY:
                os.environ["INTERNAL_API_KEY"] = _INTERNAL_API_KEY
                logger.info("Internal API key created for tool auth")
            else:
                logger.warning("API key response missing key field: %s", data)
        else:
            logger.warning(
                "Failed to create internal API key: %d %s", resp.status_code, resp.text[:200]
            )
    except Exception as e:
        logger.warning("Could not create internal API key (non-fatal): %s", e)


# Lazy-initialized shared ChatInterface with storage backend.
# Initialized once on first invocation, then reused for all sessions.
_chat = None
_chat_initialized = False

# Mapping from buyer_tier strings to BuyerContext construction.
_TIER_MAP = {
    "public": {},
    "registered_buyer": {
        "seat_id": "AAMP-BUYER-001",
        "seat_name": "AAMP Buyer Agent",
        "dsp_platform": "aamp",
    },
    "preferred_agency": {
        "seat_id": "AAMP-BUYER-001",
        "seat_name": "AAMP Buyer Agent",
        "dsp_platform": "aamp",
        "agency_id": "AGY-AAMP-001",
        "agency_name": "AAMP Demo Agency",
    },
    "strategic_advertiser": {
        "seat_id": "AAMP-BUYER-001",
        "seat_name": "AAMP Buyer Agent",
        "dsp_platform": "aamp",
        "agency_id": "AGY-AAMP-001",
        "agency_name": "AAMP Demo Agency",
        "advertiser_id": "ADV-AAMP-001",
        "advertiser_name": "AAMP Demo Advertiser",
    },
}


# ---------------------------------------------------------------------------
# Routing mode: "chat" (ChatInterface) or "crew" (PublisherCrew)
# ---------------------------------------------------------------------------
_VALID_ROUTING_MODES = {"chat", "crew"}
_DEFAULT_ROUTING_MODE = os.environ.get("ROUTING_MODE", "chat")


def _get_routing_mode(payload: dict) -> str:
    """Determine routing mode from payload field or ROUTING_MODE env var.

    Priority: payload["routing_mode"] > ROUTING_MODE env var > default ("chat").
    Invalid values fall back to "chat" for backward compatibility.
    """
    mode = payload.get("routing_mode") or os.environ.get("ROUTING_MODE", _DEFAULT_ROUTING_MODE)
    mode = str(mode).strip().lower()
    if mode not in _VALID_ROUTING_MODES:
        logger.warning("Invalid routing mode %r, falling back to 'chat'", mode)
        return _DEFAULT_ROUTING_MODE
    return mode


async def _get_chat():
    """Get or create the ChatInterface with storage backend and loaded products.

    Loads products directly from the CSV adapter instead of running
    ProductSetupFlow (which requires an MCP server connection).
    """
    global _chat, _chat_initialized

    if _chat is not None and _chat_initialized:
        return _chat

    from ad_seller.clients.ad_server_base import get_ad_server_client
    from ad_seller.interfaces.chat.main import ChatInterface
    from ad_seller.storage.factory import get_storage_backend

    # Use the storage backend configured via env vars.
    # --storage sqlite  → STORAGE_TYPE=sqlite (in-memory, default)
    # --storage postgres → STORAGE_TYPE=hybrid + DATABASE_URL + REDIS_URL
    storage_type = os.environ.get("STORAGE_TYPE", "sqlite")
    if storage_type == "sqlite":
        storage = get_storage_backend(storage_type="sqlite", database_url="sqlite:///:memory:")
    else:
        storage = get_storage_backend(
            storage_type=storage_type,
            database_url=os.environ.get("DATABASE_URL"),
            redis_url=os.environ.get("REDIS_URL"),
        )
    await storage.connect()
    logger.info("Storage backend connected: %s", storage_type)
    _chat = ChatInterface(storage=storage)

    # Load products from the configured ad server adapter.
    # AD_SERVER_TYPE env var determines which adapter is used:
    #   csv  → local filesystem (default)
    #   s3   → reads from S3 bucket (no redeploy for data updates)
    try:
        from types import SimpleNamespace

        ad_client = get_ad_server_client()  # Uses AD_SERVER_TYPE from settings
        async with ad_client:
            items = await ad_client.list_inventory()
            for item in items:
                raw = getattr(item, "raw", {}) or {}
                floor = raw.get("floor_price_cpm", 10.0)
                wrapped = SimpleNamespace(
                    id=item.id,
                    name=item.name,
                    base_cpm=floor,
                    floor_cpm=floor * 0.85,
                    inventory_type=raw.get("inventory_type", "display"),
                    raw=raw,
                )
                _chat._products[item.id] = wrapped
        logger.info(
            "Loaded %d products from %s adapter",
            len(_chat._products),
            os.environ.get("AD_SERVER_TYPE", "csv"),
        )
    except Exception as exc:
        logger.warning(
            "Failed to load products from %s: %s", os.environ.get("AD_SERVER_TYPE", "csv"), exc
        )

    _chat_initialized = True
    return _chat


def _build_buyer_context(payload: dict):
    """Build a BuyerContext from the payload's buyer_tier field."""
    tier = payload.get("buyer_tier", "public")
    identity_fields = _TIER_MAP.get(tier)
    if not identity_fields:
        return None

    from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity

    identity = BuyerIdentity(**identity_fields)
    return BuyerContext(
        identity=identity,
        is_authenticated=tier != "public",
        authentication_method="a2a",
        request_type="deal",
    )


def _extract_session_id(payload: dict) -> str | None:
    """Extract session ID from the AgentCore payload."""
    return (
        payload.get("session_id")
        or payload.get("runtimeSessionId")
        or payload.get("session_metadata", {}).get("session_id")
    )


# ---------------------------------------------------------------------------
# Structured output formatting for CrewAI responses
# ---------------------------------------------------------------------------

# Patterns for extracting structured data from crew output text
_DEAL_ID_PATTERN = re.compile(r"DEAL-[\w-]+", re.IGNORECASE)
_CPM_PATTERN = re.compile(r"\$?([\d]+(?:\.[\d]{1,2})?)\s*(?:CPM|cpm)", re.IGNORECASE)
_BUDGET_PATTERN = re.compile(r"\$?([\d,]+(?:\.[\d]{1,2})?)\s*(?:budget|total)", re.IGNORECASE)


def _format_crew_output(crew_output) -> dict:
    """Parse CrewOutput into a JSON-serializable dict with visualization tags.

    Extracts structured data (deal IDs, pricing, inventory lists) from the
    CrewOutput and wraps relevant sections in ``<visualization-data>`` tags
    for UI rendering.

    Args:
        crew_output: A CrewAI ``CrewOutput`` object.

    Returns:
        A dict with ``response`` (text) and ``metadata`` fields.
    """
    # Extract the raw text response
    raw_text = getattr(crew_output, "raw", "") or ""

    # Try to get structured data from json_dict or pydantic output
    structured_data = None
    if getattr(crew_output, "json_dict", None):
        structured_data = crew_output.json_dict
    elif getattr(crew_output, "pydantic", None):
        try:
            structured_data = crew_output.pydantic.model_dump()
        except Exception:
            pass

    # Extract deal IDs, pricing, and budget from the raw text
    deal_ids = _DEAL_ID_PATTERN.findall(raw_text)
    cpm_values = _CPM_PATTERN.findall(raw_text)
    budget_values = _BUDGET_PATTERN.findall(raw_text)

    metadata = {
        "type": "seller_response",
        "routing_mode": "crew",
    }

    # Build visualization data if we found structured info
    viz_data = {}
    if deal_ids:
        viz_data["deal_ids"] = deal_ids
        metadata["deal_ids"] = deal_ids
    if cpm_values:
        viz_data["cpm_values"] = [float(v) for v in cpm_values]
    if budget_values:
        viz_data["budget_values"] = [v.replace(",", "") for v in budget_values]
    if structured_data:
        viz_data["structured_output"] = structured_data

    # Build the response text with visualization tags where applicable
    response_text = raw_text
    if viz_data:
        viz_json = json.dumps(viz_data, default=str)
        response_text = f"{raw_text}\n\n<visualization-data>{viz_json}</visualization-data>"

    return {
        "response": response_text,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# CrewAI routing path — full PublisherCrew with native Bedrock Converse
# ---------------------------------------------------------------------------


def _is_deal_request(prompt: str) -> bool:
    """Lightweight check: does the prompt ask for deal creation/booking?

    Used to select a deal-specific task description that gives the LLM
    explicit authorization to execute the write tool. No deterministic
    fallback — the crew still does all the work.
    """
    lower = prompt.lower()
    deal_signals = [
        "create a deal",
        "create deal",
        "create two",
        "create both",
        "book a deal",
        "book the deal",
        "book deal",
        "book deals",
        "approve and book",
        "generate deal id",
        "generate deal",
        "preferred deal",
        "private auction",
        "programmatic guaranteed",
    ]
    has_product = bool(re.search(r"inv-\w+-\w+", lower))
    has_deal_keyword = any(kw in lower for kw in deal_signals)
    return has_product and has_deal_keyword


async def _run_crew_with_crewai(prompt: str, payload: dict) -> dict:
    """Run the full CrewAI PublisherCrew with native Bedrock Converse.

    Bedrock Converse API compatibility patches are applied via the
    ``patches.crewai_bedrock_fix`` module (orphaned toolUse/toolResult
    sanitization, raw-output type coercion, etc.).

    Deal creation is fully agentic — the crew is given explicit
    authorization and a deal-specific task description when the prompt
    asks for deals. No deterministic Python fallback.
    """
    from crewai import LLM, Crew, Process, Task

    from ad_seller.crews.publisher_crew import PublisherCrew

    # Apply Bedrock Converse compatibility patches
    try:
        from patches.crewai_bedrock_fix import apply_patches

        apply_patches()
    except ImportError:
        logger.warning("patches.crewai_bedrock_fix not available — skipping")

    # Apply AgentCore memory patch (read_only mode — no RememberTool injection)
    if os.environ.get("CREW_MEMORY_ENABLED", "false").lower() == "true":
        try:
            from patches.crewai_agentcore_memory import apply_patches as apply_memory_patches

            _session = payload.get("session_id", payload.get("runtimeSessionId", ""))
            apply_memory_patches(session_id=_session or None, actor_id="seller-agent")
        except ImportError:
            logger.warning("patches.crewai_agentcore_memory not available — skipping")
        except Exception as e:
            logger.warning(f"AgentCore memory patch failed: {e}")

    publisher_crew = PublisherCrew()

    bedrock_model = os.environ.get(
        "DEFAULT_LLM_MODEL",
        "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    )
    bedrock_llm = LLM(model=bedrock_model, temperature=0.3, max_tokens=4096)
    publisher_crew.inventory_manager.llm = bedrock_llm
    publisher_crew.inventory_manager.memory = False

    # ── Tools: MCP tools for reads + BaseTool CreateDealTool for writes ──
    # MCP tools (via SSE) give the crew access to list_products, get_pricing,
    # discover_inventory, etc. But the MCP create_deal_from_template has a
    # terse description and requires auth headers the MCP adapter doesn't send.
    # CreateDealTool (BaseTool) bypasses auth via direct in-process fallback
    # and has a rich description that gives the LLM confidence to execute.
    default_read_tools = (
        "list_products,get_product_details,get_pricing,"
        "discover_inventory,get_rate_card,search_media_kit,get_deal_performance"
    )
    tool_names = os.environ.get("CREW_MCP_TOOLS", default_read_tools).split(",")
    tool_names = [t.strip() for t in tool_names if t.strip()]
    # Exclude create_deal_from_template from MCP — we use the BaseTool instead
    tool_names = [t for t in tool_names if t != "create_deal_from_template"]

    from crewai_tools import MCPServerAdapter

    mcp_adapter = MCPServerAdapter(
        {"url": f"http://localhost:{_INTERNAL_PORT}/mcp-sse/sse", "transport": "sse"},
        *tool_names,
        connect_timeout=30,
    )

    # Combine MCP read tools + BaseTool CreateDealTool
    from .crew_tools import CreateDealTool

    all_tools = list(mcp_adapter.tools) + [CreateDealTool()]
    publisher_crew.inventory_manager.tools = all_tools
    logger.info(
        "Loaded %d MCP tools + CreateDealTool from localhost:%d",
        len(mcp_adapter.tools),
        _INTERNAL_PORT,
    )

    # ── Agent backstory: authorize ALL operations including deal creation ──
    publisher_crew.inventory_manager.backstory += """

CRITICAL INSTRUCTIONS — READ CAREFULLY:

1. You are the Inventory Manager for Meridian Media Group. You have FULL
   AUTHORITY to create deals, set pricing, and book inventory. This is your
   core job — deal creation is a routine operation, not a risky action.

2. You have tools that access REAL inventory data from the database.
   ALWAYS call your tools. NEVER make up product IDs, pricing, or deal terms.

3. When a buyer asks you to CREATE A DEAL or BOOK A DEAL:
   - You MUST call the create_deal tool immediately with the parameters
     from the request (product_id, deal_type, max_cpm, impressions).
   - Do NOT just describe what you would do. Actually CALL the tool.
   - Do NOT say "authentication required" or "please contact us" — you
     are already authenticated with full write access.
   - The tool returns a JSON object with a DEAL-XXXXXXXX ID. Include
     that Deal ID in your response.

4. For multiple deals in one request, call create_deal once per product.

5. If a tool call fails, report the actual error. Do not fabricate a response."""

    # ── Task: use a deal-specific description when the prompt asks for deals ──
    is_deal = _is_deal_request(prompt)

    if is_deal:
        task_description = f"""Execute the following deal creation request:

{prompt}

INSTRUCTIONS:
1. For EACH product mentioned, call the create_deal tool with:
   - product_id: the inv-xxx-xxx ID from the request
   - deal_type: PG, PD, or PA as specified (default PD if not specified)
   - max_cpm: the CPM price from the request (as a float)
   - impressions: the impression count from the request (as an integer,
     e.g. "3M" = 3000000)
2. You MUST actually call the tool — do not just describe the deal.
3. After each tool call, include the returned Deal ID in your response.
4. Format the response with deal details: Deal ID, product, CPM, impressions,
   total cost, and DSP activation instructions."""

        task_expected = """A response containing one or more Deal IDs (format: DEAL-XXXXXXXX)
with deal details including product name, CPM, impressions, total cost,
and DSP activation instructions. Each deal must have a real Deal ID
from the create_deal tool call."""
    else:
        task_description = f"""Process the following request from a buyer or user:

{prompt}

Use your available tools to access REAL inventory data from the database.
Choose the tool that best matches the request. Call the tool, then write
your response using the actual data returned.

Do NOT make up product IDs, pricing, or deal terms. Use only data from tool results."""

        task_expected = """A text response with real data from the tool call.
Include product IDs, names, inventory types, CPM pricing, and deal terms.
Format as markdown with headers and tables where appropriate."""

    general_task = Task(
        description=task_description,
        expected_output=task_expected,
        agent=publisher_crew.inventory_manager,
    )

    crew = Crew(
        agents=[publisher_crew.inventory_manager],
        tasks=[general_task],
        process=Process.sequential,
        verbose=publisher_crew._settings.crew_verbose,
        memory=False,
    )

    # max_iter: use env var or CrewAI default (no artificial limit).
    # Previously set to 3 as a workaround for Bedrock Converse bug (now patched).
    max_iter = int(os.environ.get("CREW_MAX_ITER", "0"))
    if max_iter > 0:
        publisher_crew.inventory_manager.max_iter = max_iter

    import concurrent.futures

    loop = asyncio.get_event_loop()

    try:
        from typing import Union

        from crewai.crews.crew_output import CrewOutput
        from crewai.tasks.task_output import TaskOutput

        for cls in [TaskOutput, CrewOutput]:
            if not getattr(cls, "_bedrock_raw_patched", False):
                cls.model_fields["raw"].annotation = Union[str, list]
                cls.model_rebuild(force=True)
                cls._bedrock_raw_patched = True
    except Exception as patch_err:
        logger.warning(f"Failed to patch TaskOutput: {patch_err}")

    with concurrent.futures.ThreadPoolExecutor() as pool:
        crew_output = await loop.run_in_executor(pool, crew.kickoff)

    if hasattr(crew_output, "raw") and isinstance(crew_output.raw, list):
        texts = []
        for block in crew_output.raw:
            if isinstance(block, dict):
                if "text" in block:
                    texts.append(block["text"])
                elif "toolUse" in block:
                    texts.append(f"[Tool: {block['toolUse'].get('name', '?')}]")
                else:
                    texts.append(json.dumps(block))
            else:
                texts.append(str(block))
        crew_output.raw = "\n".join(texts)

    return _format_crew_output(crew_output)


# ---------------------------------------------------------------------------
# Main invocation handler
# ---------------------------------------------------------------------------


async def _handle_invocation(payload: dict):
    """Async handler — routes to ChatInterface or CrewAI based on routing mode."""
    routing_mode = _get_routing_mode(payload)

    # UI sends payloads with agent_name/memory_id but no routing_mode.
    # Default to crew for UI calls so they get real data from MCP tools.
    if routing_mode == "chat" and not payload.get("routing_mode"):
        if (
            payload.get("agent_name")
            or payload.get("memory_id")
            or payload.get("direct_mention_target")
        ):
            routing_mode = "crew"
            logger.info("Auto-routing to crew mode (UI payload detected)")

    # CrewAI path — full PublisherCrew with Bedrock Converse patches
    if routing_mode == "crew":
        _start_fastapi_background()
        prompt = payload.get("prompt") or payload.get("message") or payload.get("input", "")
        if not prompt:
            return {"error": "Missing 'prompt', 'message', or 'input' field"}

        # Try CrewAI crew first
        result = await _run_crew_with_crewai(prompt, payload)

        return result

    # Chat path (default) — keyword-based ChatInterface
    prompt = payload.get("prompt") or payload.get("message") or payload.get("input", "")
    if not prompt:
        return {"error": "Missing 'prompt', 'message', or 'input' field"}

    session_id = _extract_session_id(payload)
    buyer_context = _build_buyer_context(payload)

    if session_id:
        logger.info("Session: %s — prompt: %s", session_id, prompt[:80])

    chat = await _get_chat()

    # Use the async session-aware path if we have a session ID.
    # This gives us NegotiationState tracking, product context, and persistence.
    if session_id:
        # Start or resume session
        try:
            session = await chat.resume_session(session_id)
        except Exception:
            session = await chat.start_session(buyer_context=buyer_context)
            # Override the auto-generated session ID with the buyer's
            session.session_id = session_id

        result = await chat.process_message_async(
            prompt,
            buyer_context=buyer_context,
            session_id=session_id,
        )
    else:
        # Fallback: sync path for local testing without session
        result = chat.process_message(prompt, buyer_context=buyer_context)

    return {
        "response": result,
        "metadata": {
            "type": "seller_response",
            "session_id": session_id,
        },
    }


@app.entrypoint
def invoke(payload, context):
    """Handle an AgentCore invocation.

    Bridges the sync ``@app.entrypoint`` to the async seller code via
    ``asyncio.run()``.  This enables full state management: storage backend,
    product catalog, and session-scoped negotiation state.
    """
    try:
        return asyncio.run(_handle_invocation(payload))
    except RuntimeError:
        # If an event loop is already running (e.g. nested async),
        # create a new loop in a thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _handle_invocation(payload))
            return future.result(timeout=120)
    except Exception as exc:
        logger.exception("Invocation failed: %s", exc)
        return {"error": "Invocation failed", "detail": str(exc)}


if __name__ == "__main__":
    # For local testing, pre-start the background FastAPI server
    # so crew mode tools have an endpoint immediately.
    # In production (AgentCore), it starts lazily on first crew request.
    _start_fastapi_background()
    app.run()
