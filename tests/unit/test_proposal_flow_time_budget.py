# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Proposal-flow time budget (bead ar-fg58).

S2 live proof 2026-07-21: the seller answers ``POST /proposals`` with a
synchronous LLM crew measured at ~10m46s, while wire buyers time out in
~30s (NegotiationClient default) — every negotiation died at round 0 and
each timed-out request kept burning the full crew server-side.

These tests pin the fix: the proposal-review crew runs under a configurable
time budget (``proposal_flow_time_budget_seconds``, env
``PROPOSAL_FLOW_TIME_BUDGET``). When the budget is exceeded the flow falls
back to the EXISTING deterministic rule-based evaluation (the same path
already used when the LLM fails), the request returns within the budget,
and the ar-alut persistence invariants hold (proposal + negotiation
history persisted; cold REST continuation works). The abandoned crew task
is detached and logged — CrewAI offers no true cancellation.
"""

import asyncio
import logging
import os
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_negotiation_cold_start.py (no LLM call is ever made here).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used in test_negotiation_cold_start.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.config.settings import Settings  # noqa: E402
from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.services import negotiation_service  # noqa: E402

# =============================================================================
# Helpers
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


def _make_catalog(products=None):
    products = products or {p.product_id: p for p in [_make_product()]}
    return {
        "products": products,
        "inventory_types": sorted({p.inventory_type for p in products.values()}),
    }


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


def _proposal_request(product_id="ctv-premium-sports", price=25.0):
    request = MagicMock()
    request.product_id = product_id
    request.deal_type = "preferred_deal"
    request.price = price
    request.impressions = 1_000_000
    request.start_date = "2026-08-01"
    request.end_date = "2026-08-31"
    request.buyer_id = "buyer-1"
    return request


def _flow_settings(budget):
    """A minimal settings stand-in for the flow (no real env/keys needed)."""
    return SimpleNamespace(
        proposal_flow_time_budget_seconds=budget,
        approval_gate_enabled=False,
        approval_required_flows="",
        approval_required_above_value=0.0,
    )


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


def _fast_crew(decision="accept"):
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


async def _run_flow(budget, crew, proposal_data=None, products=None):
    """Run ProposalHandlingFlow with a patched crew + budget; return result."""
    with (
        patch(
            "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
            return_value=crew,
        ),
        patch(
            "ad_seller.flows.proposal_handling_flow.get_settings",
            return_value=_flow_settings(budget),
        ),
        patch(
            "ad_seller.flows.proposal_handling_flow.emit_event",
            new_callable=AsyncMock,
        ) as mock_emit,
    ):
        flow = ProposalHandlingFlow()
        result = await flow.handle_proposal_async(
            proposal_id="prop-budget-test",
            proposal_data=proposal_data or _proposal_data(),
            buyer_context=_make_buyer_context(),
            products=products or {"ctv-premium-sports": _make_product()},
        )
    return result, mock_emit


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.delete = AsyncMock(side_effect=lambda k: store.pop(k, None))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_proposal = AsyncMock(side_effect=lambda pid: store.get(f"proposal:{pid}"))
    storage.set_proposal = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"proposal:{pid}", data)
    )
    storage.get_product = AsyncMock(side_effect=lambda pid: store.get(f"product:{pid}"))
    storage.set_product = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"product:{pid}", data)
    )
    storage.get_negotiation = AsyncMock(side_effect=lambda pid: store.get(f"negotiation:{pid}"))
    storage.set_negotiation = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"negotiation:{pid}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# (1) Config: budget setting default + env override
# =============================================================================


class TestBudgetSetting:
    def test_default_budget_is_wire_compatible(self, monkeypatch):
        """Default must be tens of seconds — under the buyer's 30s default
        NegotiationClient timeout (ar-vc4m makes that configurable)."""
        monkeypatch.delenv("PROPOSAL_FLOW_TIME_BUDGET", raising=False)
        monkeypatch.delenv("PROPOSAL_FLOW_TIME_BUDGET_SECONDS", raising=False)
        settings = Settings(_env_file=None, anthropic_api_key="test-key")
        assert settings.proposal_flow_time_budget_seconds == 20.0

    def test_env_override_short_name(self, monkeypatch):
        """PROPOSAL_FLOW_TIME_BUDGET (the documented env knob) overrides."""
        monkeypatch.setenv("PROPOSAL_FLOW_TIME_BUDGET", "45")
        settings = Settings(_env_file=None, anthropic_api_key="test-key")
        assert settings.proposal_flow_time_budget_seconds == 45.0

    def test_env_override_field_name(self, monkeypatch):
        """The field-name env spelling works too (settings idiom)."""
        monkeypatch.delenv("PROPOSAL_FLOW_TIME_BUDGET", raising=False)
        monkeypatch.setenv("PROPOSAL_FLOW_TIME_BUDGET_SECONDS", "12.5")
        settings = Settings(_env_file=None, anthropic_api_key="test-key")
        assert settings.proposal_flow_time_budget_seconds == 12.5


# =============================================================================
# (2) Flow: slow crew falls back deterministically within budget
# =============================================================================


@pytest.mark.asyncio
class TestBudgetFallback:
    async def test_slow_crew_falls_back_within_budget(self):
        """A crew slower than the budget must NOT bound the request: the flow
        falls back to the deterministic evaluation and returns promptly."""
        started = time.monotonic()
        # Price below floor -> deterministic path recommends a counter.
        result, _ = await _run_flow(
            budget=0.2,
            crew=_slow_crew(30.0),
            proposal_data=_proposal_data(price=15.0),
            products={"ctv-premium-sports": _make_product(floor_cpm=20.0)},
        )
        elapsed = time.monotonic() - started

        assert elapsed < 5.0, f"flow took {elapsed:.1f}s — budget did not bound it"
        assert result["recommendation"] == "counter"
        assert result["status"] == "counter_pending"
        # The fallback produced real counter terms via the NegotiationEngine
        # and exposed the NegotiationHistory for persistence (ar-alut).
        assert result["counter_terms"] is not None
        assert result.get("_negotiation_history") is not None
        assert any("time budget" in w for w in result["warnings"])

    async def test_slow_crew_fallback_accepts_above_floor(self):
        """Deterministic fallback accepts an above-floor proposal — within
        the budget, not after waiting out the crew."""
        started = time.monotonic()
        result, _ = await _run_flow(
            budget=0.2,
            crew=_slow_crew(30.0),
            proposal_data=_proposal_data(price=25.0),
            products={"ctv-premium-sports": _make_product(floor_cpm=20.0)},
        )
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"flow took {elapsed:.1f}s — budget did not bound it"
        assert result["recommendation"] == "accept"
        assert result["status"] == "accepted"

    async def test_budget_timeout_logs_abandoned_crew(self, caplog):
        """The over-budget crew task is detached and LOGGED (Bug J was
        invisible orphaned burn) — both at abandonment and at completion."""
        with caplog.at_level(logging.WARNING, logger="ad_seller.flows.proposal_handling_flow"):
            result, _ = await _run_flow(budget=0.05, crew=_slow_crew(0.4))
            assert any("time budget" in w for w in result["warnings"])
            assert any("abandon" in rec.message.lower() for rec in caplog.records)
            # Let the orphaned task finish; its completion must be logged too.
            await asyncio.sleep(0.6)
        assert any(
            "orphan" in rec.message.lower() or "discard" in rec.message.lower()
            for rec in caplog.records
        )


# =============================================================================
# (3) Flow: fast crew path unchanged; budget<=0 disables the bound
# =============================================================================


@pytest.mark.asyncio
class TestCrewPathUnchanged:
    async def test_fast_crew_result_used_unchanged(self):
        """A crew answering inside the budget drives the decision exactly as
        before — no fallback, no budget warning, evaluated event emitted."""
        result, mock_emit = await _run_flow(budget=20.0, crew=_fast_crew("accept"))
        assert result["recommendation"] == "accept"
        assert result["status"] == "accepted"
        assert not any("time budget" in w for w in result["warnings"])
        assert mock_emit.await_count >= 1  # proposal.evaluated still emitted

    async def test_zero_budget_disables_bound(self):
        """budget <= 0 disables the bound (pre-ar-fg58 unbounded behavior)."""
        result, _ = await _run_flow(budget=0, crew=_slow_crew(0.3))
        # The slow crew ran to completion (returns pydantic None -> fallback
        # via the EXISTING review-is-None path, not the budget path).
        assert not any("time budget" in w for w in result["warnings"])

    async def test_crew_failure_fallback_unchanged(self):
        """The existing LLM-failure fallback path is untouched."""
        crew = MagicMock()
        crew.kickoff_async = AsyncMock(side_effect=RuntimeError("boom"))
        result, _ = await _run_flow(budget=20.0, crew=crew)
        assert result["recommendation"] in ("accept", "counter", "reject")
        assert any("Crew evaluation failed" in w for w in result["warnings"])


# =============================================================================
# (4) Service + REST: budget fallback persists; cold continuation works
# =============================================================================


@pytest.mark.asyncio
class TestFallbackPersistence:
    async def test_budget_fallback_persists_proposal_and_history_cold(
        self, client, mock_storage
    ):
        """End-to-end with the REAL flow and a slow crew: POST /proposals
        answers within budget with a deterministic decision, persists the
        proposal + product (ar-alut invariants — exactly as the crew path
        does), and POST /api/v1/negotiations/messages works cold afterward."""
        catalog = _make_catalog(
            {"ctv-premium-sports": _make_product(base_cpm=35.0, floor_cpm=20.0)}
        )
        with (
            patch(
                "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                return_value=_slow_crew(30.0),
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.get_settings",
                return_value=_flow_settings(0.2),
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.emit_event",
                new_callable=AsyncMock,
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=catalog,
            ),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with client as c:
                started = time.monotonic()
                submit = await c.post(
                    "/proposals",
                    json={
                        "product_id": "ctv-premium-sports",
                        "deal_type": "preferred_deal",
                        "price": 25.0,  # above floor -> deterministic accept
                        "impressions": 1000000,
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-31",
                        "buyer_id": "buyer-1",
                        "agency_id": "agency-1",
                    },
                )
                elapsed = time.monotonic() - started

                assert submit.status_code == 200, submit.text
                assert elapsed < 5.0, f"POST /proposals took {elapsed:.1f}s"
                body = submit.json()
                proposal_id = body["proposal_id"]
                # Deterministic fallback: above-floor price is accepted.
                assert body["recommendation"] == "accept"

                # ar-alut invariants: proposal + product pricing persisted so
                # the negotiation surface is reachable cold.
                assert f"proposal:{proposal_id}" in mock_storage._store
                assert "product:ctv-premium-sports" in mock_storage._store

                # Cold continuation: a counter on the REST surface opens a
                # negotiation off the stored proposal (was a 404 pre-ar-alut).
                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-budget-1",
                        "action": "counter",
                        "proposal_id": proposal_id,
                        "buyer_price": {"amount_micros": 22_000_000, "currency": "USD"},
                    },
                )

        assert resp.status_code == 200, resp.text
        round_body = resp.json()
        assert round_body["negotiation_id"]
        assert round_body["round"]["round_number"] == 1
        stored_history = mock_storage._store.get(f"negotiation:{proposal_id}")
        assert stored_history is not None
        assert stored_history["status"] == "active"
