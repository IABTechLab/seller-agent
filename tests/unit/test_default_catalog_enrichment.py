# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Default catalog enrichment: caps, targeting, one unpriced product.

``DEFAULT_PRODUCT_CONFIGS`` used to declare every product uncapped,
untargeted, and priced — so the avails capping, availableTargeting, and
422-unpriceable paths only ever exercised against synthetic test products,
never on the rig wire. These tests pin the enriched catalog DATA:

- at least 2 products declare realistic ``maximum_impressions`` caps;
- at least 2 products declare audience/content targeting dicts;
- exactly ONE product is deliberately unpriced (no base_cpm/floor_cpm) so
  the 422-unpriceable path is live on the wire;
- the enrichment flows through ``build_static_product_catalog`` into
  ``ProductDefinition``s, and the avails + quote paths (honest-availability grounding)
  handle capped and unpriced products honestly.
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

from ad_seller.services import catalog_service, quote_service  # noqa: E402

# =============================================================================
# Helpers
# =============================================================================


def _configs():
    return catalog_service.DEFAULT_PRODUCT_CONFIGS


def _fresh_products():
    """ProductDefinitions built from the default configs (uncached)."""
    return list(catalog_service.build_static_product_catalog()["products"].values())


def _by_name(products, name):
    matches = [p for p in products if p.name == name]
    assert matches, f"product '{name}' missing from default catalog"
    return matches[0]


# =============================================================================
# Catalog data shape
# =============================================================================


class TestDefaultCatalogEnrichment:
    def test_at_least_two_products_declare_capacity_caps(self):
        capped = [c for c in _configs() if c.get("maximum_impressions") is not None]
        assert len(capped) >= 2
        for cfg in capped:
            assert isinstance(cfg["maximum_impressions"], int)
            assert cfg["maximum_impressions"] > 0
            # A cap below the minimum deal size would make the product
            # permanently unservable.
            assert cfg["maximum_impressions"] >= cfg.get("minimum_impressions", 10000)

    def test_at_least_two_products_declare_targeting_dicts(self):
        targeted = [
            c for c in _configs() if c.get("audience_targeting") or c.get("content_targeting")
        ]
        assert len(targeted) >= 2
        for cfg in targeted:
            for key in ("audience_targeting", "content_targeting"):
                if cfg.get(key) is not None:
                    assert isinstance(cfg[key], dict)
                    assert cfg[key], f"{key} on '{cfg['name']}' must not be empty"

    def test_exactly_one_deliberately_unpriced_product(self):
        unpriced = [
            c for c in _configs() if c.get("base_cpm") is None and c.get("floor_cpm") is None
        ]
        assert len(unpriced) == 1

    def test_existing_product_names_and_cpms_stable(self):
        """Anchor products the wire/rig scenarios rely on keep name + CPMs."""
        anchors = {
            "Premium Display - Homepage": (15.0, 10.0),
            "Standard Display - ROS": (8.0, 5.0),
            "Pre-Roll Video": (25.0, 18.0),
            "CTV Premium Streaming": (35.0, 28.0),
            "NBC Primetime :30": (55.0, 40.0),
        }
        by_name = {c["name"]: c for c in _configs()}
        for name, (base, floor) in anchors.items():
            assert name in by_name, f"anchor product '{name}' renamed or removed"
            assert by_name[name]["base_cpm"] == base
            assert by_name[name]["floor_cpm"] == floor


class TestEnrichmentReachesProductDefinitions:
    def test_caps_flow_through_build(self):
        products = _fresh_products()
        capped = [p for p in products if p.maximum_impressions is not None]
        assert len(capped) >= 2

    def test_targeting_flows_through_build(self):
        products = _fresh_products()
        targeted = [
            p
            for p in products
            if p.audience_targeting is not None or p.content_targeting is not None
        ]
        assert len(targeted) >= 2

    def test_unpriced_product_flows_through_build(self):
        products = _fresh_products()
        unpriced = [p for p in products if p.base_cpm is None and p.floor_cpm is None]
        assert len(unpriced) == 1

    def test_flow_and_catalog_share_the_config_mapping(self):
        """ProductSetupFlow consumes the same config mapping, so enrichment
        fields cannot silently diverge between the two consumers."""
        import inspect

        from ad_seller.flows import product_setup_flow

        source = inspect.getsource(product_setup_flow.ProductSetupFlow.create_default_products)
        assert "product_from_config" in source


# =============================================================================
# The enriched data exercises the honest-availability paths
# =============================================================================


class TestEnrichedCatalogOnAvailsPath:
    def test_capped_default_product_caps_avails(self):
        products = _fresh_products()
        product = next(p for p in products if p.maximum_impressions is not None)

        result = catalog_service.check_avails(
            product, requested_impressions=product.maximum_impressions * 10
        )

        assert result["available_impressions"] == product.maximum_impressions

    def test_targeted_default_product_reports_available_targeting(self):
        products = _fresh_products()
        product = next(
            p
            for p in products
            if p.audience_targeting is not None or p.content_targeting is not None
        )

        result = catalog_service.check_avails(product, requested_impressions=100_000)

        assert result["available_targeting"]

    def test_unpriced_default_product_is_422_on_avails(self):
        products = _fresh_products()
        product = next(p for p in products if p.base_cpm is None and p.floor_cpm is None)

        with pytest.raises(HTTPException) as exc:
            catalog_service.check_avails(product, requested_impressions=100_000)

        assert exc.value.status_code == 422


class TestEnrichedCatalogOnQuotePath:
    """Task interplay: quote grounding must handle the enriched
    default products (caps and the unpriced product) honestly."""

    @pytest.fixture
    def mock_storage(self):
        store = {}
        storage = AsyncMock()
        storage.get = AsyncMock(side_effect=lambda k: store.get(k))
        storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
        storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
        storage.set_quote = AsyncMock(
            side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
        )
        return storage

    def _catalog(self):
        products = {p.product_id: p for p in _fresh_products()}
        return {
            "products": products,
            "inventory_types": sorted({p.inventory_type for p in products.values()}),
        }

    def _request(self, product_id, impressions):
        request = AsyncMock()
        request.product_id = product_id
        request.deal_type = "PD"
        request.impressions = impressions
        request.flight_start = None
        request.flight_end = None
        request.target_cpm = None
        return request

    def _context(self):
        from ad_seller.interfaces.api.deps import _build_buyer_context

        return _build_buyer_context(buyer_tier="agency", agency_id="agency-1")

    async def test_quote_availability_reflects_default_cap(self, mock_storage):
        catalog = self._catalog()
        product = next(p for p in catalog["products"].values() if p.maximum_impressions is not None)
        request = self._request(product.product_id, product.maximum_impressions * 10)

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(request, self._context(), catalog)

        assert quote["availability"]["inventory_available"] is False

    async def test_quote_within_default_cap_is_available(self, mock_storage):
        catalog = self._catalog()
        product = next(p for p in catalog["products"].values() if p.maximum_impressions is not None)
        request = self._request(product.product_id, product.maximum_impressions)

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            quote = await quote_service.create_quote(request, self._context(), catalog)

        assert quote["availability"]["inventory_available"] is True

    async def test_quote_for_unpriced_default_product_is_422(self, mock_storage):
        catalog = self._catalog()
        product = next(
            p for p in catalog["products"].values() if p.base_cpm is None and p.floor_cpm is None
        )
        request = self._request(product.product_id, 500_000)

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await quote_service.create_quote(request, self._context(), catalog)

        assert exc.value.status_code == 422

    def test_pricing_for_unpriced_default_product_is_422(self):
        """POST /pricing's service path is honest too: 422, not a crash."""
        product = next(p for p in _fresh_products() if p.base_cpm is None and p.floor_cpm is None)

        with pytest.raises(HTTPException) as exc:
            quote_service.get_pricing(
                product_id=product.product_id,
                product=product,
                buyer_context=self._context(),
                volume=0,
            )

        assert exc.value.status_code == 422
