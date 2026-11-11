"""Issue #118: a proposal says whether the crew or the fallback decided, and why.

Flow-level tests drive ``ProposalHandlingFlow`` through every evaluation
path with a stand-in crew. The REST tests run the real app and the real
flow against a real SQLite backend; only the crew factory is replaced.
"""

import json
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

_broken_flows = ["ad_seller.flows.execution_activation_flow"]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.events.bus import InMemoryEventBus  # noqa: E402
from ad_seller.events.models import EventType  # noqa: E402
from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow  # noqa: E402
from ad_seller.interfaces.api import deps  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.llm import MissingApiKeyError  # noqa: E402
from ad_seller.storage.sqlite_backend import SQLiteBackend  # noqa: E402
from tests.unit.test_proposal_crew_skip import (  # noqa: E402
    _fast_crew,
    _make_buyer_context,
    _make_product,
    _proposal_data,
    _slow_crew,
)

_SECRET = "provider said 401 for key sk-live-abcd1234 at https://internal-llm.corp:8443"


def _settings(budget=0.0, min_budget=0.0, approval=False):
    return SimpleNamespace(
        proposal_flow_time_budget_seconds=budget,
        proposal_crew_min_budget_seconds=min_budget,
        approval_gate_enabled=approval,
        approval_required_flows="proposal_decision" if approval else "",
        approval_required_above_value=0.0,
    )


def _crew_returning_no_review():
    result = MagicMock()
    result.pydantic = None
    crew = MagicMock()
    crew.kickoff_async = AsyncMock(return_value=result)
    return crew


def _crew_raising(exc):
    crew = MagicMock()
    crew.kickoff_async = AsyncMock(side_effect=exc)
    return crew


_PATHS = {
    "crew": (dict(), lambda: MagicMock(return_value=_fast_crew("counter"))),
    "no_review": (dict(), lambda: MagicMock(return_value=_crew_returning_no_review())),
    "crew_raises": (dict(), lambda: MagicMock(return_value=_crew_raising(RuntimeError(_SECRET)))),
    "timeout": (dict(budget=0.2), lambda: MagicMock(return_value=_slow_crew(30.0))),
    "skipped": (dict(budget=20.0, min_budget=120.0), lambda: MagicMock()),
    "no_key": (dict(), lambda: MagicMock(side_effect=MissingApiKeyError(_SECRET))),
}


async def _run_flow(path, proposal_data=None, products=None):
    settings_kwargs, factory = _PATHS[path]
    emit = AsyncMock()
    with (
        patch("ad_seller.flows.proposal_handling_flow.create_proposal_review_crew", factory()),
        patch(
            "ad_seller.flows.proposal_handling_flow.get_settings",
            return_value=_settings(**settings_kwargs),
        ),
        patch("ad_seller.flows.proposal_handling_flow.emit_event", emit),
    ):
        result = await ProposalHandlingFlow().handle_proposal_async(
            proposal_id="prop-118",
            proposal_data=proposal_data or _proposal_data(price=25.0),
            buyer_context=_make_buyer_context(),
            products=products or {"ctv-premium-sports": _make_product(floor_cpm=20.0)},
        )
    return result, emit


def _evaluated_events(emit):
    return [
        c.kwargs["payload"]
        for c in emit.await_args_list
        if c.kwargs.get("event_type") == EventType.PROPOSAL_EVALUATED
    ]


class TestFlowEvaluator:
    @pytest.mark.parametrize(
        "path, evaluated_by, reason, warning",
        [
            ("crew", "crew", None, None),
            ("no_review", "fallback", "crew_no_structured_output", "no structured review"),
            ("crew_raises", "fallback", "crew_error", "Crew evaluation failed"),
            ("timeout", "fallback", "crew_timeout", "exceeded time budget"),
            ("skipped", "fallback", "crew_skipped", "Crew evaluation skipped"),
            ("no_key", "fallback", "crew_not_configured", "Crew evaluation failed"),
        ],
    )
    async def test_every_path_records_who_decided(self, path, evaluated_by, reason, warning):
        result, _ = await _run_flow(path)

        assert result["evaluated_by"] == evaluated_by
        assert result["fallback_reason"] == reason
        if warning is None:
            assert not any("Crew evaluation" in w for w in result["warnings"])
        else:
            assert any(warning in w for w in result["warnings"])

    async def test_crew_decision_is_the_recommendation(self):
        result, emit = await _run_flow("crew")

        assert result["recommendation"] == "counter"
        assert _evaluated_events(emit) == [
            {
                "recommendation": "counter",
                "evaluated_by": "crew",
                "fallback_reason": None,
                "evaluation": result["evaluation"],
            }
        ]

    async def test_event_after_unusable_crew_output_names_the_fallback(self):
        result, emit = await _run_flow("no_review")

        [payload] = _evaluated_events(emit)
        assert payload["evaluated_by"] == "fallback"
        assert payload["fallback_reason"] == "crew_no_structured_output"
        assert payload["recommendation"] == result["recommendation"] == "accept"

    @pytest.mark.parametrize("path", ["crew_raises", "timeout", "skipped", "no_key"])
    async def test_failed_crew_emits_no_evaluated_event(self, path):
        _, emit = await _run_flow(path)

        assert _evaluated_events(emit) == []

    async def test_crew_construction_error_fails_without_an_evaluator(self):
        with (
            patch(
                "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                MagicMock(side_effect=ValueError("bad agent config")),
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.get_settings",
                return_value=_settings(),
            ),
            patch("ad_seller.flows.proposal_handling_flow.emit_event", AsyncMock()),
        ):
            result = await ProposalHandlingFlow().handle_proposal_async(
                proposal_id="prop-118",
                proposal_data=_proposal_data(),
                buyer_context=_make_buyer_context(),
                products={"ctv-premium-sports": _make_product()},
            )

        assert result["status"] == "failed"
        assert result["evaluated_by"] is None
        assert result["fallback_reason"] is None

    async def test_fallback_that_also_errors_keeps_the_reason_but_no_evaluator(self):
        with patch.object(
            ProposalHandlingFlow, "_fallback_evaluation", side_effect=RuntimeError("boom")
        ):
            result, _ = await _run_flow("crew_raises")

        assert result["status"] == "failed"
        assert result["evaluated_by"] is None
        assert result["fallback_reason"] == "crew_error"
        assert result["errors"][0]["code"] == "crew_evaluation_error"

    async def test_unknown_product_fails_before_any_evaluator(self):
        result, _ = await _run_flow("crew", proposal_data=_proposal_data(product_id="nope"))

        assert result["status"] == "failed"
        assert result["evaluated_by"] is None
        assert result["fallback_reason"] is None


@pytest.fixture
async def storage(tmp_path):
    backend = SQLiteBackend(f"sqlite:///{tmp_path}/proposals.db")
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


@pytest.fixture
def run_app(storage, event_bus):
    catalog = {"products": {"ctv-premium-sports": _make_product(floor_cpm=20.0)}}

    async def _post(path, approval=False, body=None):
        settings_kwargs, factory = _PATHS[path]
        app.dependency_overrides[_get_optional_api_key_record] = lambda: None
        app.dependency_overrides[deps._require_operator_api_key_record] = lambda: MagicMock()
        try:
            with (
                patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
                patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
                patch(
                    "ad_seller.interfaces.api.main._get_static_product_catalog",
                    AsyncMock(return_value=catalog),
                ),
                patch(
                    "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                    factory(),
                ),
                patch(
                    "ad_seller.flows.proposal_handling_flow.get_settings",
                    return_value=_settings(approval=approval, **settings_kwargs),
                ),
            ):
                async with httpx.AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    return await client.post("/proposals", json=body or _proposal_data(price=25.0))
        finally:
            app.dependency_overrides.clear()

    return _post


class TestProposalsEndpoint:
    @pytest.mark.parametrize(
        "path, evaluated_by, reason",
        [
            ("crew", "crew", None),
            ("crew_raises", "fallback", "crew_error"),
            ("timeout", "fallback", "crew_timeout"),
            ("skipped", "fallback", "crew_skipped"),
            ("no_key", "fallback", "crew_not_configured"),
            ("no_review", "fallback", "crew_no_structured_output"),
        ],
    )
    @pytest.mark.parametrize("approval", [False, True])
    async def test_response_names_the_evaluator(
        self, run_app, storage, path, evaluated_by, reason, approval
    ):
        resp = await run_app(path, approval=approval)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["evaluated_by"] == evaluated_by
        assert body["fallback_reason"] == reason
        assert body["status"] == ("pending_approval" if approval else body["status"])
        assert body["errors"] == []
        stored = await storage.get_proposal(body["proposal_id"])
        assert (stored["evaluated_by"], stored["fallback_reason"]) == (evaluated_by, reason)

    @pytest.mark.parametrize("path", ["crew_raises", "no_key"])
    async def test_raw_failure_text_never_reaches_the_buyer(self, run_app, path):
        resp = await run_app(path, approval=False)

        assert resp.json()["fallback_reason"] in ("crew_error", "crew_not_configured")
        assert "sk-live" not in resp.text
        assert "internal-llm" not in resp.text

    @pytest.mark.parametrize("path", ["crew_raises", "no_key"])
    async def test_raw_failure_text_reaches_the_approver_but_not_the_buyer(
        self, run_app, storage, path
    ):
        from ad_seller.services import approval_service

        resp = await run_app(path, approval=True)

        assert "sk-live" not in resp.text
        assert "internal-llm" not in resp.text
        with patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)):
            approval = await approval_service.get_approval(resp.json()["approval_id"])
        context = approval["request"]["context"]
        assert context["evaluated_by"] == "fallback"
        assert context["fallback_reason"] in ("crew_error", "crew_not_configured")
        assert any(_SECRET in w for w in context["warnings"])

    async def test_crew_decision_has_no_warnings_in_the_approval(self, run_app, storage):
        from ad_seller.services import approval_service

        resp = await run_app("crew", approval=True)

        with patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)):
            approval = await approval_service.get_approval(resp.json()["approval_id"])
        context = approval["request"]["context"]
        assert (context["evaluated_by"], context["fallback_reason"]) == ("crew", None)
        assert not any("Crew evaluation" in w for w in context["warnings"])

    async def test_flow_exception_reports_no_evaluator(self, run_app):
        with patch.object(
            ProposalHandlingFlow, "handle_proposal_async", AsyncMock(side_effect=RuntimeError("x"))
        ):
            resp = await run_app("crew")

        body = resp.json()
        assert body["status"] == "failed"
        assert body["evaluated_by"] is None
        assert body["fallback_reason"] is None

    async def test_unknown_product_reports_no_evaluator(self, run_app):
        resp = await run_app("crew", body=_proposal_data(product_id="nope"))

        body = resp.json()
        assert body["status"] == "failed"
        assert body["evaluated_by"] is None

    async def test_response_keeps_every_existing_field(self, run_app):
        resp = await run_app("crew_raises", approval=True)

        assert set(resp.json()) == {
            "proposal_id",
            "recommendation",
            "status",
            "counter_terms",
            "approval_id",
            "pricing_verified",
            "pricing_verification_reason",
            "errors",
            "evaluated_by",
            "fallback_reason",
        }


class TestApprovalResume:
    @pytest.mark.parametrize("snapshot_has_new_fields", [True, False])
    async def test_resume_rehydrates_old_and_new_snapshots(
        self, run_app, storage, event_bus, snapshot_has_new_fields
    ):
        from ad_seller.events.approval import ApprovalGate
        from ad_seller.services import approval_service

        resp = await run_app("crew_raises", approval=True)
        approval_id = resp.json()["approval_id"]
        gate = ApprovalGate(storage)
        request = await gate.get_request(approval_id)
        if not snapshot_has_new_fields:
            snapshot = dict(request.flow_state_snapshot)
            snapshot.pop("evaluated_by", None)
            snapshot.pop("fallback_reason", None)
            raw = await storage.get(f"approval:{approval_id}")
            raw = json.loads(raw) if isinstance(raw, str) else raw
            raw["flow_state_snapshot"] = snapshot
            await storage.set(f"approval:{approval_id}", raw)

        with (
            patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
            patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        ):
            await approval_service.decide_approval(
                approval_id=approval_id, decision="approve", decided_by="ops"
            )
            result = await approval_service.resume_flow(approval_id)

        assert result["status"] == "accepted"
