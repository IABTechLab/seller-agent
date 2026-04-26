# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for audience-plan validation pieces (bead ar-sn8f).

Covers proposal §5.7 layers 2-3 + §5.1 Step 2:

- B: structured `audience_plan_unsupported` error from
  `validate_audience_plan()` and its surface on `POST /api/v1/deals`.
- C: `validate_audience` hard-rejects on zero standard / contextual overlap
  and remains a soft-warn on low agentic match scores.
- D: `honor_audience_plan_snapshot()` returns the frozen snapshot and
  emits a structured warning when current capabilities have shrunk.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from types import ModuleType

import pytest

# Stub broken flow modules before importing other ad_seller bits.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = (
            _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        )
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
from ad_seller.models.audience_capabilities import (  # noqa: E402
    AgenticCapabilities,
    AgenticCapabilityFlag,
    AudienceCapabilities,
    CapabilityAudienceBlock,
    MaxRefsPerRole,
    TaxonomyLockHashes,
)
from ad_seller.models.flow_state import ExecutionStatus  # noqa: E402
from ad_seller.models.media_kit import (  # noqa: E402
    Package,
    PackageLayer,
    PackageStatus,
)
from ad_seller.services.audience_plan_validator import (  # noqa: E402
    validate_audience_plan,
)
from ad_seller.services.fulfillment import (  # noqa: E402
    honor_audience_plan_snapshot,
)


# =============================================================================
# Helpers
# =============================================================================


def _caps_block(
    *,
    agentic_supported: bool = False,
    supports_extensions: bool = False,
    supports_constraints: bool = True,
    supports_exclusions: bool = False,
    standard_versions: list[str] | None = None,
    contextual_versions: list[str] | None = None,
    max_refs: MaxRefsPerRole | None = None,
) -> CapabilityAudienceBlock:
    """Build a CapabilityAudienceBlock without touching the lock file."""

    return CapabilityAudienceBlock(
        schema_version="1",
        standard_taxonomy_versions=standard_versions or ["1.1"],
        contextual_taxonomy_versions=contextual_versions or ["3.1"],
        agentic=AgenticCapabilityFlag(supported=agentic_supported),
        supports_constraints=supports_constraints,
        supports_extensions=supports_extensions,
        supports_exclusions=supports_exclusions,
        max_refs_per_role=max_refs or MaxRefsPerRole(),
        taxonomy_lock_hashes=TaxonomyLockHashes(
            audience="sha256:" + "a" * 64,
            content="sha256:" + "b" * 64,
        ),
    )


def _ref(
    type_: str,
    ident: str,
    *,
    version: str | None = None,
    source: str = "explicit",
    confidence: float | None = None,
    compliance: dict | None = None,
) -> dict:
    """Build a wire-shape AudienceRef dict."""

    taxonomy = {
        "standard": "iab-audience",
        "contextual": "iab-content",
        "agentic": "agentic-audiences",
    }[type_]
    default_version = {
        "standard": "1.1",
        "contextual": "3.1",
        "agentic": "draft-2026-01",
    }[type_]
    ref: dict = {
        "type": type_,
        "identifier": ident,
        "taxonomy": taxonomy,
        "version": version or default_version,
        "source": source,
        "confidence": confidence,
    }
    if type_ == "agentic":
        ref["compliance_context"] = compliance or {
            "jurisdiction": "US",
            "consent_framework": "IAB-TCFv2",
        }
    else:
        ref["compliance_context"] = None
    return ref


def _make_package(
    *,
    package_id: str,
    standard_segment_ids: list[str] | None = None,
    contextual_segment_ids: list[str] | None = None,
) -> Package:
    return Package(
        package_id=package_id,
        name=f"Pkg {package_id}",
        description="test",
        layer=PackageLayer.CURATED,
        status=PackageStatus.ACTIVE,
        base_price=20.0,
        floor_price=10.0,
        audience_capabilities=AudienceCapabilities(
            standard_segment_ids=standard_segment_ids or [],
            contextual_segment_ids=contextual_segment_ids or [],
        ),
    )


# =============================================================================
# B: validate_audience_plan() against capability block
# =============================================================================


class TestValidateAudiencePlan:
    """`audience_plan_unsupported` structured error production."""

    def test_supported_plan_returns_empty(self):
        plan = {
            "primary": _ref("standard", "3-7"),
            "constraints": [_ref("contextual", "IAB1-2")],
        }
        caps = _caps_block(supports_constraints=True)
        assert validate_audience_plan(plan, caps) == []

    def test_extensions_role_unsupported(self):
        plan = {
            "primary": _ref("standard", "3-7"),
            "extensions": [_ref("standard", "3-9")],
        }
        caps = _caps_block(supports_extensions=False)
        result = validate_audience_plan(plan, caps)
        assert any(
            r["path"] == "extensions[0]"
            and "extensions not supported" in r["reason"]
            for r in result
        )

    def test_primary_taxonomy_version_unsupported(self):
        plan = {"primary": _ref("standard", "3-7", version="3.2")}
        caps = _caps_block(standard_versions=["1.1"])
        result = validate_audience_plan(plan, caps)
        assert len(result) >= 1
        assert result[0]["path"] == "primary.taxonomy"
        assert "version" in result[0]["reason"]

    def test_unsupported_constraint_taxonomy_version(self):
        plan = {
            "primary": _ref("standard", "3-7"),
            "constraints": [_ref("contextual", "IAB1-2", version="2.0")],
        }
        caps = _caps_block(
            supports_constraints=True, contextual_versions=["3.1"]
        )
        result = validate_audience_plan(plan, caps)
        assert any(
            r["path"] == "constraints[0].taxonomy" for r in result
        )

    def test_agentic_in_primary_when_unsupported(self):
        plan = {"primary": _ref("agentic", "emb://x")}
        caps = _caps_block(agentic_supported=False)
        result = validate_audience_plan(plan, caps)
        assert any(
            r["path"] == "primary.taxonomy"
            and "agentic" in r["reason"]
            for r in result
        )

    def test_empty_plan_is_supported(self):
        assert validate_audience_plan(None, _caps_block()) == []
        assert validate_audience_plan({}, _caps_block()) == []

    def test_exclusions_role_unsupported(self):
        plan = {
            "primary": _ref("standard", "3-7"),
            "exclusions": [_ref("standard", "3-12")],
        }
        caps = _caps_block(supports_exclusions=False)
        result = validate_audience_plan(plan, caps)
        assert any(r["path"] == "exclusions[0]" for r in result)

    def test_cardinality_cap_exceeded(self):
        # Default max_refs_per_role.constraints = 3
        plan = {
            "primary": _ref("standard", "3-7"),
            "constraints": [
                _ref("contextual", f"IAB1-{i}") for i in range(5)
            ],
        }
        caps = _caps_block(supports_constraints=True)
        result = validate_audience_plan(plan, caps)
        assert any(
            "exceeds max_refs_per_role" in r["reason"] for r in result
        )


# =============================================================================
# C: ProposalHandlingFlow.validate_audience hard rejects
# =============================================================================


def _run_validate(flow: ProposalHandlingFlow):
    """Run the validate_audience coroutine directly."""

    asyncio.get_event_loop().run_until_complete(flow.validate_audience())


def _build_flow(packages: dict | None = None) -> ProposalHandlingFlow:
    """Build a minimally-initialized ProposalHandlingFlow for direct method calls.

    CrewAI's Flow.__init__ wires required-field state via Pydantic validation
    and SellerFlowState has required fields. Rather than fight that, we
    bypass `Flow.__init__` and seed only the attributes `validate_audience`
    actually touches.
    """

    import threading

    flow = ProposalHandlingFlow.__new__(ProposalHandlingFlow)
    flow._settings = None
    flow._audience_validation = {}
    flow._packages_for_audience_validation = packages or {}
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


@pytest.fixture
def flow_with_pkgs():
    """Build a flow with a known package list and standard product."""

    return _build_flow(
        packages={
            "pkg-a": _make_package(
                package_id="pkg-a",
                standard_segment_ids=["3-7", "3-12"],
                contextual_segment_ids=["IAB1-2"],
            ),
        }
    )


class TestValidateAudienceHardRejects:
    """Proposal §5.7 layer 3: standard / contextual zero-overlap = hard reject."""

    def test_standard_zero_overlap_hard_rejects(self, flow_with_pkgs):
        flow = flow_with_pkgs
        flow.state.proposal_data = {
            "product_id": "prod-x",
            "audience_plan": {
                "primary": _ref("standard", "9-99"),
            },
        }
        _run_validate(flow)
        assert flow.state.status == ExecutionStatus.FAILED
        assert any(
            "zero overlap" in e and "standard" in e for e in flow.state.errors
        )

    def test_contextual_zero_overlap_hard_rejects(self, flow_with_pkgs):
        flow = flow_with_pkgs
        flow.state.proposal_data = {
            "product_id": "prod-x",
            "audience_plan": {
                "primary": _ref("standard", "3-7"),  # standard ok
                "constraints": [_ref("contextual", "IAB99-99")],
            },
        }
        _run_validate(flow)
        assert flow.state.status == ExecutionStatus.FAILED
        assert any(
            "zero overlap" in e and "contextual" in e for e in flow.state.errors
        )

    def test_standard_overlap_does_not_hard_reject(self, flow_with_pkgs):
        flow = flow_with_pkgs
        flow.state.proposal_data = {
            "product_id": "prod-x",
            "audience_plan": {"primary": _ref("standard", "3-7")},
        }
        _run_validate(flow)
        assert flow.state.status != ExecutionStatus.FAILED

    def test_low_agentic_match_does_not_hard_reject(self, flow_with_pkgs):
        # Per bead ar-sn8f: agentic refs are SOFT WARN, not hard reject,
        # because the score is opinion (mock-quality in Epic 1).
        flow = flow_with_pkgs
        flow.state.proposal_data = {
            "product_id": "prod-x",
            "audience_plan": {
                "primary": _ref("standard", "3-7"),  # standard ok -> no hard reject
                "extensions": [_ref("agentic", "emb://low-score-anything")],
            },
        }
        _run_validate(flow)
        # Low agentic doesn't fail the flow on the hard-reject path.
        assert flow.state.status != ExecutionStatus.FAILED

    def test_no_packages_falls_back_to_soft_warn(self):
        # When the seller has no packages registered, hard reject defers
        # to the existing soft-warn UCP path.
        flow = _build_flow(packages=None)
        flow.state.proposal_id = "p2"
        flow.state.proposal_data = {
            "product_id": "prod-x",
            "audience_plan": {"primary": _ref("standard", "9-99")},
        }
        _run_validate(flow)
        # Should NOT fail -- fallback to existing path.
        assert flow.state.status != ExecutionStatus.FAILED


# =============================================================================
# D: honor_audience_plan_snapshot helper
# =============================================================================


class TestHonorAudienceSnapshot:
    """Snapshot is honored even when current capabilities have shrunk."""

    def test_returns_frozen_snapshot(self):
        snapshot = {
            "schema_version": "1",
            "audience_plan_id": "sha256:" + "f" * 64,
            "primary": _ref("standard", "3-7"),
            "extensions": [_ref("agentic", "emb://x")],
        }
        deal_record = {"audience_plan_snapshot": snapshot}
        # Current caps are weaker (no extensions, no agentic).
        current = _caps_block(
            agentic_supported=False, supports_extensions=False
        )
        result = honor_audience_plan_snapshot(
            "DEAL-X", deal_record, current
        )
        # Snapshot returned exactly -- not rewritten.
        assert result == snapshot

    def test_logs_warning_on_capability_degradation(self, caplog):
        snapshot = {
            "audience_plan_id": "sha256:" + "1" * 64,
            "primary": _ref("standard", "3-7"),
            "extensions": [_ref("agentic", "emb://x")],
        }
        deal_record = {"audience_plan_snapshot": snapshot}
        current = _caps_block(
            agentic_supported=False, supports_extensions=False
        )

        with caplog.at_level(logging.WARNING, logger="ad_seller.services.fulfillment"):
            honor_audience_plan_snapshot("DEAL-Y", deal_record, current)

        assert any(
            "capabilities degraded" in record.message
            and "DEAL-Y" in record.message
            for record in caplog.records
        )

    def test_no_warning_when_caps_still_match(self, caplog):
        snapshot = {
            "audience_plan_id": "sha256:" + "2" * 64,
            "primary": _ref("standard", "3-7"),
        }
        deal_record = {"audience_plan_snapshot": snapshot}
        current = _caps_block()

        with caplog.at_level(logging.WARNING, logger="ad_seller.services.fulfillment"):
            honor_audience_plan_snapshot("DEAL-Z", deal_record, current)

        assert not any(
            "capabilities degraded" in record.message for record in caplog.records
        )

    def test_returns_none_when_no_snapshot(self):
        result = honor_audience_plan_snapshot(
            "DEAL-NONE", {"other": "data"}, _caps_block()
        )
        assert result is None

    def test_returns_none_when_no_deal_record(self):
        assert (
            honor_audience_plan_snapshot("DEAL-MISSING", None, _caps_block())
            is None
        )


# =============================================================================
# Endpoint surface: POST /api/v1/deals with audience_plan
# =============================================================================


@pytest.fixture
def http_client():
    """httpx AsyncClient with FastAPI dependency overrides."""

    from datetime import datetime, timedelta

    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    # Build a minimal in-memory storage for the deal-booking happy path.
    store: dict = {}
    storage = type(
        "_FakeStorage",
        (),
        {
            "get_quote": staticmethod(
                lambda qid: _async_return(store.get(f"quote:{qid}"))
            ),
            "set_quote": staticmethod(
                lambda qid, data, ttl=None: _async_set(store, f"quote:{qid}", data)
            ),
            "get_deal": staticmethod(
                lambda did: _async_return(store.get(f"deal:{did}"))
            ),
            "set_deal": staticmethod(
                lambda did, data: _async_set(store, f"deal:{did}", data)
            ),
            "_store": store,
        },
    )()
    quote_id = "qt-test-audplan"
    store[f"quote:{quote_id}"] = {
        "quote_id": quote_id,
        "status": "available",
        "deal_type": "PD",
        "product": {
            "product_id": "ctv-premium",
            "name": "Premium CTV",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 30.0,
            "tier_discount_pct": 0.0,
            "volume_discount_pct": 0.0,
            "final_cpm": 30.0,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "test",
        },
        "terms": {
            "impressions": 1000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "public",
        "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    yield c, storage, quote_id
    app.dependency_overrides.clear()


def _async_return(value):
    """Helper to wrap a value in an awaitable for AsyncMock-like behavior."""

    async def _coro():
        return value

    return _coro()


def _async_set(store, key, value):
    async def _coro():
        store[key] = value

    return _coro()


class TestBookDealAudiencePlanRejection:
    """Endpoint-level: POST /api/v1/deals with unsupported audience_plan."""

    @pytest.mark.asyncio
    async def test_extensions_unsupported_returns_structured_400(
        self, http_client
    ):
        from unittest.mock import patch

        client, storage, quote_id = http_client
        with patch(
            "ad_seller.storage.factory.get_storage", return_value=storage
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/deals",
                    json={
                        "quote_id": quote_id,
                        "audience_plan": {
                            "primary": _ref("standard", "3-7"),
                            "extensions": [_ref("standard", "3-9")],
                        },
                    },
                )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "audience_plan_unsupported"
        assert any(
            r["path"] == "extensions[0]" for r in body["detail"]["unsupported"]
        )

    @pytest.mark.asyncio
    async def test_supported_plan_books_successfully(self, http_client):
        from unittest.mock import patch

        client, storage, quote_id = http_client
        with patch(
            "ad_seller.storage.factory.get_storage", return_value=storage
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/deals",
                    json={
                        "quote_id": quote_id,
                        "audience_plan": {
                            "primary": _ref("standard", "3-7"),
                            "constraints": [_ref("contextual", "IAB1-2")],
                        },
                    },
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deal_id"].startswith("DEMO-")
