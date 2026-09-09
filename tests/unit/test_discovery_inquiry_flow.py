# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Real coverage for DiscoveryInquiryFlow and POST /discovery (issue #60 part 1).

Every test file that touched ad_seller.flows used to stub this module out
before import, citing a "pre-existing @listen() bug with CrewAI version
mismatch" that dated to when crewai was pinned at >=0.86.0. crewai is
>=1.14.4 now, the flow runs cleanly end to end, and DiscoveryInquiryFlow
backs a live endpoint (POST /discovery, products.py:201) -- so the stub
wasn't just hiding dead code, it was hiding a served API path. See issue
#60 for the full investigation.

These tests run the real flow, not a stub.
"""

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.flows.discovery_inquiry_flow import DiscoveryInquiryFlow
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import ProductDefinition


def _make_product(product_id="ctv-premium-sports", inventory_type="ctv"):
    return ProductDefinition(
        product_id=product_id,
        name="Premium CTV - Sports",
        inventory_type=inventory_type,
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )


def _products():
    return {"ctv-premium-sports": _make_product()}


class TestDiscoveryInquiryFlowRouting:
    """The flow's own routing logic, run for real -- no stub in the way."""

    @pytest.mark.parametrize(
        "query, expected_type",
        [
            ("show me what you have", "catalog"),
            ("how much does this cost?", "pricing"),
            ("what CPM for CTV?", "pricing"),
            ("what inventory is available?", "availability"),
            ("how many impressions do you have?", "availability"),
            ("what audience targeting do you support?", "targeting"),
        ],
    )
    def test_query_routes_to_expected_response_type(self, query, expected_type):
        flow = DiscoveryInquiryFlow()
        response = flow.query(query=query, buyer_context=None, products=_products())
        assert flow.state.response_type == expected_type
        assert response is not None

    def test_public_buyer_gets_price_range_not_exact_price(self):
        flow = DiscoveryInquiryFlow()
        response = flow.query(
            query="how much does CTV cost?", buyer_context=None, products=_products()
        )
        assert flow.state.response_type == "pricing"
        assert response is not None

    def test_authenticated_buyer_context_is_accepted(self):
        ctx = BuyerContext(
            identity=BuyerIdentity(agency_id="agency-1", agency_name="Test Agency"),
            is_authenticated=True,
        )
        flow = DiscoveryInquiryFlow()
        response = flow.query(query="what's available?", buyer_context=ctx, products=_products())
        assert flow.state.response_type == "availability"
        assert response is not None

    def test_empty_catalog_does_not_crash(self):
        flow = DiscoveryInquiryFlow()
        response = flow.query(query="show me your catalog", buyer_context=None, products={})
        assert response is not None


class TestDiscoveryEndpointRealFlow:
    """POST /discovery through the real FastAPI app -- the real
    DiscoveryInquiryFlow runs, nothing mocked."""

    @pytest.fixture
    def client(self):
        app.dependency_overrides[_get_optional_api_key_record] = lambda: None
        transport = ASGITransport(app=app)
        c = httpx.AsyncClient(transport=transport, base_url="http://test")
        yield c
        app.dependency_overrides.clear()

    async def test_discovery_query_returns_200(self, client):
        async with client as c:
            resp = await c.post("/discovery", json={"query": "what inventory do you have?"})
        assert resp.status_code == 200, resp.text

    async def test_discovery_pricing_query_returns_200(self, client):
        async with client as c:
            resp = await c.post("/discovery", json={"query": "how much does CTV cost?"})
        assert resp.status_code == 200, resp.text

    async def test_discovery_query_with_buyer_tier_returns_200(self, client):
        async with client as c:
            resp = await c.post(
                "/discovery",
                json={"query": "what's available?", "buyer_tier": "agency", "agency_id": "ag-1"},
            )
        assert resp.status_code == 200, resp.text

    async def test_discovery_missing_query_is_422(self, client):
        async with client as c:
            resp = await c.post("/discovery", json={})
        assert resp.status_code == 422
