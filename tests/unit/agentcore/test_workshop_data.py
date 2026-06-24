"""Integration tests for AWS Workshop synthetic data.

Validates:
- inventory.csv loads 15+ products across 5 inventory types
- rate_card.json has correct base CPMs and 4-tier pricing
- media_kits.json has 4 packages with valid product references
- audiences.csv loads audience segments

Property 15: AWS Workshop synthetic data completeness
Property 12: Tiered pricing by authentication level

Validates: Requirements 8.1, 8.2, 8.3, 8.6
"""

import csv
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data" / "csv" / "samples" / "aws_workshop"

EXPECTED_CHANNELS = {"ctv", "linear", "digital_video", "display", "audio"}
EXPECTED_BASE_RATES = {
    "ctv": 45.0,
    "linear": 25.0,
    "digital_video": 18.0,
    "display": 12.0,
    "audio": 8.0,
}
EXPECTED_TIERS = {
    "public": 0,
    "registered_buyer": 5,
    "preferred_agency": 12,
    "strategic_advertiser": 15,
}


# ===================================================================
# Inventory CSV Tests
# ===================================================================


class TestWorkshopInventory:
    """Validate inventory.csv structure and content."""

    @pytest.fixture(autouse=True)
    def load_inventory(self):
        path = DATA_DIR / "inventory.csv"
        assert path.exists(), f"inventory.csv not found at {path}"
        with open(path) as f:
            self.rows = list(csv.DictReader(f))
        self.inventory_types = {row["inventory_type"] for row in self.rows}

    def test_minimum_15_products(self):
        assert len(self.rows) >= 15

    def test_all_five_channels_present(self):
        assert self.inventory_types == EXPECTED_CHANNELS

    def test_each_row_has_required_fields(self):
        required = {"id", "name", "status", "inventory_type", "floor_price_cpm", "currency"}
        for row in self.rows:
            for field in required:
                assert row.get(field), f"Row {row.get('id')} missing field: {field}"

    def test_all_products_active(self):
        for row in self.rows:
            assert row["status"] == "ACTIVE", f"Product {row['id']} is not ACTIVE"

    def test_floor_prices_are_positive(self):
        for row in self.rows:
            cpm = float(row["floor_price_cpm"])
            assert cpm > 0, f"Product {row['id']} has non-positive CPM: {cpm}"

    def test_product_ids_are_unique(self):
        ids = [row["id"] for row in self.rows]
        assert len(ids) == len(set(ids)), "Duplicate product IDs found"

    def test_ctv_has_at_least_3_products(self):
        ctv = [r for r in self.rows if r["inventory_type"] == "ctv"]
        assert len(ctv) >= 3

    def test_linear_has_at_least_2_products(self):
        linear = [r for r in self.rows if r["inventory_type"] == "linear"]
        assert len(linear) >= 2


# ===================================================================
# Rate Card Tests
# ===================================================================


class TestWorkshopRateCard:
    """Validate rate_card.json structure and pricing."""

    @pytest.fixture(autouse=True)
    def load_rate_card(self):
        path = DATA_DIR / "rate_card.json"
        assert path.exists(), f"rate_card.json not found at {path}"
        with open(path) as f:
            self.rate_card = json.load(f)

    def test_has_base_rates_for_all_channels(self):
        base_rates = self.rate_card["base_rates"]
        assert set(base_rates.keys()) == EXPECTED_CHANNELS

    def test_base_rates_match_spec(self):
        for channel, expected_cpm in EXPECTED_BASE_RATES.items():
            assert self.rate_card["base_rates"][channel] == expected_cpm

    def test_has_four_pricing_tiers(self):
        tiers = self.rate_card["tiers"]
        assert set(tiers.keys()) == set(EXPECTED_TIERS.keys())

    def test_tier_discounts_match_spec(self):
        for tier, expected_pct in EXPECTED_TIERS.items():
            assert self.rate_card["tiers"][tier]["discount_pct"] == expected_pct

    def test_discounts_are_ascending(self):
        discounts = [
            self.rate_card["tiers"][t]["discount_pct"]
            for t in ["public", "registered_buyer", "preferred_agency", "strategic_advertiser"]
        ]
        assert discounts == sorted(discounts)


# ===================================================================
# Media Kits Tests
# ===================================================================


class TestWorkshopMediaKits:
    """Validate media_kits.json structure and product references."""

    @pytest.fixture(autouse=True)
    def load_data(self):
        kits_path = DATA_DIR / "media_kits.json"
        inv_path = DATA_DIR / "inventory.csv"
        assert kits_path.exists()
        assert inv_path.exists()
        with open(kits_path) as f:
            self.kits = json.load(f)
        with open(inv_path) as f:
            self.valid_ids = {row["id"] for row in csv.DictReader(f)}

    def test_has_four_packages(self):
        assert len(self.kits) == 4

    def test_each_kit_has_required_fields(self):
        required = {"id", "name", "description", "products", "cpm_range", "target_audience"}
        for kit in self.kits:
            for field in required:
                assert field in kit, f"Kit {kit.get('id')} missing field: {field}"

    def test_all_product_references_are_valid(self):
        for kit in self.kits:
            for product_id in kit["products"]:
                assert product_id in self.valid_ids, (
                    f"Kit {kit['id']} references unknown product: {product_id}"
                )

    def test_cpm_range_min_less_than_max(self):
        for kit in self.kits:
            assert kit["cpm_range"]["min"] < kit["cpm_range"]["max"], (
                f"Kit {kit['id']} has invalid CPM range"
            )

    def test_kit_ids_are_unique(self):
        ids = [kit["id"] for kit in self.kits]
        assert len(ids) == len(set(ids))


# ===================================================================
# Audiences Tests
# ===================================================================


class TestWorkshopAudiences:
    """Validate audiences.csv structure."""

    @pytest.fixture(autouse=True)
    def load_audiences(self):
        path = DATA_DIR / "audiences.csv"
        assert path.exists()
        with open(path) as f:
            self.rows = list(csv.DictReader(f))

    def test_has_audience_segments(self):
        assert len(self.rows) >= 4

    def test_each_segment_has_required_fields(self):
        required = {"id", "name", "description", "size", "segment_type", "status"}
        for row in self.rows:
            for field in required:
                assert row.get(field), f"Audience {row.get('id')} missing field: {field}"


# ===================================================================
# Property 12: Tiered pricing by authentication level
# ===================================================================


_tier_strategy = st.sampled_from(list(EXPECTED_TIERS.keys()))
_channel_strategy = st.sampled_from(list(EXPECTED_BASE_RATES.keys()))


class TestTieredPricingProperty:
    """Property 12: Higher tiers always get lower or equal CPM than lower tiers.

    Validates: Requirement 8.6
    """

    @pytest.fixture(autouse=True)
    def load_rate_card(self):
        with open(DATA_DIR / "rate_card.json") as f:
            self.rate_card = json.load(f)

    @given(channel=_channel_strategy)
    @settings(max_examples=20, deadline=None)
    def test_strategic_advertiser_always_cheapest(self, channel):
        base = self.rate_card["base_rates"][channel]
        tiers = self.rate_card["tiers"]
        prices = {tier: base * (1 - tiers[tier]["discount_pct"] / 100) for tier in tiers}
        assert prices["strategic_advertiser"] <= prices["preferred_agency"]
        assert prices["preferred_agency"] <= prices["registered_buyer"]
        assert prices["registered_buyer"] <= prices["public"]
