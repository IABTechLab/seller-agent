"""Discovery, packages, media-kit search and sessions verify the buyer tier.

``POST /discovery`` used to build its buyer context straight from the body,
so a keyless request claiming ``buyer_tier="agency"`` with any ``agency_id``
was served exact agency CPMs instead of the public price ranges. Every
price-revealing read now goes through ``deps._verified_buyer_context``:
self-asserted identity floors to PUBLIC unless a seller-issued API key (or a
registry-verified agent) backs it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.buyer_identity import BuyerIdentity
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import ProductDefinition


def _catalog():
    product = ProductDefinition(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )
    return {"products": {product.product_id: product}, "inventory_types": ["ctv"]}


def _agency_key_record():
    record = MagicMock()
    record.identity = BuyerIdentity(agency_id="agency-verified", agency_name="Verified Agency")
    return record


@pytest.fixture
def client_factory():
    clients = []

    def _make(api_key_record=None):
        app.dependency_overrides[_get_optional_api_key_record] = lambda: api_key_record
        c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield _make
    app.dependency_overrides.clear()


async def _discover(client):
    with patch(
        "ad_seller.interfaces.api.main._get_static_product_catalog",
        new=AsyncMock(return_value=_catalog()),
    ):
        async with client as c:
            return await c.post(
                "/discovery",
                json={
                    "query": "how much does CTV cost?",
                    "buyer_tier": "agency",
                    "agency_id": "agency-made-up",
                },
            )


class TestDiscoveryTierVerification:
    async def test_keyless_agency_claim_gets_public_ranges(self, client_factory):
        resp = await _discover(client_factory(None))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["access_tier"] == "public"
        product = data["pricing"]["products"][0]
        assert "price" not in product
        assert product["price_range"]

    async def test_keyed_agency_gets_agency_view(self, client_factory):
        resp = await _discover(client_factory(_agency_key_record()))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["access_tier"] == "agency"
        product = data["pricing"]["products"][0]
        assert product["price"] == pytest.approx(35.0 * 0.9)
        assert "price_range" not in product


class TestSessionTierVerification:
    async def test_keyless_identity_claim_creates_public_session(self, client_factory):
        captured = {}

        async def _fake_create_session(buyer_context):
            captured["ctx"] = buyer_context
            return {"session_id": "s-1"}

        with patch(
            "ad_seller.services.session_service.create_session",
            new=_fake_create_session,
        ):
            async with client_factory(None) as c:
                resp = await c.post(
                    "/sessions",
                    json={"agency_id": "agency-made-up", "advertiser_id": "adv-made-up"},
                )

        assert resp.status_code == 200, resp.text
        assert captured["ctx"].effective_tier.value == "public"

    async def test_keyed_session_keeps_key_tier(self, client_factory):
        captured = {}

        async def _fake_create_session(buyer_context):
            captured["ctx"] = buyer_context
            return {"session_id": "s-2"}

        with patch(
            "ad_seller.services.session_service.create_session",
            new=_fake_create_session,
        ):
            async with client_factory(_agency_key_record()) as c:
                resp = await c.post("/sessions", json={})

        assert resp.status_code == 200, resp.text
        assert captured["ctx"].effective_tier.value == "agency"


class TestMediaKitSearchTierVerification:
    async def test_search_with_key_uses_verified_context(self, client_factory):
        service = MagicMock()
        service.search_packages = AsyncMock(return_value=[])

        with patch(
            "ad_seller.interfaces.api.deps._get_media_kit_service",
            new=AsyncMock(return_value=service),
        ):
            async with client_factory(_agency_key_record()) as c:
                resp = await c.post(
                    "/media-kit/search",
                    json={"query": "sports", "buyer_tier": "advertiser"},
                )

        assert resp.status_code == 200, resp.text
        ctx = service.search_packages.await_args.kwargs["buyer_context"]
        assert ctx.effective_tier.value == "agency"
