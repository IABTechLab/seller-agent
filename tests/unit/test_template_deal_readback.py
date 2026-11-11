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


def _flat_curated_deal():
    return {
        "deal_id": "CUR-CURATED",
        "deal_type": "PA",
        "status": "confirmed",
        "product_id": "ctv-sports",
        "base_cpm": 15.0,
        "curator_fee_cpm": 1.5,
        "total_cpm": 16.5,
        "currency": "USD",
        "impressions": 500_000,
        "flight_start": "2026-10-06",
        "flight_end": "2026-11-05",
        "created_at": "2026-10-05T12:00:00Z",
    }


def test_flat_curated_price_is_mapped_without_changing_storage():
    stored = _flat_curated_deal()
    original = deepcopy(stored)

    deal = cm.internal_deal_to_shared_deal(stored)

    assert deal.pricing.base_cpm.amount_micros == 15_000_000
    assert deal.pricing.final_cpm.amount_micros == 16_500_000
    assert deal.pricing.final_cpm.currency == "USD"
    assert deal.terms.impressions == 500_000
    assert stored == original


@pytest.mark.parametrize(
    "fields, final_micros",
    [
        ({"actual_price_cpm": 20.0, "total_cpm": 16.5}, 20_000_000),
        ({"actual_price_cpm": 0, "total_cpm": 16.5}, 0),
        ({"actual_price_cpm": None, "total_cpm": 0}, 0),
        ({"actual_price_cpm": None, "total_cpm": None}, None),
    ],
)
def test_actual_price_wins_over_total_and_zero_is_a_price(fields, final_micros):
    stored = {**_flat_curated_deal(), **fields}

    deal = cm.internal_deal_to_shared_deal(stored)

    if final_micros is None:
        assert deal.pricing.final_cpm is None
    else:
        assert deal.pricing.final_cpm.amount_micros == final_micros


def test_nested_pricing_ignores_flat_curated_fields():
    deal = cm.internal_deal_to_shared_deal({**_flat_curated_deal(), "pricing": {}})

    assert deal.pricing.base_cpm is None
    assert deal.pricing.final_cpm is None


@pytest.mark.parametrize("product_id", [None, ""])
def test_missing_product_id_maps_to_empty_reference(product_id):
    deal = cm.internal_deal_to_shared_deal({**_flat_curated_deal(), "product_id": product_id})

    assert deal.product.product_id == ""


@pytest.mark.parametrize(
    "stored, wire",
    [
        ("PG", "PG"),
        ("pd", "PD"),
        ("PMP", "PA"),
        ("Preferred", "PD"),
        ("PRIVATE_AUCTION", "PA"),
        ("programmaticguaranteed", "PG"),
    ],
)
def test_stored_deal_type_spellings_map_to_the_wire_code(stored, wire):
    deal = cm.internal_deal_to_shared_deal({**_flat_curated_deal(), "deal_type": stored})

    assert deal.deal_type.value == wire


@pytest.mark.parametrize("stored", ["BOGUS", "", None, 3])
def test_unknown_stored_deal_type_names_the_value(stored):
    with pytest.raises(ValueError, match="Stored deal_type"):
        cm.internal_deal_to_shared_deal({**_flat_curated_deal(), "deal_type": stored})
