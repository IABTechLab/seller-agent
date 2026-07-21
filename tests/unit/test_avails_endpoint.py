# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the OpenDirect avails endpoint — POST /products/avails.

The buyer agent's OpenDirect client calls ``POST /products/avails``
(``check_avails``) with a spec-lowercase wire body (OpenDirect 2.1
attribute names; Tier-1 rename). The response is the
``AvailsResponse`` shape the buyer expects: availability is
derived honestly from the product catalog (``maximum_impressions`` /
``minimum_impressions`` / CPMs) with no fabricated data —
``deliveryConfidence`` is always null and unpriceable products are a 422,
never a made-up price.
"""

import sys
from types import ModuleType
from unittest.mock import patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch) before any import of ad_seller.flows triggers __init__.py.
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

from ad_seller.interfaces.api.main import app  # noqa: E402

# =============================================================================
# Helpers
# =============================================================================


def _mock_catalog(products_dict):
    """Build the dict shape returned by `_get_static_product_catalog`."""
    inventory_types = sorted({p.inventory_type for p in products_dict.values()})
    return {"products": products_dict, "inventory_types": inventory_types}


def _make_product(**overrides):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    defaults = dict(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        description="Premium CTV sports inventory",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL, DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )
    defaults.update(overrides)
    return ProductDefinition(**defaults)


def _catalog_with(product):
    return _mock_catalog({product.product_id: product})


def _body(**fields):
    """A valid AvailsRequest body (spec-lowercase, as the buyer client sends it)."""
    body = {
        "productid": "ctv-premium-sports",
        "startdate": "2026-08-01T00:00:00Z",
        "enddate": "2026-08-31T00:00:00Z",
    }
    body.update(fields)
    return body


@pytest.fixture
def client():
    """httpx AsyncClient over the FastAPI app."""
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c


def _patch_catalog(catalog):
    return patch(
        "ad_seller.interfaces.api.main._get_static_product_catalog",
        return_value=catalog,
    )


# =============================================================================
# POST /products/avails
# =============================================================================


class TestAvailsHappyPath:
    async def test_requested_impressions_all_fields(self, client):
        """Happy path: full response, PG product."""
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=500000),
            )

        assert resp.status_code == 200
        data = resp.json()
        # Exact wire keys the buyer's AvailsResponse parses (spec-lowercase
        # productid; extension fields keep camelCase pending Tier 2).
        assert data["productid"] == "ctv-premium-sports"
        assert data["availableImpressions"] == 500000
        # PG in supported_deal_types → guaranteed equals available.
        assert data["guaranteedImpressions"] == 500000
        assert data["estimatedCpm"] == 35.0
        assert data["totalCost"] == 17500.0  # 500000 / 1000 * 35.0
        # No data source for confidence — never fabricated.
        assert data["deliveryConfidence"] is None
        # No snake_case leakage on the wire.
        assert "available_impressions" not in data

    async def test_budget_derived_impressions(self, client):
        """No requestedImpressions: derive int(budget / cpm * 1000)."""
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=_body(budget=1000.0))

        assert resp.status_code == 200
        data = resp.json()
        assert data["availableImpressions"] == 28571  # int(1000 / 35.0 * 1000)
        assert data["totalCost"] == 999.99  # rounded to 2 decimals

    async def test_fallback_to_minimum_impressions(self, client):
        """No requestedImpressions and no budget: product minimum_impressions."""
        with _patch_catalog(_catalog_with(_make_product(minimum_impressions=250000))):
            resp = await client.post("/products/avails", json=_body())

        assert resp.status_code == 200
        assert resp.json()["availableImpressions"] == 250000

    async def test_maximum_impressions_caps_availability(self, client):
        """maximum_impressions set → available = min(requested, maximum)."""
        with _patch_catalog(_catalog_with(_make_product(maximum_impressions=200000))):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=500000),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["availableImpressions"] == 200000
        assert data["totalCost"] == 7000.0  # 200000 / 1000 * 35.0

    async def test_no_cap_reports_requested(self, client):
        """maximum_impressions None → no capacity cap, requested is available."""
        with _patch_catalog(_catalog_with(_make_product(maximum_impressions=None))):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=99000000),
            )

        assert resp.status_code == 200
        assert resp.json()["availableImpressions"] == 99000000

    async def test_floor_cpm_fallback_when_no_base_cpm(self, client):
        """base_cpm None → estimatedCpm falls back to floor_cpm."""
        with _patch_catalog(_catalog_with(_make_product(base_cpm=None, floor_cpm=28.0))):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=100000),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["estimatedCpm"] == 28.0
        assert data["totalCost"] == 2800.0


class TestAvailsGuaranteedImpressions:
    async def test_non_pg_product_guaranteed_null(self, client):
        """No PROGRAMMATIC_GUARANTEED support → guaranteedImpressions null."""
        from ad_seller.models.core import DealType

        product = _make_product(supported_deal_types=[DealType.PREFERRED_DEAL])
        with _patch_catalog(_catalog_with(product)):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=100000),
            )

        assert resp.status_code == 200
        assert resp.json()["guaranteedImpressions"] is None


class TestAvailsTargeting:
    async def test_available_targeting_union_of_keys(self, client):
        """Sorted union of keys across the product's targeting dicts."""
        product = _make_product(
            audience_targeting={"demo": ["A25-54"], "geo": ["US"]},
            content_targeting={"genre": ["sports"]},
            ad_product_targeting={"format": ["video"]},
        )
        with _patch_catalog(_catalog_with(product)):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=100000),
            )

        assert resp.status_code == 200
        assert resp.json()["availableTargeting"] == ["demo", "format", "genre", "geo"]

    async def test_no_targeting_dicts_is_null(self, client):
        """All targeting dicts None → availableTargeting null."""
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=100000),
            )

        assert resp.status_code == 200
        assert resp.json()["availableTargeting"] is None

    async def test_request_targeting_accepted_not_filtering(self, client):
        """Request targeting is accepted (no 422) but does not change avails."""
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=100000, targeting={"geo": ["US"]}),
            )

        assert resp.status_code == 200
        assert resp.json()["availableImpressions"] == 100000


class TestAvailsErrors:
    async def test_unknown_product_404(self, client):
        with _patch_catalog(_mock_catalog({})):
            resp = await client.post(
                "/products/avails",
                json=_body(productid="prod-does-not-exist"),
            )

        assert resp.status_code == 404
        assert "prod-does-not-exist" in resp.json()["detail"]

    async def test_end_date_before_start_date_422(self, client):
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post(
                "/products/avails",
                json=_body(
                    startdate="2026-08-31T00:00:00Z",
                    enddate="2026-08-01T00:00:00Z",
                ),
            )

        assert resp.status_code == 422

    async def test_end_date_equal_start_date_422(self, client):
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post(
                "/products/avails",
                json=_body(
                    startdate="2026-08-01T00:00:00Z",
                    enddate="2026-08-01T00:00:00Z",
                ),
            )

        assert resp.status_code == 422

    async def test_unpriceable_product_422(self, client):
        """base_cpm and floor_cpm both None → 422, never a fabricated price."""
        with _patch_catalog(_catalog_with(_make_product(base_cpm=None, floor_cpm=None))):
            resp = await client.post(
                "/products/avails",
                json=_body(requestedImpressions=100000),
            )

        assert resp.status_code == 422
        assert "price" in str(resp.json()["detail"]).lower()
