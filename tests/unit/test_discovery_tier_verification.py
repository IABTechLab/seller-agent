"""Discovery, packages, media-kit search and sessions verify the buyer tier.

``POST /discovery`` used to build its buyer context straight from the body,
so a keyless request claiming ``buyer_tier="agency"`` with any ``agency_id``
was served exact agency CPMs instead of the public price ranges. Every
price-revealing read now goes through ``deps._verified_buyer_context``:
self-asserted identity floors to PUBLIC unless a seller-issued API key (or a
registry-verified agent) backs it.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.clients.agent_registry_client import BaseRegistryClient
from ad_seller.events.bus import InMemoryEventBus
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.agent_registry import AgentCard, AgentProvider, TrustStatus
from ad_seller.models.buyer_identity import BuyerIdentity
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.registry.agent_registry import AgentRegistryService
from ad_seller.storage.sqlite_backend import SQLiteBackend

AGENT_URL = "https://buyer-agent.example.test"


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


class _UnregisteredRegistryClient(BaseRegistryClient):
    """Registry double that knows no agents (no network)."""

    def __init__(self):
        super().__init__(
            registry_id="fake_registry",
            registry_name="Fake Registry",
            registry_url="http://registry.test",
        )

    async def verify_registration(self, agent_url):
        return False, None

    async def lookup_agent(self, agent_id):
        return None

    async def search_agents(self, agent_type=None, inventory_types=None):
        return []


@pytest.fixture
async def storage(tmp_path):
    backend = SQLiteBackend(f"sqlite:///{tmp_path}/discovery.db")
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def registry(storage):
    return AgentRegistryService(storage, registry_clients=[_UnregisteredRegistryClient()])


@pytest.fixture
def wired(storage, registry):
    """Route storage, registry, catalog and event bus to in-process doubles."""
    with ExitStack() as stack:
        stack.enter_context(patch("ad_seller.storage.factory.get_storage", return_value=storage))
        stack.enter_context(
            patch(
                "ad_seller.interfaces.api.deps._get_registry_service",
                new=AsyncMock(return_value=registry),
            )
        )
        stack.enter_context(
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                new=AsyncMock(return_value=_catalog()),
            )
        )
        stack.enter_context(
            patch(
                "ad_seller.services.catalog_service.get_static_product_catalog",
                new=AsyncMock(return_value=_catalog()),
            )
        )
        stack.enter_context(
            patch(
                "ad_seller.events.bus.get_event_bus",
                new=AsyncMock(return_value=InMemoryEventBus()),
            )
        )
        stack.enter_context(
            patch(
                "ad_seller.registry.agent_registry.fetch_agent_card",
                new=AsyncMock(return_value=None),
            )
        )
        yield


async def _register(registry, trust_status):
    card = AgentCard(
        name="Fictional Buyer Agent",
        description="A buyer agent",
        url=AGENT_URL,
        provider=AgentProvider(name="Fictional DSP"),
    )
    return await registry.register_agent(agent_card=card, trust_status=trust_status)


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


class TestBlockedAgentRejected:
    """Discovery and sessions left ``_resolve_and_enforce_agent``; the 403 must survive."""

    async def test_blocked_agent_gets_403_on_discovery(self, client_factory, registry, wired):
        await _register(registry, TrustStatus.BLOCKED)

        async with client_factory(None) as c:
            resp = await c.post(
                "/discovery",
                json={"query": "how much does CTV cost?", "agent_url": AGENT_URL},
            )

        assert resp.status_code == 403, resp.text
        assert "pricing" not in resp.json()

    async def test_blocked_agent_gets_403_on_sessions(self, client_factory, registry, wired):
        await _register(registry, TrustStatus.BLOCKED)
        create = AsyncMock(return_value={"session_id": "s-blocked"})

        with patch("ad_seller.services.session_service.create_session", new=create):
            async with client_factory(None) as c:
                resp = await c.post(
                    "/sessions",
                    json={"agency_id": "agency-x", "agent_url": AGENT_URL},
                )

        assert resp.status_code == 403, resp.text
        create.assert_not_awaited()


class TestFlooredSessionFiling:
    """A session floored to PUBLIC must not be filed under the claimed buyer key."""

    async def test_anonymous_advertiser_claim_is_filed_under_public(
        self, client_factory, storage, wired
    ):
        async with client_factory(None) as c:
            resp = await c.post(
                "/sessions",
                json={"advertiser_id": "adv-x", "agency_id": "agency-x"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["buyer_pricing_key"] == "public"
        session_id = body["session_id"]

        stored = await storage.get_session(session_id)
        assert stored["buyer_identity"]["advertiser_id"] is None
        assert stored["buyer_identity"]["agency_id"] is None
        assert [s["session_id"] for s in await storage.get_buyer_sessions("public")] == [session_id]
        assert await storage.get_buyer_sessions("advertiser:adv-x") == []
        assert await storage.get_buyer_sessions("agency:agency-x") == []

    async def test_keyed_session_stays_under_key_identity(self, client_factory, storage, wired):
        async with client_factory(_agency_key_record()) as c:
            resp = await c.post("/sessions", json={})

        assert resp.status_code == 200, resp.text
        assert resp.json()["buyer_pricing_key"] == "agency:agency-verified"
        assert len(await storage.get_buyer_sessions("agency:agency-verified")) == 1
