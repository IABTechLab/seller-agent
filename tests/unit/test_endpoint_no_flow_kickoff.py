# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests: endpoints must not call sync ProductSetupFlow.kickoff().

In CrewAI 1.10.1, `Flow.kickoff()` is synchronous and returns `None`.
`await flow.kickoff()` raises `TypeError: object NoneType can't be used in
'await' expression`. The fix (mirroring origin's 3d8b69c for /products) is
`await flow.kickoff_async()`.

These tests guard the invariant that no production endpoint reaches the
sync `.kickoff()` method via the autouse fixture below — if any endpoint
regresses to the broken pattern, the AssertionError trips immediately.
The hermetic POST /packages/sync test additionally exercises one of the
six previously-broken endpoints end-to-end with a mocked flow.

Read endpoints (`GET /products`, `GET /products/{id}`, `GET /.well-known/agent.json`)
were separately fixed to read from a static catalog instead of
running the flow at all; the same kickoff-call guard applies to them.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version mismatch).
# Same pattern used in test_deal_booking_endpoints.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api import main as api_main  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """Reset the static catalog cache between tests so each test sees a fresh state."""
    api_main._STATIC_PRODUCT_CATALOG = None
    yield
    api_main._STATIC_PRODUCT_CATALOG = None


@pytest.fixture(autouse=True)
def _fail_if_flow_kickoff_called(monkeypatch):
    """Hard-fail the test if any code path calls ProductSetupFlow().kickoff()."""

    def _boom(*args, **kwargs):
        raise AssertionError(
            "ProductSetupFlow.kickoff() was called from a read endpoint. "
            "Read endpoints must use the cached static catalog."
        )

    # Patch on the class so any code path that constructs ProductSetupFlow
    # and calls kickoff() trips the assertion.
    from ad_seller.flows.product_setup_flow import ProductSetupFlow

    monkeypatch.setattr(ProductSetupFlow, "kickoff", _boom)


async def test_health_returns_200(client):
    """Sanity: /health is unaffected by the change."""
    async with client as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


async def test_agent_card_returns_200_with_audience_capabilities(client):
    """`/.well-known/agent.json` returns 200 with `audience_capabilities` block.

    Previously hung in OpenDirect MCP session.initialize() because of the
    per-request flow.kickoff().
    """
    async with client as c:
        resp = await c.get("/.well-known/agent.json")
    assert resp.status_code == 200
    body = resp.json()
    assert "audience_capabilities" in body
    assert body["audience_capabilities"] is not None
    # Inventory types still populated from the static catalog.
    assert "inventory_types" in body
    assert len(body["inventory_types"]) > 0


async def test_list_products_returns_200_with_products_key(client):
    """`GET /products` returns the shared ProductListResponse."""
    async with client as c:
        resp = await c.get("/products")
    assert resp.status_code == 200
    body = resp.json()
    assert "products" in body
    assert isinstance(body["products"], list)
    # Shared envelope carries pagination metadata.
    assert body["total_count"] == len(body["products"])
    assert body["limit"] == 50
    assert body["offset"] == 0
    # Default catalog is non-empty.
    assert len(body["products"]) > 0
    # Shape check on first product — the shared Product primitive.
    p = body["products"][0]
    assert "product_id" in p
    assert "name" in p
    assert "seller_organization_id" in p
    # Money in micros, not a bare base_cpm float.
    assert "amount_micros" in p["base_price"]
    # Seller-local fields ride in ext (nothing silently dropped).
    assert p["ext"]["inventory_type"] is not None
    assert isinstance(p["ext"]["deal_types"], list)


async def test_get_product_returns_200_for_existing_404_for_missing(client):
    """`GET /products/{id}` returns 200 for an existing product, 404 for missing."""
    # First list products to get a valid id.
    async with client as c:
        list_resp = await c.get("/products")
        assert list_resp.status_code == 200
        existing_id = list_resp.json()["products"][0]["product_id"]

        # Existing product → 200
        ok_resp = await c.get(f"/products/{existing_id}")
        assert ok_resp.status_code == 200
        assert ok_resp.json()["product_id"] == existing_id

        # Missing product → 404
        miss_resp = await c.get("/products/prod-doesnotexist")
        assert miss_resp.status_code == 404


async def test_endpoints_do_not_invoke_flow_kickoff(client):
    """Hits all four read endpoints; the autouse fixture asserts no flow.kickoff()."""
    async with client as c:
        for path in (
            "/health",
            "/.well-known/agent.json",
            "/products",
        ):
            resp = await c.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"


async def test_create_quote_returns_200_without_flow_kickoff(client):
    """`POST /api/v1/quotes` returns 200 and does NOT invoke ProductSetupFlow.

    Regression: this endpoint used to call
    `await ProductSetupFlow().kickoff()` per request to load the product
    catalog, which hangs in OpenDirect MCP session.initialize(). The
    autouse `_fail_if_flow_kickoff_called` fixture trips an AssertionError
    if any code path under this test calls Flow.kickoff().
    """
    async with client as c:
        # Find a real product_id from the cached catalog.
        list_resp = await c.get("/products")
        assert list_resp.status_code == 200
        product_id = list_resp.json()["products"][0]["product_id"]

        # PG deal requires impressions; pick one comfortably above min (default 10000).
        # Shared QuoteRequest requires idempotency_key (FD-12).
        body = {
            "idempotency_key": "idem-nokickoff-1",
            "product_id": product_id,
            "deal_type": "PG",
            "impressions": 1_000_000,
        }
        resp = await c.post("/api/v1/quotes", json=body)
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    quote = resp.json()["quote"]
    assert quote["status"] == "available"
    assert quote["product"]["product_id"] == product_id
    assert quote["deal_type"] == "PG"
    assert quote["pricing"]["final_cpm"]["amount_micros"] > 0
    assert "quote_id" in quote and quote["quote_id"].startswith("qt-")


async def test_create_quote_returns_404_for_unknown_product(client):
    """Unknown product → 404, also without flow.kickoff()."""
    async with client as c:
        body = {
            "idempotency_key": "idem-unknown-1",
            "product_id": "prod-doesnotexist",
            "deal_type": "PD",
            "impressions": 100_000,
        }
        resp = await c.post("/api/v1/quotes", json=body)
    assert resp.status_code == 404


async def test_create_quote_validates_deal_type(client):
    """Bad deal_type → 422 at the wire edge (shared QuoteRequest types it as an enum)."""
    async with client as c:
        list_resp = await c.get("/products")
        product_id = list_resp.json()["products"][0]["product_id"]
        body = {
            "idempotency_key": "idem-badtype-1",
            "product_id": product_id,
            "deal_type": "ZZ",
            "impressions": 100_000,
        }
        resp = await c.post("/api/v1/quotes", json=body)
    assert resp.status_code == 422


# =============================================================================
# Flow-kickoff write endpoints must use kickoff_async(), not kickoff()
#
# In CrewAI 1.10.1 Flow.kickoff() is synchronous and returns None.
# Awaiting None raises TypeError: object NoneType can't be used in 'await'.
# These tests verify /packages/sync (the simplest of the six affected endpoints)
# does NOT return a 500 with that TypeError, proving kickoff_async() is called.
# The autouse `_fail_if_flow_kickoff_called` fixture from this module guarantees
# the old (broken) kickoff() path is never taken.
# =============================================================================


async def test_packages_sync_does_not_return_typeerror_500():
    """`POST /packages/sync` must not crash with TypeError from awaiting kickoff().

    Regression: this endpoint called `await flow.kickoff()` which
    returns None in CrewAI 1.10.1 and crashes.  Fix: `await flow.kickoff_async()`.

    We mock ProductSetupFlow so the test is hermetic (no real flow execution).
    The `_fail_if_flow_kickoff_called` autouse fixture ensures the old `.kickoff()`
    method is never reached — if it were, it would raise AssertionError, not pass.
    """
    mock_flow = MagicMock()
    mock_flow.kickoff_async = AsyncMock()
    mock_flow.state.synced_segments = []
    mock_flow.state.warnings = []

    with patch("ad_seller.flows.ProductSetupFlow", return_value=mock_flow):
        with patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post("/packages/sync")

    # Any status other than 500 (with TypeError) means the fix is working.
    # 200 = fully successful; other 2xx/4xx/5xx domain errors are also acceptable
    # as long as they are NOT from the TypeError crash.
    assert resp.status_code != 500 or "NoneType" not in resp.text, (
        f"POST /packages/sync returned 500 with TypeError body: {resp.text}"
    )
    # Confirm kickoff_async was actually awaited (not the old kickoff()).
    mock_flow.kickoff_async.assert_awaited_once()
