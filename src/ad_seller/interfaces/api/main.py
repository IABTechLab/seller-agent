# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""REST API interface for programmatic access — app assembly.

As of EP-3.1 this module only assembles the FastAPI app: middleware,
lifespan (inventory sync + MCP mounting), and router mounting. Endpoint
logic lives in ``interfaces/api/routers/`` (thin routers) and
``ad_seller/services/`` (business logic); request/response models live in
``interfaces/api/schemas.py``.

A small compatibility surface is re-exported at the bottom of this module
because tests (and possibly external callers) import/patch names from
``ad_seller.interfaces.api.main`` directly.
"""

import logging
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

logger = logging.getLogger(__name__)

# Wire-format media types accepted on `audience_plan`-bearing requests per
# proposal §5.6 + wire-format spec §8 (docs/api/audience_plan_wire_format.md).
# FastAPI's default body parsing reads JSON regardless of `Content-Type`, so
# both names parse cleanly without custom dependencies; the constants are
# kept here for documentation, header-echo on responses, and explicit test
# coverage of the dual-name acceptance contract.
_UCP_CONTENT_TYPE = "application/vnd.ucp.embedding+json; v=1"
_AGENTIC_AUDIENCES_CONTENT_TYPE = "application/vnd.iab.agentic-audiences+json; v=1"
_AUDIENCE_PLAN_CONTENT_TYPES = frozenset({_UCP_CONTENT_TYPE, _AGENTIC_AUDIENCES_CONTENT_TYPE})

app = FastAPI(
    title="Ad Seller System API",
    description=(
        "IAB OpenDirect 2.1 compliant seller agent for programmatic advertising. "
        "Supports product discovery, tiered pricing, proposal evaluation, "
        "multi-round negotiation, deal execution, order management, and change requests."
    ),
    version="1.0.0",
    contact={"name": "IAB Tech Lab", "url": "https://iabtechlab.com"},
    license_info={"name": "Apache 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
    root_path_in_servers=False,
    openapi_tags=[
        {"name": "Core", "description": "Health check and API root"},
        {"name": "Products", "description": "Product catalog browsing"},
        {"name": "Pricing", "description": "Tiered pricing with buyer context"},
        {"name": "Proposals", "description": "Proposal submission and evaluation"},
        {"name": "Deals", "description": "Deal generation from accepted proposals"},
        {"name": "Discovery", "description": "Natural language inventory discovery"},
        {"name": "Events", "description": "Event bus log inspection"},
        {"name": "Approvals", "description": "Human-in-the-loop approval workflow"},
        {"name": "Sessions", "description": "Multi-turn buyer conversation sessions"},
        {"name": "Negotiation", "description": "Multi-round price negotiation"},
        {"name": "Media Kit", "description": "Public media kit and package catalog"},
        {"name": "Packages", "description": "Package management (authenticated/admin)"},
        {"name": "Authentication", "description": "API key lifecycle management"},
        {"name": "Agent Registry", "description": "A2A agent discovery and trust management"},
        {"name": "Quotes", "description": "Non-binding price quotes (IAB Deals API v1.0)"},
        {"name": "Deal Booking", "description": "Quote-to-deal booking (IAB Deals API v1.0)"},
        {"name": "Orders", "description": "Order state machine and lifecycle management"},
        {"name": "Change Requests", "description": "Post-deal modification requests"},
        {"name": "Audit", "description": "Order audit logs and operational reports"},
        {
            "name": "Supply Chain",
            "description": "Supply chain transparency (sellers.json-like self-description)",
        },
        {"name": "Deal Performance", "description": "Deal delivery and performance metrics"},
        {"name": "Bulk Operations", "description": "Batch deal create/update/cancel"},
    ],
)


# =============================================================================
# Middleware
# =============================================================================

# Trust X-Forwarded-Proto / X-Forwarded-For from Cloud Run so that Starlette
# generates https:// redirects instead of http:// ones behind the TLS proxy.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Allow all browser-based clients — buyer UIs, claude.ai, SSP dashboards, etc.
# The MCP Streamable HTTP protocol requires CORS for browser-originated requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
    expose_headers=["*"],
)


# =============================================================================
# Lifecycle: start/stop background services
# =============================================================================

_mcp_server_ref = None


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(application):
    """Manage app lifecycle — inventory sync + MCP session manager."""
    from ...services.inventory_sync_scheduler import start_sync_scheduler, stop_sync_scheduler

    start_sync_scheduler()

    # Mount MCP server with both transports:
    # - Streamable HTTP at /mcp (current MCP standard, protocol 2025-06-18)
    # - HTTP+SSE at /mcp-sse (deprecated, kept for backwards compat)
    # Starlette doesn't call mounted sub-app lifespans, so we must run the
    # session manager ourselves to keep its task group alive.
    global _mcp_server_ref
    try:
        from ..mcp_server import mcp as mcp_server

        _mcp_server_ref = mcp_server
        application.mount("/mcp", mcp_server.streamable_http_app())
        application.mount("/mcp-sse", mcp_server.sse_app())
        logger.info("MCP server mounted: Streamable HTTP at /mcp, legacy SSE at /mcp-sse/sse")

        async with mcp_server.session_manager.run():
            yield
    except Exception as e:
        logger.warning("MCP server not mounted: %s", e)
        yield
    finally:
        stop_sync_scheduler()


app.router.lifespan_context = lifespan


# =============================================================================
# Compatibility surface (import/patch targets kept stable across EP-3.1)
# =============================================================================

# Same function objects as the routers use in Depends(...), so existing
# `app.dependency_overrides[_get_optional_api_key_record]` test hooks work.
# Agentic-match scoring helpers moved to services.deal_service; tests
# import the underscore-prefixed names from this module.
from ...services.deal_service import (  # noqa: E402
    agentic_match_quality as _agentic_match_quality,  # noqa: F401
)
from ...services.deal_service import (  # noqa: E402
    booking_logger,  # noqa: F401
)
from ...services.deal_service import (  # noqa: E402
    deterministic_score as _deterministic_score,  # noqa: F401
)
from .deps import (  # noqa: E402,F401
    _build_buyer_context,
    _get_api_settings,
    _get_media_kit_service,
    _get_optional_api_key_record,
    _get_registry_service,
    _resolve_and_enforce_agent,
)

# Request/response models were moved to schemas.py; re-exported for
# backward compatibility with external importers.
from .schemas import *  # noqa: E402,F401,F403

# Cached static product catalog compat surface. The single catalog source
# lives in services.catalog_service, but tests patch
# `ad_seller.interfaces.api.main._get_static_product_catalog` and reset
# `_STATIC_PRODUCT_CATALOG` between tests — so this module keeps the
# patchable delegator, and endpoint code resolves the catalog through it
# at call time (see deps.get_product_catalog).
_STATIC_PRODUCT_CATALOG: Optional[dict[str, Any]] = None


def _get_static_product_catalog() -> dict[str, Any]:
    """Return the seller's default product catalog without running the flow.

    Delegates to ``services.catalog_service`` (the single catalog source).
    """
    global _STATIC_PRODUCT_CATALOG
    if _STATIC_PRODUCT_CATALOG is None:
        from ...services import catalog_service

        # Honor legacy reset semantics: clearing _STATIC_PRODUCT_CATALOG
        # means "give me a fresh catalog", so drop the service cache too.
        catalog_service.reset_catalog_cache()
        _STATIC_PRODUCT_CATALOG = catalog_service.get_static_product_catalog()
    return _STATIC_PRODUCT_CATALOG


def _serialize_product(product: Any) -> dict[str, Any]:
    """Serialize a ProductDefinition to the public JSON shape."""
    from ...services.catalog_service import serialize_product

    return serialize_product(product)


# =============================================================================
# Router mounting
# =============================================================================

from fastapi.routing import APIRoute  # noqa: E402

from .routers import ALL_ROUTERS  # noqa: E402

# Mount routers EAGERLY (route objects appended directly to app.router.routes)
# instead of via app.include_router(). FastAPI >= 0.13x includes routers
# lazily through `_IncludedRouter` placeholders, which would change what
# `app.routes` contains — existing tests (e.g. test_auth_header_binding)
# and tooling introspect `app.routes` expecting materialized APIRoute
# objects, exactly as when every endpoint was decorated with @app directly.
# Routers use no prefixes/extra dependencies, so appending is equivalent.
for _router in ALL_ROUTERS:
    for _route in _router.routes:
        if isinstance(_route, APIRoute):
            # Wire dependency overrides to the app so
            # `app.dependency_overrides[...]` test hooks keep working.
            _route.dependency_overrides_provider = app
        app.router.routes.append(_route)

if hasattr(app.router, "_mark_routes_changed"):
    app.router._mark_routes_changed()
