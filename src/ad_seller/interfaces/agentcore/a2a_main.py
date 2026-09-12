"""AgentCore A2A runtime entrypoint for the IAB AAMP Seller Agent.

This is the seller's **inbound** A2A (agent-to-agent) server — the piece that
was previously "designed, not implemented" (see ``docs/api/a2a.md`` and the
note in ``interfaces/api/routers/registry.py``). It lets another agent (e.g.
the buyer) talk to this seller with the A2A JSON-RPC 2.0 ``message/send``
contract over natural language, instead of the OpenDirect REST surface.

Wire model
----------
AgentCore has no native ``A2A`` protocol value; an A2A server is just a plain
HTTP server that AgentCore fronts as an ``-p HTTP`` runtime. We therefore run a
minimal Starlette app (Starlette ships with FastAPI, already a dependency) and
select this entrypoint with ``AGENTCORE_MODE=a2a``. The server binds the
AgentCore HTTP contract port (8080).

Routes (match the buyer's ``A2AClient`` expectations —
``{base_url}/a2a/{agent_type}/...``):

- ``GET  /a2a/seller/.well-known/agent-card.json`` — agent discovery card
- ``POST /a2a/seller/jsonrpc``                     — JSON-RPC 2.0 ``message/send``
- ``GET  /ping``                                   — AgentCore health probe

Every ``message/send`` is translated to a ``{"prompt": ...}`` payload and
handed to :func:`ad_seller.interfaces.agentcore.http_main._handle_invocation`
— the SAME routing/crew/chat core the HTTP ``/invocations`` entrypoint uses —
so the A2A surface has identical behavior and tools, just a different wire
contract.

Deploy with::

    agentcore configure -p HTTP -e src/ad_seller/interfaces/agentcore/a2a_main.py ...
    agentcore deploy --env AGENTCORE_MODE=a2a

Local testing::

    AGENTCORE_MODE=a2a python src/ad_seller/interfaces/agentcore/a2a_main.py
    # Agent card: http://localhost:8080/a2a/seller/.well-known/agent-card.json
    # JSON-RPC:   POST http://localhost:8080/a2a/seller/jsonrpc
"""

import asyncio
import logging
import os
import sys
import uuid
from typing import Any

# Add the src directory to Python path so ad_seller is importable.
# We're at src/ad_seller/interfaces/agentcore/a2a_main.py — three levels up to src/
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)

# Environment defaults for AgentCore / workshop demo mode (mirror mcp_main.py).
os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-with-bedrock")
os.environ.setdefault("STORAGE_TYPE", "sqlite")
os.environ.setdefault("AD_SERVER_TYPE", "csv")
os.environ.setdefault("CSV_DATA_DIR", "./data/csv/samples/aws_workshop")

logger = logging.getLogger(__name__)

# A2A protocol constants.
A2A_AGENT_TYPE = "seller"
_A2A_PORT = int(os.environ.get("A2A_PORT", "8080"))

# JSON-RPC 2.0 error codes.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


def _extract_text(params: dict[str, Any]) -> str:
    """Pull the natural-language text out of an A2A ``message/send`` params.

    A2A message shape: ``{"message": {"parts": [{"kind": "text", "text": ...}]}}``.
    Falls back to a bare ``prompt``/``text`` for lenient callers.
    """
    message = params.get("message") or {}
    parts = message.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("kind") == "text" and p.get("text")]
    if texts:
        return "\n".join(texts)
    return params.get("prompt") or params.get("text") or ""


def _result_to_parts(result: Any) -> list[dict[str, Any]]:
    """Convert an ``_handle_invocation`` result into A2A message parts.

    The seller core returns either ``{"response": <text|dict>, "metadata": ...}``
    (chat path) or a structured dict (crew path). We emit a text part for the
    human-readable body and a data part carrying the full structured result so
    the caller can machine-read it.
    """
    parts: list[dict[str, Any]] = []
    text_body: str
    if isinstance(result, dict) and "response" in result:
        response = result["response"]
        text_body = response if isinstance(response, str) else str(response)
    else:
        text_body = result if isinstance(result, str) else ""

    if text_body:
        parts.append({"kind": "text", "text": text_body})
    # Always attach the structured payload as a data part.
    parts.append({"kind": "data", "data": result if isinstance(result, dict) else {"result": result}})
    return parts


async def _handle_message_send(params: dict[str, Any]) -> dict[str, Any]:
    """Translate an A2A ``message/send`` to the shared seller invocation core."""
    from ad_seller.interfaces.agentcore.http_main import _handle_invocation

    prompt = _extract_text(params)
    if not prompt:
        raise ValueError("message/send: no text part found in message")

    # Thread the A2A contextId through as the session id so multi-turn
    # negotiation resumes the correct per-session state on the seller.
    context_id = params.get("contextId") or params.get("context_id")
    payload: dict[str, Any] = {"prompt": prompt}
    if context_id:
        payload["session_id"] = context_id

    result = await _handle_invocation(payload)

    return {
        "taskId": str(uuid.uuid4()),
        "contextId": context_id or str(uuid.uuid4()),
        "status": {"state": "completed"},
        "parts": _result_to_parts(result),
    }


def _build_agent_card() -> dict[str, Any]:
    """Build the A2A discovery card, reusing the REST registry card builder."""
    # Reuse the canonical card so the A2A card and the REST /.well-known card
    # stay in sync; fall back to a minimal card if settings are unavailable.
    try:
        from ad_seller.interfaces.api import deps

        settings = deps._get_api_settings()
        name = settings.seller_agent_name
        url = settings.seller_agent_url
    except Exception:  # pragma: no cover — settings unavailable in exotic envs
        name = "IAB AAMP Seller Agent"
        url = ""

    return {
        "name": name,
        "description": (
            "IAB OpenDirect 2.1 compliant seller agent. Over A2A it accepts "
            "natural-language messages for product discovery, pricing, "
            "proposals, negotiation, and deal execution."
        ),
        "url": url,
        "version": "2.4.2",
        "protocolVersion": "0.3.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "data"],
        "skills": [
            {"id": "discovery", "name": "Inventory Discovery", "tags": ["inventory", "search"]},
            {"id": "pricing", "name": "Tiered Pricing", "tags": ["pricing", "cpm"]},
            {"id": "negotiation", "name": "Deal Negotiation", "tags": ["negotiation", "deals"]},
        ],
    }


def build_app():
    """Build the Starlette A2A app (kept factory-style for unit testing)."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    agent_card_path = f"/a2a/{A2A_AGENT_TYPE}/.well-known/agent-card.json"
    jsonrpc_path = f"/a2a/{A2A_AGENT_TYPE}/jsonrpc"

    async def agent_card(_request: Request) -> JSONResponse:
        return JSONResponse(_build_agent_card())

    async def ping(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy"})

    async def jsonrpc(request: Request) -> JSONResponse:
        # Parse envelope.
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": _PARSE_ERROR, "message": "Parse error"}}
            )

        req_id = body.get("id")
        if body.get("jsonrpc") != "2.0" or "method" not in body:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": _INVALID_REQUEST, "message": "Invalid Request"}}
            )

        method = body["method"]
        if method != "message/send":
            return JSONResponse(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": _METHOD_NOT_FOUND, "message": f"Method not found: {method}"}}
            )

        try:
            result = await _handle_message_send(body.get("params") or {})
        except ValueError as exc:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": _INVALID_REQUEST, "message": str(exc)}}
            )
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC internal error
            logger.exception("A2A message/send failed: %s", exc)
            return JSONResponse(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": _INTERNAL_ERROR, "message": "Internal error", "data": str(exc)}}
            )

        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})

    return Starlette(
        routes=[
            Route(agent_card_path, agent_card, methods=["GET"]),
            Route(jsonrpc_path, jsonrpc, methods=["POST"]),
            Route("/ping", ping, methods=["GET"]),
        ]
    )


def main():
    """Run the A2A server on the AgentCore HTTP contract port (8080)."""
    import uvicorn

    app = build_app()
    logger.info("Starting A2A server on 0.0.0.0:%d (agent_type=%s)", _A2A_PORT, A2A_AGENT_TYPE)
    uvicorn.run(app, host="0.0.0.0", port=_A2A_PORT, log_level="info")


if __name__ == "__main__":
    main()
