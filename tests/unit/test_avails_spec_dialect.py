# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenDirect 2.1 spec dialect on POST /products/avails (convergence).

The published OpenDirect 2.1 request is ``ProductAvailsSearch``: a
multi-product ``productids`` ARRAY plus required ``accountid`` and
``advertiserbrandid``; the response is the ``avails`` collection envelope
of ``Avails`` records with ``availsstatus`` semantics (Available /
Partially Available / Unavailable + enumerated reasons). Spec source:
OpenDirect v2.1 final, normative attribute tables
(https://github.com/InteractiveAdvertisingBureau/OpenDirect).

Convergence contract (shared iab-agentic-primitives avails module):

* the seller accepts BOTH dialects, discriminated by ``productids``
  (array, spec) vs ``productid`` (scalar, legacy);
* the response dialect FOLLOWS the request dialect — legacy requests get
  the legacy single-object response byte-for-byte (pinned in
  test_avails_endpoint.py / test_opendirect_wire_conformance.py), spec
  requests get the spec envelope;
* requested volume/budget arrive on the spec dialect as minted
  Investment ``producttargeting`` entries
  (``datasource=iab-agentic-primitives``,
  ``target=requestedimpressions|budget``) and are honored by the same
  honest-availability policy as the legacy fields.
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

# The spec Avails record's complete top-level attribute set (normative
# table): the emitted records must never carry anything else.
SPEC_AVAILS_FIELDS = {
    "productid",
    "accountid",
    "availability",
    "availsstatus",
    "currency",
    "price",
    "startdate",
    "enddate",
}


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


def _catalog_with(*products):
    return _mock_catalog({p.product_id: p for p in products})


def _spec_body(**fields):
    """A strictly spec-shaped ProductAvailsSearch body."""
    body = {
        "productids": ["ctv-premium-sports"],
        "accountid": "acct-42",
        "advertiserbrandid": "brand-orchard-7",
        "startdate": "2026-08-01T00:00:00Z",
        "enddate": "2026-08-31T00:00:00Z",
        "currency": "USD",
    }
    body.update(fields)
    return body


def _volume_producttargeting(impressions=None, budget=None):
    entries = []
    if impressions is not None:
        entries.append(
            {
                "name": "Investment",
                "type": "Audience",
                "datasource": "iab-agentic-primitives",
                "target": "requestedimpressions",
                "targetvalues": [str(impressions)],
                "selectable": False,
            }
        )
    if budget is not None:
        entries.append(
            {
                "name": "Investment",
                "type": "Investment",
                "datasource": "iab-agentic-primitives",
                "target": "budget",
                "targetvalues": [str(budget)],
                "selectable": False,
            }
        )
    return entries


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c


def _patch_catalog(catalog):
    return patch(
        "ad_seller.interfaces.api.main._get_static_product_catalog",
        return_value=catalog,
    )


class TestSpecRequestAccepted:
    async def test_strictly_spec_shaped_request_succeeds(self, client):
        """A conformant independent OpenDirect 2.1 client is no longer 422'd."""
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=_spec_body())

        assert resp.status_code == 200

    async def test_response_is_the_avails_collection_envelope(self, client):
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=_spec_body())

        data = resp.json()
        # Collection Objects table: the array property is named 'avails'.
        assert set(data.keys()) == {"avails"}
        assert isinstance(data["avails"], list)
        assert len(data["avails"]) == 1

    async def test_record_carries_the_spec_required_fields(self, client):
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=_spec_body())

        [record] = resp.json()["avails"]
        assert set(record.keys()) <= SPEC_AVAILS_FIELDS
        assert record["productid"] == "ctv-premium-sports"
        assert record["accountid"] == "acct-42"  # spec-required echo
        assert record["price"] == 35.0  # base CPM
        assert record["currency"] == "USD"
        assert record["startdate"] == "2026-08-01T00:00:00Z"
        assert record["enddate"] == "2026-08-31T00:00:00Z"
        # No requested volume in the search: product minimum, Available.
        assert record["availability"] == 100000

    async def test_multi_product_yields_one_record_per_product_in_order(self, client):
        products = [
            _make_product(),
            _make_product(product_id="display-news", inventory_type="display"),
        ]
        body = _spec_body(productids=["ctv-premium-sports", "display-news"])
        with _patch_catalog(_catalog_with(*products)):
            resp = await client.post("/products/avails", json=body)

        assert resp.status_code == 200
        records = resp.json()["avails"]
        assert [r["productid"] for r in records] == [
            "ctv-premium-sports",
            "display-news",
        ]


class TestAvailsStatusSemantics:
    async def test_full_availability_is_available_without_reason(self, client):
        body = _spec_body(producttargeting=_volume_producttargeting(impressions=500000))
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=body)

        [record] = resp.json()["avails"]
        assert record["availability"] == 500000
        status = record["availsstatus"]
        assert status["status"] == "Available"
        assert "reason" not in status
        # Spec-required producttargeting array describes the inventory.
        [pt] = status["producttargeting"]
        assert pt["target"] == "impressions"
        assert pt["targetvalues"] == ["500000"]

    async def test_capacity_cap_is_partially_available_booked(self, client):
        product = _make_product(maximum_impressions=400000)
        body = _spec_body(producttargeting=_volume_producttargeting(impressions=500000))
        with _patch_catalog(_catalog_with(product)):
            resp = await client.post("/products/avails", json=body)

        [record] = resp.json()["avails"]
        assert record["availability"] == 400000
        status = record["availsstatus"]
        assert status["status"] == "Partially Available"
        assert status["reason"] == "Booked"

    async def test_zero_availability_is_unavailable(self, client):
        product = _make_product(maximum_impressions=0)
        body = _spec_body(producttargeting=_volume_producttargeting(impressions=500000))
        with _patch_catalog(_catalog_with(product)):
            resp = await client.post("/products/avails", json=body)

        [record] = resp.json()["avails"]
        assert record["availability"] == 0
        assert record["availsstatus"]["status"] == "Unavailable"
        assert record["availsstatus"]["reason"] == "Booked"

    async def test_budget_derived_volume_respects_the_cap(self, client):
        """Budget rides the minted Investment entry; the honest-availability
        policy derives volume from it exactly as on the legacy dialect."""
        product = _make_product(maximum_impressions=20000)
        # int(1000 / 35.0 * 1000) = 28571 requested > 20000 cap -> partial.
        body = _spec_body(producttargeting=_volume_producttargeting(budget=1000.0))
        with _patch_catalog(_catalog_with(product)):
            resp = await client.post("/products/avails", json=body)

        [record] = resp.json()["avails"]
        assert record["availability"] == 20000
        assert record["availsstatus"]["status"] == "Partially Available"


class TestSpecDialectErrors:
    async def test_unknown_product_is_404(self, client):
        body = _spec_body(productids=["ctv-premium-sports", "no-such-product"])
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=body)

        assert resp.status_code == 404
        assert "no-such-product" in str(resp.json())

    async def test_missing_accountid_is_422(self, client):
        body = _spec_body()
        del body["accountid"]
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=body)

        assert resp.status_code == 422

    async def test_unpriceable_product_is_422(self, client):
        product = _make_product(base_cpm=None, floor_cpm=None)
        with _patch_catalog(_catalog_with(product)):
            resp = await client.post("/products/avails", json=_spec_body())

        assert resp.status_code == 422


class TestLegacyDialectUnchanged:
    async def test_legacy_request_still_gets_the_legacy_single_object(self, client):
        """Response dialect follows request dialect: no envelope for the
        v2.1.0-v2.2.1 simplified profile."""
        body = {
            "productid": "ctv-premium-sports",
            "startdate": "2026-08-01T00:00:00Z",
            "enddate": "2026-08-31T00:00:00Z",
            "requestedImpressions": 500000,
        }
        with _patch_catalog(_catalog_with(_make_product())):
            resp = await client.post("/products/avails", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert "avails" not in data
        assert data["productid"] == "ctv-premium-sports"
        assert data["availableImpressions"] == 500000
