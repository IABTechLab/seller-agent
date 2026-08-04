# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Causeful error taxonomy for the proposal path (seller issue #34).

``POST /proposals`` used to answer ``status=failed`` with an EMPTY
``errors[]`` — identically for a valid-but-unresolvable ``product_id`` and
for outright garbage. These tests pin the fix: every ProposalHandlingFlow
stage that fails the proposal records a machine-readable
``{"stage": ..., "code": ..., "detail": ...}`` entry (FD-6 structured-error
house style, snake_case codes like the shared ``ErrorCode``), and an empty
``errors[]`` alongside ``status=failed`` is impossible at every seam
(flow result, service, wire).

Stage -> code taxonomy pinned here:

- ``receive_proposal``     -> ``missing_required_fields``
- ``validate_product``     -> ``product_not_found``
- ``validate_audience``    -> ``audience_validation``
- ``evaluate_pricing``     -> ``pricing``
- ``check_availability``   -> ``availability``
- ``run_crew_evaluation``  -> ``crew_evaluation_error``
- whole-flow crash / unattributed failure -> stage ``flow``, code ``internal``
"""

import os
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_proposal_flow_time_budget.py (no LLM call is ever made here).
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

from ad_seller.flows.proposal_handling_flow import (  # noqa: E402
    ProposalHandlingFlow,
    ProposalState,
)
from ad_seller.interfaces.api.main import (  # noqa: E402
    _get_optional_api_key_record,
    app,
)
from ad_seller.models.flow_state import ExecutionStatus  # noqa: E402
from ad_seller.services import negotiation_service  # noqa: E402

pytestmark = pytest.mark.asyncio


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


def _proposal_data(price=25.0, product_id="ctv-premium-sports", **overrides):
    data = {
        "product_id": product_id,
        "deal_type": "preferred_deal",
        "price": price,
        "impressions": 1_000_000,
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "buyer_id": "buyer-1",
    }
    data.update(overrides)
    return data


def _flow_settings():
    return SimpleNamespace(
        proposal_flow_time_budget_seconds=0.0,
        approval_gate_enabled=False,
        approval_required_flows="",
        approval_required_above_value=0.0,
    )


async def _run_flow(proposal_data, products, crew=None, packages=None):
    """Run the real flow end-to-end with the crew patched out."""
    crew = crew if crew is not None else MagicMock()
    with (
        patch(
            "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
            return_value=crew,
        ),
        patch(
            "ad_seller.flows.proposal_handling_flow.get_settings",
            return_value=_flow_settings(),
        ),
        patch(
            "ad_seller.flows.proposal_handling_flow.emit_event",
            new_callable=AsyncMock,
        ),
    ):
        flow = ProposalHandlingFlow()
        if packages is not None:
            flow._packages_for_audience_validation = packages
        result = await flow.handle_proposal_async(
            proposal_id="prop-taxonomy-test",
            proposal_data=proposal_data,
            buyer_context=None,
            products=products,
        )
    return result


def _build_bare_flow() -> ProposalHandlingFlow:
    """Minimally-initialized flow for direct stage calls (no kickoff).

    Same ``__new__`` idiom as test_audience_plan_validation.py.
    """
    flow = ProposalHandlingFlow.__new__(ProposalHandlingFlow)
    flow._settings = None
    flow._audience_validation = {}
    flow._packages_for_audience_validation = {}
    flow._state_lock = threading.Lock()
    state = ProposalState(
        flow_id="test-flow",
        flow_type="proposal_handling",
        seller_organization_id="test",
        seller_name="Test",
    )
    state.proposal_id = "p1"
    flow._state = state
    return flow


def _entries(result, code=None, stage=None):
    """Filter structured error entries by code and/or stage."""
    found = []
    for entry in result["errors"]:
        assert isinstance(entry, dict), f"errors[] entry is not structured: {entry!r}"
        assert set(entry) >= {"stage", "code", "detail"}, entry
        if code is not None and entry["code"] != code:
            continue
        if stage is not None and entry["stage"] != stage:
            continue
        found.append(entry)
    return found


# =============================================================================
# (1) Flow stages record distinct, causeful errors
# =============================================================================


class TestStageErrorTaxonomy:
    async def test_product_not_found_records_causeful_error(self):
        """A well-formed but unresolvable product_id must name its cause."""
        result = await _run_flow(
            _proposal_data(product_id="prod-does-not-exist"),
            products={"ctv-premium-sports": _make_product()},
        )
        assert result["status"] == "failed"
        assert result["errors"], "status=failed must never have empty errors[]"
        matches = _entries(result, code="product_not_found", stage="validate_product")
        assert matches, result["errors"]
        assert "prod-does-not-exist" in matches[0]["detail"]

    async def test_missing_required_fields_is_a_distinct_cause(self):
        """Garbage (missing fields) must be distinguishable from a bad id."""
        result = await _run_flow(
            {"product_id": "ctv-premium-sports"},  # no impressions/dates
            products={"ctv-premium-sports": _make_product()},
        )
        assert result["status"] == "failed"
        assert result["errors"]
        matches = _entries(
            result, code="missing_required_fields", stage="receive_proposal"
        )
        assert matches, result["errors"]
        assert "impressions" in matches[0]["detail"]
        # Distinctness pin: this cause is NOT the product-not-found cause.
        assert matches[0]["code"] != "product_not_found"
        assert not _entries(result, code="product_not_found")

    async def test_audience_hard_reject_records_audience_validation_error(self):
        """The §5.7 layer-3 hard reject must carry the audience code."""
        packages = {
            "pkg-a": {
                "audience_capabilities": {
                    "standard_segment_ids": ["3-7"],
                    "contextual_segment_ids": ["IAB1-2"],
                }
            }
        }
        result = await _run_flow(
            _proposal_data(
                audience_plan={
                    "primary": {"type": "standard", "identifier": "9-99"},
                }
            ),
            products={"ctv-premium-sports": _make_product()},
            packages=packages,
        )
        assert result["status"] == "failed"
        assert result["errors"]
        matches = _entries(result, code="audience_validation", stage="validate_audience")
        assert matches, result["errors"]
        assert "overlap" in matches[0]["detail"]

    async def test_pricing_stage_error_records_pricing_code(self):
        """An exploding pricing evaluation must fail with the pricing code,
        not resolve failed with no detail."""
        with patch(
            "ad_seller.services.catalog_service.check_avails",
            side_effect=RuntimeError("avails backend down"),
        ):
            result = await _run_flow(
                _proposal_data(),
                products={"ctv-premium-sports": _make_product()},
            )
        assert result["status"] == "failed"
        assert result["errors"]
        matches = _entries(result, code="pricing", stage="evaluate_pricing")
        assert matches, result["errors"]
        assert "avails backend down" in matches[0]["detail"]

    async def test_availability_stage_error_records_availability_code(self):
        """An exploding availability check must fail with the availability
        code (direct stage call — the stage body is the unit under test)."""

        class _BoomEvaluation:
            def __getattr__(self, name):
                raise RuntimeError("availability introspection exploded")

        flow = _build_bare_flow()
        flow.state.status = ExecutionStatus.EVALUATING
        flow.state.evaluation = _BoomEvaluation()

        await flow.check_availability()

        assert flow.state.status == ExecutionStatus.FAILED
        details = flow.state.error_details
        assert details, "availability failure must record a structured error"
        assert details[0]["stage"] == "check_availability"
        assert details[0]["code"] == "availability"
        assert "availability introspection exploded" in details[0]["detail"]

    async def test_crew_creation_error_records_crew_evaluation_error(self):
        """A crew that cannot even be constructed must fail the proposal with
        the crew_evaluation_error code instead of raising out of the flow."""
        with (
            patch(
                "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                side_effect=RuntimeError("crew wiring broken"),
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.get_settings",
                return_value=_flow_settings(),
            ),
        ):
            flow = ProposalHandlingFlow()
            result = await flow.handle_proposal_async(
                proposal_id="prop-crew-error",
                proposal_data=_proposal_data(),
                buyer_context=None,
                products={"ctv-premium-sports": _make_product()},
            )
        assert result["status"] == "failed"
        assert result["errors"]
        matches = _entries(
            result, code="crew_evaluation_error", stage="run_crew_evaluation"
        )
        assert matches, result["errors"]
        assert "crew wiring broken" in matches[0]["detail"]

    async def test_crew_failure_with_broken_fallback_records_crew_error(self):
        """Crew failed AND the deterministic fallback raised: the proposal
        fails with the crew_evaluation_error code, not a bare failed."""
        crew = MagicMock()
        crew.kickoff_async = AsyncMock(side_effect=RuntimeError("llm down"))
        with patch.object(
            ProposalHandlingFlow,
            "_fallback_evaluation",
            side_effect=RuntimeError("fallback also broken"),
        ):
            result = await _run_flow(
                _proposal_data(),
                products={"ctv-premium-sports": _make_product()},
                crew=crew,
            )
        assert result["status"] == "failed"
        assert result["errors"]
        matches = _entries(
            result, code="crew_evaluation_error", stage="run_crew_evaluation"
        )
        assert matches, result["errors"]

    async def test_human_readable_strings_still_on_state(self):
        """flow.state.errors keeps human-readable strings (internal seam
        pinned by existing tests / e2e helpers)."""
        result = await _run_flow(
            _proposal_data(product_id="prod-does-not-exist"),
            products={"ctv-premium-sports": _make_product()},
        )
        assert result["status"] == "failed"
        # The wire entries are dicts; each carries a non-empty detail string.
        for entry in result["errors"]:
            assert isinstance(entry["detail"], str) and entry["detail"]


# =============================================================================
# (2) Invariant: status=failed  =>  errors[] non-empty (every seam)
# =============================================================================


class TestFailedNeverEmptyErrors:
    async def test_flow_result_backfills_unattributed_failure(self):
        """A FAILED status with nothing recorded must still surface a
        structured internal error in the flow result."""
        flow = _build_bare_flow()
        flow.state.status = ExecutionStatus.FAILED
        assert not flow.state.errors

        result = flow._build_result()

        assert result["status"] == "failed"
        assert result["errors"], "failed with empty errors[] must be impossible"
        entry = result["errors"][0]
        assert entry["stage"] == "flow"
        assert entry["code"] == "internal"
        assert entry["detail"]

    async def test_service_backfills_empty_errors_on_failed(self):
        """Even if a (mocked/legacy) flow returns failed with empty errors,
        the service answers with a non-empty causeful errors[]."""
        flow = MagicMock()
        flow.handle_proposal_async = AsyncMock(
            return_value={"recommendation": "reject", "status": "failed", "errors": []}
        )
        verification = MagicMock()
        verification.pricing_verified = False
        verification.reason = "no quote"
        store = MagicMock()
        store.verify_pricing = AsyncMock(return_value=verification)

        request = MagicMock()
        request.product_id = "prod-1"
        request.deal_type = "preferred_deal"
        request.price = 20.0
        request.impressions = 1_000_000
        request.start_date = "2026-08-01"
        request.end_date = "2026-08-31"
        request.buyer_id = "buyer-1"
        ctx = MagicMock()
        ctx.get_pricing_key = MagicMock(return_value="agency-1")

        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow),
            patch("ad_seller.storage.factory.get_storage", return_value=AsyncMock()),
            patch(
                "ad_seller.storage.quote_history.QuoteHistoryStore",
                return_value=store,
            ),
        ):
            result = await negotiation_service.submit_proposal(
                request, ctx, {"products": {}}
            )

        assert result["status"] == "failed"
        assert result["errors"]
        assert result["errors"][0]["code"] == "internal"
        assert result["errors"][0]["stage"] == "flow"

    async def test_service_flow_crash_yields_structured_internal_error(self):
        """A whole-flow crash degrades to a structured causeful failure."""
        flow = MagicMock()
        flow.handle_proposal_async = AsyncMock(side_effect=RuntimeError("crew exploded"))
        request = MagicMock()
        request.product_id = "prod-1"
        request.deal_type = "preferred_deal"
        request.price = 20.0
        request.impressions = 1_000_000
        request.start_date = "2026-08-01"
        request.end_date = "2026-08-31"
        request.buyer_id = "buyer-1"

        with patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow):
            result = await negotiation_service.submit_proposal(
                request, MagicMock(), {"products": {}}
            )

        assert result["status"] == "failed"
        assert result["errors"]
        entry = result["errors"][0]
        assert entry["stage"] == "flow"
        assert entry["code"] == "internal"
        assert "crew exploded" in entry["detail"]


# =============================================================================
# (3) Wire: POST /proposals answers HTTP 200 / status=failed with causes
# =============================================================================


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.set_proposal = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"proposal:{pid}", data)
    )
    storage.set_product = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"product:{pid}", data)
    )
    storage._store = store
    return storage


class TestWireContract:
    async def test_unresolvable_product_id_wire_shape(self, client, mock_storage):
        """HTTP 200 + status=failed is unchanged; errors[] now carries the
        machine-readable cause."""
        catalog = {
            "products": {"ctv-premium-sports": _make_product()},
            "inventory_types": ["ctv"],
        }
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=catalog,
            ),
        ):
            async with client as c:
                resp = await c.post(
                    "/proposals",
                    json=_proposal_data(product_id="prod-does-not-exist"),
                )

        assert resp.status_code == 200, resp.text  # wire-compat: NOT a 4xx/5xx
        body = resp.json()
        assert body["status"] == "failed"
        assert body["errors"], "wire commitment: failed => errors[] non-empty"
        codes = {e["code"] for e in body["errors"]}
        assert "product_not_found" in codes
        entry = next(e for e in body["errors"] if e["code"] == "product_not_found")
        assert entry["stage"] == "validate_product"
        assert "prod-does-not-exist" in entry["detail"]
