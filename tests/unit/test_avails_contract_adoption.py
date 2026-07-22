# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""EP-12 adoption: the avails endpoint speaks the shared contract.

``POST /products/avails`` now validates/serializes with
``iab_agentic_primitives.protocol.AvailsRequest`` / ``AvailsResponse`` —
the canonical home of the avails wire contract — re-exported through
``ad_seller.interfaces.api.schemas`` so existing imports keep working.

Policy conformance pinned here (settled decisions the shared schema
encodes):

1. ``availableImpressions`` REQUIRED — uncapped products report
   requested-as-available.
2. ``deliveryConfidence`` OPTIONAL and OMITTED ENTIRELY when there is no
   forecast data source. This seller has no data source, so the key never
   appears on the wire (it was ``null``-padded through v2.1.x — that
   legacy shape remains parseable by readers but is no longer emitted).
3. ``guaranteedImpressions`` present ONLY for PG-capable products; the
   key is absent, not null, for non-PG products.
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
from iab_agentic_primitives.protocol import (  # noqa: E402
    AvailsRequest as SharedAvailsRequest,
)
from iab_agentic_primitives.protocol import (  # noqa: E402
    AvailsResponse as SharedAvailsResponse,
)

from ad_seller.interfaces.api import schemas  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402


def _mock_catalog(products_dict):
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


def _patch_catalog(catalog):
    return patch(
        "ad_seller.interfaces.api.main._get_static_product_catalog",
        return_value=catalog,
    )


def _body(**fields):
    body = {
        "productid": "ctv-premium-sports",
        "startdate": "2026-08-01T00:00:00Z",
        "enddate": "2026-08-31T00:00:00Z",
    }
    body.update(fields)
    return body


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c


class TestSharedContractAdoption:
    """The seller's avails schema names alias the shared contract classes."""

    def test_avails_request_is_the_shared_class(self):
        assert schemas.AvailsRequest is SharedAvailsRequest

    def test_avails_response_is_the_shared_class(self):
        assert schemas.AvailsResponse is SharedAvailsResponse


class TestPolicyConformantEmission:
    """The endpoint emits the canonical shape: no null padding."""

    async def test_delivery_confidence_omitted_not_null(self, client):
        """Policy 2: no forecast data source -> the key is ABSENT."""
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post(
                "/products/avails", json=_body(requestedImpressions=500000)
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "deliveryConfidence" not in data

    async def test_guaranteed_impressions_omitted_for_non_pg(self, client):
        """Policy 3: guaranteedImpressions present ONLY for PG products."""
        from ad_seller.models.core import DealType

        product = _make_product(supported_deal_types=[DealType.PREFERRED_DEAL])
        with _patch_catalog(_catalog_with(product)):
            resp = await client.post(
                "/products/avails", json=_body(requestedImpressions=100000)
            )

        assert resp.status_code == 200
        assert "guaranteedImpressions" not in resp.json()

    async def test_canonical_wire_shape_pg_product(self, client):
        """Byte-level pin of the full canonical response for a PG product
        with no targeting dicts: exactly the required fields plus
        guaranteedImpressions — nothing null-padded."""
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post(
                "/products/avails", json=_body(requestedImpressions=500000)
            )

        assert resp.status_code == 200
        assert resp.json() == {
            "productid": "ctv-premium-sports",
            "availableImpressions": 500000,
            "guaranteedImpressions": 500000,
            "estimatedCpm": 35.0,
            "totalCost": 17500.0,
        }

    async def test_available_impressions_always_present(self, client):
        """Policy 1: uncapped product reports requested-as-available."""
        with _patch_catalog(_catalog_with(_make_product(maximum_impressions=None))):
            resp = await client.post(
                "/products/avails", json=_body(requestedImpressions=99000000)
            )

        assert resp.status_code == 200
        assert resp.json()["availableImpressions"] == 99000000


class TestLegacyReaderCompatibility:
    """The shared model still reads the null-padded v2.1.x emission."""

    def test_legacy_null_padded_response_parses(self):
        legacy = {
            "productid": "prod-display-001",
            "availableImpressions": 750000,
            "guaranteedImpressions": 500000,
            "estimatedCpm": 12.0,
            "totalCost": 6000.0,
            "deliveryConfidence": None,
            "availableTargeting": None,
        }
        resp = schemas.AvailsResponse.model_validate(legacy)
        assert resp.delivery_confidence is None
        assert resp.available_targeting is None
