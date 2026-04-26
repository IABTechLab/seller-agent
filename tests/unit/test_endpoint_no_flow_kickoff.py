# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests for ar-uwad: read endpoints must not run ProductSetupFlow.

`GET /products`, `GET /products/{id}`, and `GET /.well-known/agent.json`
used to call `await ProductSetupFlow().kickoff()` per request, which hangs
in OpenDirect MCP `session.initialize()`. These tests verify those
endpoints return 200 quickly without ever touching the flow.
"""

import sys
from types import ModuleType

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
            "Read endpoints must use the cached static catalog (see ar-uwad)."
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
    """`GET /products` returns 200 with a `products` list."""
    async with client as c:
        resp = await c.get("/products")
    assert resp.status_code == 200
    body = resp.json()
    assert "products" in body
    assert isinstance(body["products"], list)
    # Default catalog is non-empty.
    assert len(body["products"]) > 0
    # Shape check on first product.
    p = body["products"][0]
    assert "product_id" in p
    assert "name" in p
    assert "inventory_type" in p
    assert "base_cpm" in p
    assert "deal_types" in p


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
