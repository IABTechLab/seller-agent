# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Skip the proposal-review crew when its result could never be used.

The 5-task proposal-review crew was measured at ~646s end to end while the
default request time budget is 20s (``proposal_flow_time_budget_seconds``,
see settings). Under any such budget the crew ALWAYS times out: the request
is answered by the deterministic fallback while the abandoned crew burns
16-40 LLM calls in a worker thread whose result is discarded (CrewAI has no
cancellation API — see ``_abandon_crew_task``).

These tests pin the fix: when the effective budget is positive but below
``proposal_crew_min_budget_seconds`` (default 120, env
``PROPOSAL_CREW_MIN_BUDGET``), the flow never constructs or kicks off the
crew at all and goes straight to the SAME deterministic evaluation, logging
one INFO line. Budget <= 0 still means "no bound" and always runs the crew;
setting the threshold to 0 restores the previous always-kick-off behavior.
The timeout path itself (budget >= threshold, crew slower than budget) is
untouched — its wire answers are pinned by
``test_proposal_flow_time_budget.py``.
"""

import asyncio
import logging
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_proposal_flow_time_budget.py (no LLM call is ever made here).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

# Stub execution_activation_flow (cancel-scope leak on ad-server
# connection failure, unresolved -- issue #60 part 2).
_broken_flows = [
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from ad_seller.config.settings import Settings  # noqa: E402
from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow  # noqa: E402

# =============================================================================
# Helpers (same shapes as test_proposal_flow_time_budget.py)
# =============================================================================


def _make_product(product_id="ctv-premium-sports", base_cpm=35.0, floor_cpm=20.0):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id=product_id,
        name="Premium CTV - Sports",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=base_cpm,
        floor_cpm=floor_cpm,
        minimum_impressions=100000,
    )


def _make_buyer_context(buyer_tier="agency", agency_id="agency-1"):
    from ad_seller.interfaces.api.deps import _build_buyer_context

    return _build_buyer_context(buyer_tier=buyer_tier, agency_id=agency_id)


def _proposal_data(price=25.0, product_id="ctv-premium-sports"):
    return {
        "product_id": product_id,
        "deal_type": "preferred_deal",
        "price": price,
        "impressions": 1_000_000,
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "buyer_id": "buyer-1",
    }


def _flow_settings(budget, min_budget):
    """A minimal settings stand-in for the flow (no real env/keys needed)."""
    return SimpleNamespace(
        proposal_flow_time_budget_seconds=budget,
        proposal_crew_min_budget_seconds=min_budget,
        approval_gate_enabled=False,
        approval_required_flows="",
        approval_required_above_value=0.0,
    )


def _fast_crew(decision="reject"):
    """A crew that answers instantly with a structured review."""
    from ad_seller.models.flow_state import ProposalDecision, ProposalReviewOutput

    review = ProposalReviewOutput(
        decision=ProposalDecision(decision),
        rationale="fast crew decision",
        audience_summary="no audience targeting supplied",
    )
    result = MagicMock()
    result.pydantic = review
    crew = MagicMock()
    crew.kickoff_async = AsyncMock(return_value=result)
    return crew


def _slow_crew(delay_seconds):
    """A crew whose kickoff_async takes ``delay_seconds`` (simulated LLM)."""
    crew = MagicMock()

    async def _slow_kickoff():
        await asyncio.sleep(delay_seconds)
        result = MagicMock()
        result.pydantic = None
        return result

    crew.kickoff_async = AsyncMock(side_effect=_slow_kickoff)
    return crew


async def _run_flow(budget, min_budget, factory, proposal_data=None, products=None):
    """Run ProposalHandlingFlow with a patched crew FACTORY + budgets.

    ``factory`` is patched in as ``create_proposal_review_crew`` itself so
    the tests can assert whether crew construction happened at all.
    """
    with (
        patch(
            "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
            factory,
        ),
        patch(
            "ad_seller.flows.proposal_handling_flow.get_settings",
            return_value=_flow_settings(budget, min_budget),
        ),
        patch(
            "ad_seller.flows.proposal_handling_flow.emit_event",
            new_callable=AsyncMock,
        ),
    ):
        flow = ProposalHandlingFlow()
        result = await flow.handle_proposal_async(
            proposal_id="prop-skip-test",
            proposal_data=proposal_data or _proposal_data(),
            buyer_context=_make_buyer_context(),
            products=products or {"ctv-premium-sports": _make_product()},
        )
    return result


# =============================================================================
# (1) Config: threshold setting default + env override
# =============================================================================


class TestMinBudgetSetting:
    def test_default_threshold(self, monkeypatch):
        """Default 120s: the crew was measured at ~646s, so any budget below
        two minutes guarantees the timeout path and 16-40 discarded LLM
        calls — kicking the crew off there is pure burn."""
        monkeypatch.delenv("PROPOSAL_CREW_MIN_BUDGET", raising=False)
        monkeypatch.delenv("PROPOSAL_CREW_MIN_BUDGET_SECONDS", raising=False)
        settings = Settings(_env_file=None, anthropic_api_key="test-key")
        assert settings.proposal_crew_min_budget_seconds == 120.0

    def test_env_override_short_name(self, monkeypatch):
        """PROPOSAL_CREW_MIN_BUDGET (the documented env knob) overrides."""
        monkeypatch.setenv("PROPOSAL_CREW_MIN_BUDGET", "0")
        settings = Settings(_env_file=None, anthropic_api_key="test-key")
        assert settings.proposal_crew_min_budget_seconds == 0.0

    def test_env_override_field_name(self, monkeypatch):
        """The field-name env spelling works too (settings idiom)."""
        monkeypatch.delenv("PROPOSAL_CREW_MIN_BUDGET", raising=False)
        monkeypatch.setenv("PROPOSAL_CREW_MIN_BUDGET_SECONDS", "45.5")
        settings = Settings(_env_file=None, anthropic_api_key="test-key")
        assert settings.proposal_crew_min_budget_seconds == 45.5


# =============================================================================
# (2) Flow: sub-threshold budget skips the crew entirely
# =============================================================================


@pytest.mark.asyncio
class TestSkipDoomedCrew:
    async def test_sub_threshold_budget_never_constructs_crew(self, caplog):
        """Budget 20 < threshold 120: the crew factory is NEVER called (no
        construction, no kickoff, no orphaned burn), the deterministic
        evaluation answers, and one INFO line states why."""
        factory = MagicMock()
        with caplog.at_level(logging.INFO, logger="ad_seller.flows.proposal_handling_flow"):
            # Price above floor -> deterministic path accepts.
            result = await _run_flow(
                budget=20.0,
                min_budget=120.0,
                factory=factory,
                proposal_data=_proposal_data(price=25.0),
                products={"ctv-premium-sports": _make_product(floor_cpm=20.0)},
            )

        factory.assert_not_called()
        assert result["recommendation"] == "accept"
        assert result["status"] == "accepted"
        assert any("Crew evaluation skipped" in w for w in result["warnings"])
        skip_logs = [r for r in caplog.records if "Skipping proposal-review crew" in r.message]
        assert len(skip_logs) == 1
        assert skip_logs[0].levelno == logging.INFO

    async def test_sub_threshold_skip_matches_deterministic_fallback(self):
        """The skip path yields the SAME decision the deterministic fallback
        gives on the timeout path: a below-floor opener is countered at the
        floor with real counter terms and a persistable history."""
        factory = MagicMock()
        result = await _run_flow(
            budget=20.0,
            min_budget=120.0,
            factory=factory,
            proposal_data=_proposal_data(price=15.0),
            products={"ctv-premium-sports": _make_product(floor_cpm=20.0)},
        )

        factory.assert_not_called()
        assert result["recommendation"] == "counter"
        assert result["status"] == "counter_pending"
        assert result["counter_terms"] is not None
        assert result.get("_negotiation_history") is not None


# =============================================================================
# (3) Flow: the crew still runs when it can be useful
# =============================================================================


@pytest.mark.asyncio
class TestCrewStillRunsWhenUsable:
    async def test_zero_budget_unlimited_still_runs_crew(self):
        """Budget <= 0 means 'no bound' — the crew runs regardless of the
        threshold and its decision drives the outcome (a crew reject on an
        above-floor available offer proves the deterministic path, which
        would accept, did NOT decide)."""
        crew = _fast_crew("reject")
        factory = MagicMock(return_value=crew)
        result = await _run_flow(
            budget=0,
            min_budget=120.0,
            factory=factory,
            proposal_data=_proposal_data(price=25.0),
            products={"ctv-premium-sports": _make_product(floor_cpm=20.0)},
        )

        factory.assert_called_once()
        crew.kickoff_async.assert_awaited_once()
        assert result["recommendation"] == "reject"
        assert not any("Crew evaluation skipped" in w for w in result["warnings"])

    async def test_budget_at_threshold_runs_crew(self):
        """A budget at/above the threshold kicks the crew off as before."""
        crew = _fast_crew("accept")
        factory = MagicMock(return_value=crew)
        result = await _run_flow(budget=120.0, min_budget=120.0, factory=factory)

        factory.assert_called_once()
        crew.kickoff_async.assert_awaited_once()
        assert result["recommendation"] == "accept"
        assert not any("Crew evaluation skipped" in w for w in result["warnings"])

    async def test_threshold_zero_disables_skip(self):
        """Threshold 0 restores the previous behavior: the crew is kicked
        off even under a doomed budget and the TIMEOUT path answers (wire
        answer unchanged — same warning, same deterministic decision)."""
        crew = _slow_crew(30.0)
        factory = MagicMock(return_value=crew)
        result = await _run_flow(budget=0.2, min_budget=0.0, factory=factory)

        factory.assert_called_once()
        assert any("time budget" in w for w in result["warnings"])
        assert not any("Crew evaluation skipped" in w for w in result["warnings"])
        assert result["recommendation"] in ("accept", "counter", "reject")
