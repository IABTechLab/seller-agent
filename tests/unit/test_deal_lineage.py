# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for deal migration, deprecation, and lineage (issue #55).

migrate_deal, deprecate_deal, and get_deal_lineage had zero test coverage
before this file. That's how a migrate_deal double-call went unnoticed:
it has no guard against acting on an already-deprecated deal, unlike its
sibling deprecate_deal, so calling /migrate twice on the same deal_id
silently orphaned the first replacement and forked the lineage chain.
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
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(did))
    storage.set_deal = AsyncMock(side_effect=lambda did, data: store.__setitem__(did, data))
    storage._store = store
    return storage


def _make_deal(**overrides):
    defaults = {
        "deal_id": "DEAL-ORIG",
        "deal_type": "PD",
        "status": "confirmed",
        "product_id": "ctv-premium-sports",
        "actual_price_cpm": 30.0,
        "impressions": 1_000_000,
    }
    defaults.update(overrides)
    return defaults


def _migration_request(**overrides):
    defaults = dict(
        deal_type=None,
        product_id=None,
        max_cpm=None,
        impressions=None,
        flight_start=None,
        flight_end=None,
        buyer_seat_ids=None,
        reason="better supply path",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _deprecation_request(**overrides):
    defaults = dict(reason="inventory retired", replacement_deal_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestMigrateDeal:
    async def test_happy_path_creates_lineage(self, mock_storage, monkeypatch):
        mock_storage._store["DEAL-ORIG"] = _make_deal()
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        result = await deal_service.migrate_deal("DEAL-ORIG", _migration_request())

        new_deal_id = result["new_deal_id"]
        assert result["old_deal_id"] == "DEAL-ORIG"
        assert result["lineage"]["parent_deal_id"] == "DEAL-ORIG"
        assert result["lineage"]["replacement_deal_id"] == new_deal_id

        old = mock_storage._store["DEAL-ORIG"]
        assert old["status"] == "deprecated"
        assert old["replacement_deal_id"] == new_deal_id

        new = mock_storage._store[new_deal_id]
        assert new["parent_deal_id"] == "DEAL-ORIG"
        assert new["status"] == "confirmed"

    async def test_not_found_returns_404(self, mock_storage, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )
        with pytest.raises(HTTPException) as exc_info:
            await deal_service.migrate_deal("DEAL-NOPE", _migration_request())
        assert exc_info.value.status_code == 404

    async def test_migrating_an_already_migrated_deal_is_rejected(self, mock_storage, monkeypatch):
        """The bug: this used to silently create a second replacement and
        orphan the first one instead of erroring, the same way a double
        /deprecate call already errors."""
        mock_storage._store["DEAL-ORIG"] = _make_deal()
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        first = await deal_service.migrate_deal("DEAL-ORIG", _migration_request())
        first_replacement = first["new_deal_id"]

        with pytest.raises(HTTPException) as exc_info:
            await deal_service.migrate_deal("DEAL-ORIG", _migration_request(reason="retry"))

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "already_deprecated"
        assert exc_info.value.detail["replacement_deal_id"] == first_replacement

        # No second replacement was created, and the first one is still
        # correctly referenced — not orphaned.
        assert mock_storage._store["DEAL-ORIG"]["replacement_deal_id"] == first_replacement
        assert len(mock_storage._store) == 2  # original + first replacement only

    async def test_overrides_take_priority_over_old_deal_values(self, mock_storage, monkeypatch):
        mock_storage._store["DEAL-ORIG"] = _make_deal(impressions=1_000_000)
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        result = await deal_service.migrate_deal(
            "DEAL-ORIG", _migration_request(impressions=2_000_000, max_cpm=40.0)
        )

        new_deal = result["new_deal"]
        assert new_deal["impressions"] == 2_000_000
        assert new_deal["actual_price_cpm"] == 40.0


class TestDeprecateDeal:
    async def test_happy_path(self, mock_storage, monkeypatch):
        mock_storage._store["DEAL-ORIG"] = _make_deal()
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        result = await deal_service.deprecate_deal("DEAL-ORIG", _deprecation_request())

        assert result["status"] == "deprecated"
        assert mock_storage._store["DEAL-ORIG"]["status"] == "deprecated"

    async def test_double_deprecate_returns_409(self, mock_storage, monkeypatch):
        mock_storage._store["DEAL-ORIG"] = _make_deal()
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        await deal_service.deprecate_deal("DEAL-ORIG", _deprecation_request())
        with pytest.raises(HTTPException) as exc_info:
            await deal_service.deprecate_deal("DEAL-ORIG", _deprecation_request())

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "already_deprecated"


class TestGetDealLineage:
    async def test_single_migration_forward_lineage(self, mock_storage, monkeypatch):
        mock_storage._store["DEAL-ORIG"] = _make_deal()
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        result = await deal_service.migrate_deal("DEAL-ORIG", _migration_request())
        new_deal_id = result["new_deal_id"]

        lineage = await deal_service.get_deal_lineage("DEAL-ORIG")
        assert [r["deal_id"] for r in lineage["replacements"]] == [new_deal_id]

        lineage_from_new = await deal_service.get_deal_lineage(new_deal_id)
        assert [p["deal_id"] for p in lineage_from_new["parents"]] == ["DEAL-ORIG"]

    async def test_chain_of_two_migrations_stays_intact(self, mock_storage, monkeypatch):
        """A legitimate multi-hop chain (A -> B, then B -> C, each called
        on the CURRENT deal, never re-migrating an already-deprecated one)
        must remain fully walkable in both directions."""
        mock_storage._store["DEAL-A"] = _make_deal(deal_id="DEAL-A")
        monkeypatch.setattr(
            "ad_seller.storage.factory.get_storage", AsyncMock(return_value=mock_storage)
        )

        first = await deal_service.migrate_deal("DEAL-A", _migration_request())
        deal_b = first["new_deal_id"]
        second = await deal_service.migrate_deal(deal_b, _migration_request(reason="hop 2"))
        deal_c = second["new_deal_id"]

        lineage_from_a = await deal_service.get_deal_lineage("DEAL-A")
        assert [r["deal_id"] for r in lineage_from_a["replacements"]] == [deal_b, deal_c]

        lineage_from_c = await deal_service.get_deal_lineage(deal_c)
        assert [p["deal_id"] for p in lineage_from_c["parents"]] == ["DEAL-A", deal_b]
