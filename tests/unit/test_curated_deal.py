# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for create_curated_deal (issue #57).

create_curated_deal had zero test coverage before this file. That's how
an unknown product_id went unnoticed silently falling back to a
hardcoded $12 CPM default and minting a live, "confirmed" deal instead
of 404ing -- the same honest-pricing guarantee the adjacent code comment
claims to enforce, just missing for the "product doesn't exist at all"
case rather than the "product exists but has no price" case it does
cover.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from ad_seller.services import deal_service


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.set_deal = AsyncMock(side_effect=lambda did, data: store.__setitem__(did, data))
    storage._store = store
    return storage


def _make_product(**overrides):
    defaults = dict(product_id="ctv-premium-sports", base_cpm=45.0, floor_cpm=35.0)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _catalog(*products):
    return {"products": {p.product_id: p for p in products}}


def _curated_request(**overrides):
    defaults = dict(
        curator_id="agent-range",
        deal_type="PMP",
        product_id=None,
        audience_segments=[],
        content_categories=[],
        impressions=1_000_000,
        max_cpm=None,
        flight_start=None,
        flight_end=None,
        buyer_seat_ids=[],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCreateCuratedDeal:
    async def test_unknown_product_id_returns_404_not_a_fabricated_price(
        self, mock_storage, monkeypatch
    ):
        """The bug: a typo'd/stale product_id used to silently price the
        deal at the $12 CPM no-product default and confirm it anyway."""
        catalog = _catalog(_make_product(base_cpm=45.0, floor_cpm=35.0))
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        request = _curated_request(product_id="ctv-premiumm-sports")  # typo, not in catalog

        with pytest.raises(HTTPException) as exc_info:
            await deal_service.create_curated_deal(request, catalog)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "product_not_found"
        # No deal was minted off the phantom product reference.
        assert mock_storage._store == {}

    async def test_known_product_prices_off_its_real_base_cpm(self, mock_storage, monkeypatch):
        catalog = _catalog(_make_product(base_cpm=45.0, floor_cpm=35.0))
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        request = _curated_request(product_id="ctv-premium-sports")
        result = await deal_service.create_curated_deal(request, catalog)

        assert result["pricing"]["base_cpm"] == 45.0
        # Agent Range's default fee is 10% of base.
        assert result["pricing"]["curator_fee_cpm"] == pytest.approx(4.5)
        assert result["pricing"]["total_cpm"] == pytest.approx(49.5)

    async def test_known_but_unpriced_product_is_422(self, mock_storage, monkeypatch):
        """The case the original comment was already correctly handling --
        confirms the fix didn't disturb it."""
        catalog = _catalog(_make_product(base_cpm=None, floor_cpm=None))
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        request = _curated_request(product_id="ctv-premium-sports")
        with pytest.raises(HTTPException) as exc_info:
            await deal_service.create_curated_deal(request, catalog)
        assert exc_info.value.status_code == 422

    async def test_no_product_id_uses_generic_default(self, mock_storage, monkeypatch):
        """A curated deal with no product reference at all is a supported
        generic case -- distinct from a product_id that fails to resolve."""
        catalog = _catalog(_make_product())
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        request = _curated_request(product_id=None)
        result = await deal_service.create_curated_deal(request, catalog)

        assert result["pricing"]["base_cpm"] == 12.0

    async def test_curator_not_found_returns_404(self, mock_storage, monkeypatch):
        catalog = _catalog(_make_product())
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        request = _curated_request(curator_id="nonexistent-curator")
        with pytest.raises(HTTPException) as exc_info:
            await deal_service.create_curated_deal(request, catalog)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "curator_not_found"
