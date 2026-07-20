# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the async/hardened proposal path in negotiation_service.

main (PR #29 + d5373ba) made the monolith's submit_proposal run the crew via
``handle_proposal_async`` (never blocking the event loop with ``kickoff()``)
and degrade a flow failure to a structured reject/failed response instead of
a raw 500. The v2 service-layer refactor still called the sync
``handle_proposal``; these tests pin the ported behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_seller.services import negotiation_service

pytestmark = pytest.mark.asyncio


def _request():
    request = MagicMock()
    request.product_id = "prod-1"
    request.deal_type = "preferred_deal"
    request.price = 20.0
    request.impressions = 1_000_000
    request.start_date = "2026-08-01"
    request.end_date = "2026-08-31"
    request.buyer_id = "buyer-1"
    return request


def _buyer_context():
    ctx = MagicMock()
    ctx.get_pricing_key = MagicMock(return_value="agency-1")
    return ctx


def _catalog():
    return {"products": {}}


def _mock_quote_history(verified=True, reason="ok"):
    verification = MagicMock()
    verification.pricing_verified = verified
    verification.reason = reason
    store = MagicMock()
    store.verify_pricing = AsyncMock(return_value=verification)
    return store


class TestSubmitProposalAsyncHardening:
    async def test_uses_handle_proposal_async_not_sync_kickoff(self):
        """The service must run the flow via handle_proposal_async — the sync
        handle_proposal blocks the event loop with kickoff()."""
        flow = MagicMock()
        flow.handle_proposal_async = AsyncMock(
            return_value={"recommendation": "accept", "status": "completed"}
        )
        flow.handle_proposal = MagicMock(
            side_effect=AssertionError("sync handle_proposal must not be called")
        )

        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow),
            patch("ad_seller.storage.factory.get_storage", return_value=AsyncMock()),
            patch(
                "ad_seller.storage.quote_history.QuoteHistoryStore",
                return_value=_mock_quote_history(),
            ),
        ):
            result = await negotiation_service.submit_proposal(
                _request(), _buyer_context(), _catalog()
            )

        flow.handle_proposal_async.assert_awaited_once()
        assert result["recommendation"] == "accept"
        assert result["status"] == "completed"

    async def test_flow_failure_degrades_to_structured_reject(self):
        """A crew/flow crash must return reject/failed, not raise a 500."""
        flow = MagicMock()
        flow.handle_proposal_async = AsyncMock(side_effect=RuntimeError("crew exploded"))

        with patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow):
            result = await negotiation_service.submit_proposal(
                _request(), _buyer_context(), _catalog()
            )

        assert result["recommendation"] == "reject"
        assert result["status"] == "failed"
        assert any("crew exploded" in e for e in result["errors"])

    async def test_missing_recommendation_defaults_to_reject(self):
        """A flow that completes without a recommendation must not KeyError."""
        flow = MagicMock()
        flow.handle_proposal_async = AsyncMock(return_value={})

        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow),
            patch("ad_seller.storage.factory.get_storage", return_value=AsyncMock()),
            patch(
                "ad_seller.storage.quote_history.QuoteHistoryStore",
                return_value=_mock_quote_history(),
            ),
        ):
            result = await negotiation_service.submit_proposal(
                _request(), _buyer_context(), _catalog()
            )

        assert result["recommendation"] == "reject"
        assert result["status"] == "failed"
