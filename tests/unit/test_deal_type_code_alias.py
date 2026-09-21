# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""``create_deal_from_template`` deal-type code mismatch (PG vs
programmaticguaranteed).

Regression tests: ``list_products``/``discover_products`` advertise a
product's supported deal types via the internal long-form ``DealType``
values (``models.core``, e.g. ``"programmaticguaranteed"``) — that is
exactly the value an end-to-end caller reads off the catalog and would
pass straight back into ``create_deal_from_template``. The service only
accepted the short wire code ("PG"/"PD"/"PA"), so that call 400'd with
``invalid_deal_type`` even though the value came from this seller's own
catalog response. Scope is universal (any ad server) since the mismatch
is in the deal-type validation shared by every booking path, not in any
SSP/ad-server-specific client.

The fix normalizes accepted spellings to the canonical short code before
validating and storing, so the deal a buyer gets back always reports the
same short code regardless of which spelling they used to request it.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Stub execution_activation_flow (cancel-scope leak on ad-server
# connection failure, unresolved -- issue #60 part 2). Same workaround as
# test_deal_status_wire_mapping.py: deal_service imports it transitively.
_broken_flows = [
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from fastapi import HTTPException  # noqa: E402

from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity  # noqa: E402
from ad_seller.models.core import DealType, PricingModel  # noqa: E402
from ad_seller.models.flow_state import ProductDefinition  # noqa: E402
from ad_seller.services import deal_service  # noqa: E402

# =============================================================================
# Helpers / fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(did))
    storage.set_deal = AsyncMock(side_effect=lambda did, data: store.__setitem__(did, data))
    storage._store = store
    return storage


def _make_product(**overrides) -> ProductDefinition:
    defaults = dict(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        description="Premium CTV sports inventory",
        inventory_type="ctv",
        supported_deal_types=[DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )
    defaults.update(overrides)
    return ProductDefinition(**defaults)


def _catalog(product: ProductDefinition) -> dict:
    return {"products": {product.product_id: product}, "inventory_types": [product.inventory_type]}


def _public_context() -> BuyerContext:
    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


def _template_request(**overrides) -> SimpleNamespace:
    defaults = dict(
        deal_type="PG",
        product_id="ctv-premium-sports",
        impressions=1_000_000,
        max_cpm=None,
        flight_start=None,
        flight_end=None,
        buyer_identity=None,
        notes=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# =============================================================================
# _normalize_deal_type_code — the alias table itself
# =============================================================================


class TestNormalizeDealTypeCode:
    @pytest.mark.parametrize(
        "raw",
        ["PG", "pg", "programmaticguaranteed", "PROGRAMMATICGUARANTEED", "programmatic_guaranteed"],
    )
    def test_pg_aliases_normalize_to_short_code(self, raw):
        assert deal_service._normalize_deal_type_code(raw) == "PG"

    @pytest.mark.parametrize(
        "raw",
        ["PD", "preferreddeal", "PREFERRED_DEAL"],
    )
    def test_pd_aliases_normalize_to_short_code(self, raw):
        assert deal_service._normalize_deal_type_code(raw) == "PD"

    @pytest.mark.parametrize(
        "raw",
        ["PA", "privateauction", "PRIVATE_AUCTION"],
    )
    def test_pa_aliases_normalize_to_short_code(self, raw):
        assert deal_service._normalize_deal_type_code(raw) == "PA"

    def test_unknown_spelling_returns_none(self):
        assert deal_service._normalize_deal_type_code("bogus") is None


# =============================================================================
# End-to-end reproduction: catalog's advertised deal type must be accepted
# =============================================================================


class TestCreateDealFromTemplateAcceptsCatalogAdvertisedDealType:
    async def test_long_form_catalog_value_is_accepted_not_400ed(self, mock_storage):
        """Reproduction of the reported defect: a caller reads
        ``product.supported_deal_types`` (long-form ``DealType.value``,
        exactly what ``list_products``/``discover_products`` return) and
        passes that same string as ``deal_type`` to
        ``create_deal_from_template``. Before the fix this 400'd with
        ``invalid_deal_type`` even though the value came straight off this
        seller's own catalog.
        """
        product = _make_product()
        advertised_deal_type = product.supported_deal_types[0].value
        assert advertised_deal_type == "programmaticguaranteed"

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.create_deal_from_template(
                _template_request(deal_type=advertised_deal_type),
                _public_context(),
                _catalog(product),
            )

        # Reported (and stored) as the canonical short wire code, regardless
        # of which accepted spelling the caller used to request it.
        assert deal["deal_type"] == "PG"

    async def test_underscored_long_form_is_also_accepted(self, mock_storage):
        product = _make_product()

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.create_deal_from_template(
                _template_request(deal_type="programmatic_guaranteed"),
                _public_context(),
                _catalog(product),
            )

        assert deal["deal_type"] == "PG"

    async def test_lowercase_short_code_is_accepted(self, mock_storage):
        product = _make_product()

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.create_deal_from_template(
                _template_request(deal_type="pg"),
                _public_context(),
                _catalog(product),
            )

        assert deal["deal_type"] == "PG"

    async def test_genuinely_invalid_deal_type_still_400s(self, mock_storage):
        product = _make_product()

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc_info:
                await deal_service.create_deal_from_template(
                    _template_request(deal_type="bogus"),
                    _public_context(),
                    _catalog(product),
                )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "invalid_deal_type"
