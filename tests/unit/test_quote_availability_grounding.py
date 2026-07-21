# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quote availability is grounded in catalog data — never fabricated.

The quote path used to ship every quote with bare ``QuoteAvailability()``
defaults: ``inventory_available=True``, ``estimated_fill_rate=0.95``,
``competing_demand="moderate"`` — invented numbers on the wire to
counterparties. These tests pin the honest-availability policy (mirroring
``catalog_service.check_avails`` / POST /products/avails):

- ``inventory_available`` is derived from the product's declared capacity
  (``maximum_impressions``) vs the requested volume.
- ``estimated_fill_rate`` / ``competing_demand`` have no data source and
  are null — omission over invention.
- Unpriceable products (no ``base_cpm``/``floor_cpm``) are a 422 on the
  quote path too, never a fabricated price.
- The toolless level-3 availability_agent is grounded with a catalog-backed
  avails tool (same calculation) instead of free-hand forecasting prose.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used across the unit test suite.
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

from ad_seller.services import quote_service  # noqa: E402

# =============================================================================
# Helpers
# =============================================================================


def _make_product(**overrides):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    defaults = dict(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL, DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )
    defaults.update(overrides)
    return ProductDefinition(**defaults)


def _make_catalog(product):
    return {
        "products": {product.product_id: product},
        "inventory_types": [product.inventory_type],
    }


def _make_buyer_context():
    from ad_seller.interfaces.api.deps import _build_buyer_context

    return _build_buyer_context(buyer_tier="agency", agency_id="agency-1")


def _make_request(product_id="ctv-premium-sports", deal_type="PD", impressions=None):
    request = AsyncMock()
    request.product_id = product_id
    request.deal_type = deal_type
    request.impressions = impressions
    request.flight_start = None
    request.flight_end = None
    request.target_cpm = None
    return request


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage._store = store
    return storage


async def _create_quote(mock_storage, product, impressions=None, deal_type="PD"):
    request = _make_request(impressions=impressions, deal_type=deal_type)
    with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
        return await quote_service.create_quote(
            request, _make_buyer_context(), _make_catalog(product)
        )


# =============================================================================
# Model defaults: no fabricated numbers
# =============================================================================


class TestQuoteAvailabilityModelDefaults:
    def test_no_fabricated_fill_rate_or_demand(self):
        """Fields with no data source default to None, not invented values."""
        from ad_seller.models.quotes import QuoteAvailability

        availability = QuoteAvailability()
        assert availability.estimated_fill_rate is None
        assert availability.competing_demand is None


# =============================================================================
# Quote service: availability derived from declared capacity
# =============================================================================


class TestQuoteAvailabilityGrounding:
    async def test_uncapped_product_is_available(self, mock_storage):
        """No declared cap → requested volume is servable; nulls stay null."""
        quote = await _create_quote(mock_storage, _make_product(), impressions=5_000_000)

        availability = quote["availability"]
        assert availability["inventory_available"] is True
        assert availability["estimated_fill_rate"] is None
        assert availability["competing_demand"] is None

    async def test_request_above_cap_not_available(self, mock_storage):
        """Requested volume above maximum_impressions → not available."""
        product = _make_product(maximum_impressions=1_000_000)
        quote = await _create_quote(mock_storage, product, impressions=5_000_000)

        assert quote["availability"]["inventory_available"] is False

    async def test_request_within_cap_available(self, mock_storage):
        """Requested volume at/below maximum_impressions → available."""
        product = _make_product(maximum_impressions=1_000_000)
        quote = await _create_quote(mock_storage, product, impressions=1_000_000)

        assert quote["availability"]["inventory_available"] is True

    async def test_no_impressions_uses_minimum_as_requested_volume(self, mock_storage):
        """No impressions on the request → grounded at minimum_impressions
        (same fallback as check_avails), so a cap below the product minimum
        means the product cannot serve even its smallest deal."""
        product = _make_product(minimum_impressions=500_000, maximum_impressions=100_000)
        quote = await _create_quote(mock_storage, product, impressions=None)

        assert quote["availability"]["inventory_available"] is False

    async def test_unpriced_product_422_not_fabricated(self, mock_storage):
        """No base_cpm and no floor_cpm → 422, never an invented price."""
        product = _make_product(base_cpm=None, floor_cpm=None)

        with pytest.raises(HTTPException) as exc:
            await _create_quote(mock_storage, product, impressions=500_000)

        assert exc.value.status_code == 422

    async def test_floor_cpm_fallback_when_no_base_cpm(self, mock_storage):
        """base_cpm None with floor_cpm set → priced from the floor (mirrors
        check_avails' estimated_cpm fallback) instead of crashing."""
        product = _make_product(base_cpm=None, floor_cpm=28.0)
        quote = await _create_quote(mock_storage, product, impressions=500_000)

        assert quote["pricing"]["base_cpm"] == 28.0
        assert quote["pricing"]["final_cpm"] > 0


# =============================================================================
# Wire edge: shared QuoteResponse carries the grounded availability
# =============================================================================


class TestQuoteWireAvailability:
    @pytest.fixture
    def client(self, mock_storage):
        import httpx
        from httpx import ASGITransport

        from ad_seller.interfaces.api.main import _get_optional_api_key_record, app

        app.dependency_overrides[_get_optional_api_key_record] = lambda: None
        transport = ASGITransport(app=app)
        c = httpx.AsyncClient(transport=transport, base_url="http://test")
        yield c
        app.dependency_overrides.clear()

    def _patch_catalog(self, product):
        return patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_make_catalog(product),
        )

    async def test_capped_product_wire_availability_honest(self, client, mock_storage):
        """On the shared wire: capped-out quote says unavailable with null
        fill-rate/demand — no invented 0.95/"moderate"."""
        product = _make_product(maximum_impressions=200_000)
        body = {
            "idempotency_key": "idem-ground-1",
            "product_id": product.product_id,
            "deal_type": "PD",
            "impressions": 500_000,
        }

        with (
            self._patch_catalog(product),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post("/api/v1/quotes", json=body)

        assert resp.status_code == 200
        availability = resp.json()["quote"]["availability"]
        assert availability["inventory_available"] is False
        assert availability["estimated_fill_rate"] is None
        assert availability["competing_demand"] is None

    async def test_unpriced_product_wire_422(self, client, mock_storage):
        """Quote for an unpriced product is a 422 on the wire."""
        product = _make_product(base_cpm=None, floor_cpm=None)
        body = {
            "idempotency_key": "idem-ground-2",
            "product_id": product.product_id,
            "deal_type": "PD",
            "impressions": 500_000,
        }

        with (
            self._patch_catalog(product),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post("/api/v1/quotes", json=body)

        assert resp.status_code == 422


# =============================================================================
# availability_agent: grounded tool over free-hand prose
# =============================================================================


class TestCatalogAvailsTool:
    def _patched_catalog(self, product):
        return patch(
            "ad_seller.services.catalog_service.get_static_product_catalog",
            return_value=_make_catalog(product),
        )

    def test_product_avails_reports_capacity(self):
        """Tool output carries check_avails numbers for the product."""
        from ad_seller.tools import CatalogAvailsTool

        product = _make_product(maximum_impressions=200_000)
        with self._patched_catalog(product):
            out = CatalogAvailsTool()._run(
                product_id=product.product_id, requested_impressions=500_000
            )

        assert "200,000" in out  # capped available impressions
        assert "no data source" in out.lower()  # delivery confidence honesty

    def test_unpriced_product_reported_not_invented(self):
        """Unpriceable product → honest 'cannot be priced' text, no numbers."""
        from ad_seller.tools import CatalogAvailsTool

        product = _make_product(base_cpm=None, floor_cpm=None)
        with self._patched_catalog(product):
            out = CatalogAvailsTool()._run(
                product_id=product.product_id, requested_impressions=500_000
            )

        assert "cannot be priced" in out.lower()

    def test_unknown_product_lists_catalog(self):
        from ad_seller.tools import CatalogAvailsTool

        with self._patched_catalog(_make_product()):
            out = CatalogAvailsTool()._run(product_id="prod-nope")

        assert "not found" in out.lower()
        assert "ctv-premium-sports" in out

    def test_catalog_summary_without_product_id(self):
        """No product_id → declared-capacity summary of the catalog."""
        from ad_seller.tools import CatalogAvailsTool

        with self._patched_catalog(_make_product(maximum_impressions=750_000)):
            out = CatalogAvailsTool()._run()

        assert "ctv-premium-sports" in out
        assert "750,000" in out


class TestAvailabilityAgentGrounded:
    def test_agent_carries_catalog_avails_tool(self):
        """The level-3 availability agent is no longer toolless: it carries
        the catalog-grounded avails tool (same calculation as the avails
        endpoint and the quote path)."""
        from unittest.mock import MagicMock

        from ad_seller.agents.level3 import availability_agent as agent_module
        from ad_seller.tools import CatalogAvailsTool

        settings = MagicMock(
            default_llm_model="anthropic/claude-test",
            llm_max_tokens=1024,
            crew_memory_enabled=False,
        )
        # Suite runs key-less: stub settings + LLM construction; the wiring
        # under test is the agent's tool list.
        with (
            patch.object(agent_module, "get_settings", return_value=settings),
            patch.object(agent_module, "build_llm", return_value="anthropic/claude-test"),
        ):
            agent = agent_module.create_availability_agent()

        assert any(isinstance(t, CatalogAvailsTool) for t in agent.tools)
