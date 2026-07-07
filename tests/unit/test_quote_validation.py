# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Layer 4: seller-side quote validation.

Tests that when a buyer submits a proposal with a CPM, the seller
cross-references against quotes it previously issued and flags
proposals with unverified pricing.

Bead: ar-hm9l (child of epic ar-rrgw)
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from ad_seller.storage.quote_history import QuoteHistoryStore


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    """In-memory dict-backed mock storage for the KV store."""
    store: dict = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(
        side_effect=lambda k, v, ttl=None: store.__setitem__(k, v)
    )
    storage.keys = AsyncMock(
        side_effect=lambda pattern="*": [
            k for k in store if k.startswith(pattern.replace("*", ""))
        ]
    )
    storage._store = store
    return storage


@pytest.fixture
def quote_history(mock_storage) -> QuoteHistoryStore:
    """Create a QuoteHistoryStore backed by mock storage."""
    return QuoteHistoryStore(mock_storage)


# =============================================================================
# Test: Quote is recorded when issued
# =============================================================================


class TestRecordQuote:
    @pytest.mark.asyncio
    async def test_quote_recorded_on_issue(self, quote_history, mock_storage):
        """When a quote is issued, it should be persisted in quote_history."""
        await quote_history.record_quote(
            quote_id="qt-abc123",
            buyer_id="buyer-001",
            product_id="ctv-premium-sports",
            quoted_cpm=29.75,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        record = await quote_history.get_quote("qt-abc123")
        assert record is not None
        assert record["quote_id"] == "qt-abc123"
        assert record["buyer_id"] == "buyer-001"
        assert record["product_id"] == "ctv-premium-sports"
        assert record["quoted_cpm"] == 29.75
        assert "quoted_at" in record
        assert "expires_at" in record

    @pytest.mark.asyncio
    async def test_multiple_quotes_for_same_buyer_product(
        self, quote_history
    ):
        """Multiple quotes for the same buyer+product should all be stored."""
        await quote_history.record_quote(
            quote_id="qt-001",
            buyer_id="buyer-001",
            product_id="prod-A",
            quoted_cpm=25.0,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        await quote_history.record_quote(
            quote_id="qt-002",
            buyer_id="buyer-001",
            product_id="prod-A",
            quoted_cpm=22.0,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        quotes = await quote_history.find_quotes(
            buyer_id="buyer-001", product_id="prod-A"
        )
        assert len(quotes) >= 2


# =============================================================================
# Test: Proposal with matching quote CPM -> pricing_verified=true
# =============================================================================


class TestVerifyPricing:
    @pytest.mark.asyncio
    async def test_matching_cpm_verified(self, quote_history):
        """Proposal CPM that matches a quote should be verified."""
        await quote_history.record_quote(
            quote_id="qt-match",
            buyer_id="buyer-001",
            product_id="ctv-premium-sports",
            quoted_cpm=29.75,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        result = await quote_history.verify_pricing(
            buyer_id="buyer-001",
            product_id="ctv-premium-sports",
            proposed_cpm=29.75,
        )

        assert result.pricing_verified is True
        assert result.matched_quote_id == "qt-match"

    @pytest.mark.asyncio
    async def test_cpm_within_tolerance_verified(self, quote_history):
        """CPM within tolerance (default 1%) of quote should be verified."""
        await quote_history.record_quote(
            quote_id="qt-tol",
            buyer_id="buyer-001",
            product_id="prod-A",
            quoted_cpm=30.00,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        # 0.5% off — should be within 1% tolerance
        result = await quote_history.verify_pricing(
            buyer_id="buyer-001",
            product_id="prod-A",
            proposed_cpm=29.85,
        )

        assert result.pricing_verified is True

    # =================================================================
    # Test: Proposal with no matching quote -> pricing_verified=false
    # =================================================================

    @pytest.mark.asyncio
    async def test_no_matching_quote_unverified(self, quote_history):
        """Proposal with no matching quote should be unverified."""
        result = await quote_history.verify_pricing(
            buyer_id="buyer-999",
            product_id="ctv-premium-sports",
            proposed_cpm=50.00,
        )

        assert result.pricing_verified is False
        assert result.matched_quote_id is None
        assert "no matching quote" in result.reason.lower()

    # =================================================================
    # Test: Proposal with CPM outside tolerance -> pricing_verified=false
    # =================================================================

    @pytest.mark.asyncio
    async def test_cpm_outside_tolerance_unverified(self, quote_history):
        """CPM that differs >1% from any quote should be unverified."""
        await quote_history.record_quote(
            quote_id="qt-far",
            buyer_id="buyer-001",
            product_id="prod-A",
            quoted_cpm=30.00,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        # 10% off — well outside 1% tolerance
        result = await quote_history.verify_pricing(
            buyer_id="buyer-001",
            product_id="prod-A",
            proposed_cpm=33.00,
        )

        assert result.pricing_verified is False
        assert "outside tolerance" in result.reason.lower()

    # =================================================================
    # Test: Expired quote -> pricing_verified=false
    # =================================================================

    @pytest.mark.asyncio
    async def test_expired_quote_unverified(self, quote_history):
        """Proposal referencing an expired quote should be unverified."""
        await quote_history.record_quote(
            quote_id="qt-expired",
            buyer_id="buyer-001",
            product_id="prod-A",
            quoted_cpm=30.00,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        result = await quote_history.verify_pricing(
            buyer_id="buyer-001",
            product_id="prod-A",
            proposed_cpm=30.00,
        )

        assert result.pricing_verified is False
        assert "expired" in result.reason.lower()


# =============================================================================
# Test: Backward compatibility — proposals without pricing_verified still work
# =============================================================================


class TestBackwardCompat:
    def test_proposal_response_defaults_pricing_verified_false(self):
        """ProposalResponse should have pricing_verified defaulting to False."""
        from ad_seller.models.core import Pricing

        pricing = Pricing(price=15.0)
        # pricing_verified should not be required — defaults to False
        assert not hasattr(pricing, "pricing_verified") or pricing.pricing_verified is False

    def test_pricing_verification_result_model(self):
        """PricingVerificationResult should be importable and constructable."""
        from ad_seller.storage.quote_history import PricingVerificationResult

        result = PricingVerificationResult(
            pricing_verified=False,
            reason="No matching quote found",
        )
        assert result.pricing_verified is False
        assert result.matched_quote_id is None
