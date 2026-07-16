# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""The shared-contract Product must declare ``ad_formats``.

Regression tests for the catalog contract seam that walked the real-mode rig
scenarios with ``no_booking``: the seller served every product with
``ad_formats: []`` (the taxonomy only lived in ``ext.inventory_type``), so any
buyer-side ``adFormat`` filter deterministically returned zero products.

The mapper must populate the shared ``Product.ad_formats`` field from the
internal ``inventory_type`` while keeping ``ext.inventory_type`` intact for
existing consumers.
"""

import pytest

from ad_seller.interfaces.api import contract_mappers as cm
from ad_seller.models.flow_state import ProductDefinition


def _make_product(inventory_type: str = "display") -> ProductDefinition:
    return ProductDefinition(
        product_id="prod_test_1",
        name="Test Product",
        description="A product for ad_formats mapping tests",
        inventory_type=inventory_type,
        base_cpm=12.5,
    )


@pytest.mark.parametrize("inventory_type", ["display", "video", "ctv", "native"])
def test_shared_product_ad_formats_populated_from_inventory_type(inventory_type):
    """A product with an inventory_type must emit non-empty ad_formats."""
    shared = cm.internal_product_to_shared(_make_product(inventory_type))
    assert shared.ad_formats == [inventory_type]


def test_ext_inventory_type_preserved():
    """ext.inventory_type must stay as-is (existing consumers rely on it)."""
    shared = cm.internal_product_to_shared(_make_product("video"))
    assert shared.ext is not None
    assert shared.ext["inventory_type"] == "video"


def test_products_list_response_emits_non_empty_ad_formats():
    """Every product served by the /products envelope declares its format."""
    products = [_make_product("display"), _make_product("video")]
    resp = cm.products_to_list_response(products)
    assert len(resp.products) == 2
    for wire in resp.products:
        assert wire.ad_formats, (
            f"product {wire.product_id} has empty ad_formats despite "
            f"inventory_type={wire.ext.get('inventory_type')!r}"
        )


def test_missing_inventory_type_yields_empty_ad_formats():
    """No inventory_type -> no declared formats (undeclared, not wrong)."""
    product = _make_product("display")
    object.__setattr__(product, "inventory_type", None)
    shared = cm.internal_product_to_shared(product)
    assert shared.ad_formats == []
