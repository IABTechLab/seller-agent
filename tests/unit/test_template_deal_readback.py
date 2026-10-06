"""Template deal fields survive conversion into the shared wire contract."""

from copy import deepcopy
from datetime import date

import pytest

from ad_seller.interfaces.api import contract_mappers as cm


def _flat_deal():
    return {
        "deal_id": "DEMO-TEMPLATE",
        "deal_type": "PD",
        "status": "confirmed",
        "product_id": "ctv-sports",
        "actual_price_cpm": 28.26,
        "currency": "EUR",
        "impressions": 1_000_000,
        "flight_start": "2026-10-06",
        "flight_end": "2026-11-05",
        "created_at": "2026-10-05T12:00:00Z",
    }


@pytest.mark.parametrize("null_sections", [False, True])
def test_flat_template_fields_are_mapped_without_changing_storage(null_sections):
    stored = _flat_deal()
    if null_sections:
        stored.update(product=None, pricing=None, terms=None)
    original = deepcopy(stored)

    deal = cm.internal_deal_to_shared_deal(stored)

    assert deal.product.product_id == "ctv-sports"
    assert deal.product.name == ""
    assert deal.pricing.final_cpm.amount_micros == 28_260_000
    assert deal.pricing.final_cpm.currency == "EUR"
    assert deal.pricing.base_cpm is None
    assert deal.terms.impressions == 1_000_000
    assert deal.terms.flight_start == date(2026, 10, 6)
    assert deal.terms.flight_end == date(2026, 11, 5)
    assert stored == original


def test_nested_sections_remain_authoritative_over_conflicting_flat_fields():
    stored = _flat_deal()
    stored.update(
        product={"product_id": "nested-product", "name": "Booked product"},
        pricing={"base_cpm": 40, "final_cpm": 32, "currency": "USD"},
        terms={"impressions": 2_000_000, "flight_start": "2026-12-01", "guaranteed": True},
    )
    original = deepcopy(stored)

    deal = cm.internal_deal_to_shared_deal(stored)

    assert deal.product.product_id == "nested-product"
    assert deal.product.name == "Booked product"
    assert deal.pricing.base_cpm.amount_micros == 40_000_000
    assert deal.pricing.final_cpm.amount_micros == 32_000_000
    assert deal.pricing.final_cpm.currency == "USD"
    assert deal.terms.impressions == 2_000_000
    assert deal.terms.flight_start == date(2026, 12, 1)
    assert deal.terms.flight_end is None
    assert deal.terms.guaranteed is True
    assert stored == original


def test_empty_nested_sections_do_not_pick_up_flat_values():
    deal = cm.internal_deal_to_shared_deal(
        {**_flat_deal(), "product": {}, "pricing": {}, "terms": {}}
    )
    assert deal.product.product_id == ""
    assert deal.pricing.final_cpm is None
    assert deal.terms.impressions is None


def test_zero_price_and_impressions_are_preserved():
    deal = cm.internal_deal_to_shared_deal(
        {**_flat_deal(), "actual_price_cpm": 0, "impressions": 0}
    )
    assert deal.pricing.final_cpm.amount_micros == 0
    assert deal.terms.impressions == 0
