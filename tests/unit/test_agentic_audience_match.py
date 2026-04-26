# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for `POST /agentic-audience/match` (proposal §5.7 + §6 row 11).

Covers bead ar-sn8f deliverable A:

- Happy path (seller advertises agentic): deterministic mock score returned
  with quality bucket and `agentic_supported_by_seller=True`.
- Legacy seller (top-level agentic.supported=False): returns POOR / 0.0 /
  `agentic_supported_by_seller=False`.
- Non-agentic ref (`type='standard'`): rejected with HTTP 400.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import patch

import pytest

# Stub broken flow modules before importing main, mirroring sibling tests.
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

from ad_seller.interfaces.api.main import (  # noqa: E402
    _agentic_match_quality,
    _deterministic_score,
    _get_optional_api_key_record,
    app,
)
from ad_seller.models.audience_capabilities import (  # noqa: E402
    AgenticCapabilityFlag,
    CapabilityAudienceBlock,
    MaxRefsPerRole,
    TaxonomyLockHashes,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    """httpx AsyncClient with FastAPI dependency overrides."""

    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _agentic_caps_block(*, supported: bool) -> CapabilityAudienceBlock:
    """Build a CapabilityAudienceBlock with deterministic hashes.

    We bypass `build_capability_audience_block()` (which reads the lock file)
    by stubbing the function the endpoint imports lazily.
    """

    return CapabilityAudienceBlock(
        schema_version="1",
        standard_taxonomy_versions=["1.1"],
        contextual_taxonomy_versions=["3.1"],
        agentic=AgenticCapabilityFlag(supported=supported),
        supports_constraints=True,
        supports_extensions=False,
        supports_exclusions=False,
        max_refs_per_role=MaxRefsPerRole(),
        taxonomy_lock_hashes=TaxonomyLockHashes(
            audience="sha256:" + "a" * 64,
            content="sha256:" + "b" * 64,
        ),
    )


@pytest.fixture
def agentic_supported_seller():
    """Patch the endpoint's lazy import of build_capability_audience_block."""

    with patch(
        "ad_seller.models.audience_capabilities.build_capability_audience_block",
        return_value=_agentic_caps_block(supported=True),
    ):
        yield


@pytest.fixture
def legacy_seller():
    with patch(
        "ad_seller.models.audience_capabilities.build_capability_audience_block",
        return_value=_agentic_caps_block(supported=False),
    ):
        yield


# =============================================================================
# Pure helpers
# =============================================================================


class TestQualityBuckets:
    """Score -> quality label mapping."""

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.95, "STRONG"),
            (0.85, "STRONG"),
            (0.84, "MODERATE"),
            (0.65, "MODERATE"),
            (0.5, "WEAK"),
            (0.4, "WEAK"),
            (0.39, "POOR"),
            (0.0, "POOR"),
        ],
    )
    def test_buckets(self, score, expected):
        assert _agentic_match_quality(score) == expected


class TestDeterministicScore:
    """Sha256-derived score is deterministic and in [0, 1]."""

    def test_in_range(self):
        score = _deterministic_score("emb://test/x")
        assert 0.0 <= score <= 1.0

    def test_deterministic(self):
        a = _deterministic_score("emb://test/y")
        b = _deterministic_score("emb://test/y")
        assert a == b

    def test_distinct_inputs_distinct_scores(self):
        # Cheap sanity: two different identifiers produce different scores.
        a = _deterministic_score("emb://buyer.x/foo")
        b = _deterministic_score("emb://buyer.x/bar")
        assert a != b


# =============================================================================
# POST /agentic-audience/match endpoint
# =============================================================================


class TestAgenticMatchEndpoint:
    """End-to-end behavior of the new endpoint."""

    @pytest.mark.asyncio
    async def test_happy_path_agentic_supported(
        self, client, agentic_supported_seller
    ):
        body = {
            "audience_ref": {
                "type": "agentic",
                "identifier": "emb://buyer.example.com/audiences/auto-q1",
                "taxonomy": "agentic-audiences",
                "version": "draft-2026-01",
                "source": "explicit",
                "compliance_context": {
                    "jurisdiction": "US",
                    "consent_framework": "IAB-TCFv2",
                },
            }
        }
        async with client as c:
            resp = await c.post("/agentic-audience/match", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["agentic_supported_by_seller"] is True
        assert 0.0 <= data["match_confidence"] <= 1.0
        assert data["match_quality"] in {"STRONG", "MODERATE", "WEAK", "POOR"}
        assert data["audience_ref"] == body["audience_ref"]
        assert "rationale" in data and isinstance(data["rationale"], str)

    @pytest.mark.asyncio
    async def test_deterministic_response_per_identifier(
        self, client, agentic_supported_seller
    ):
        body = {
            "audience_ref": {
                "type": "agentic",
                "identifier": "emb://stable/test",
                "taxonomy": "agentic-audiences",
                "version": "draft-2026-01",
                "source": "explicit",
                "compliance_context": {
                    "jurisdiction": "US",
                    "consent_framework": "IAB-TCFv2",
                },
            }
        }
        async with client as c:
            r1 = await c.post("/agentic-audience/match", json=body)
            r2 = await c.post("/agentic-audience/match", json=body)
        assert r1.json()["match_confidence"] == r2.json()["match_confidence"]
        assert r1.json()["match_quality"] == r2.json()["match_quality"]

    @pytest.mark.asyncio
    async def test_legacy_seller_returns_poor(self, client, legacy_seller):
        body = {
            "audience_ref": {
                "type": "agentic",
                "identifier": "emb://anything",
                "taxonomy": "agentic-audiences",
                "version": "draft-2026-01",
                "source": "explicit",
                "compliance_context": {
                    "jurisdiction": "US",
                    "consent_framework": "IAB-TCFv2",
                },
            }
        }
        async with client as c:
            resp = await c.post("/agentic-audience/match", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["agentic_supported_by_seller"] is False
        assert data["match_confidence"] == 0.0
        assert data["match_quality"] == "POOR"
        assert data["matched_capabilities"] == []

    @pytest.mark.asyncio
    async def test_non_agentic_ref_rejected_with_400(
        self, client, agentic_supported_seller
    ):
        body = {
            "audience_ref": {
                "type": "standard",
                "identifier": "3-7",
                "taxonomy": "iab-audience",
                "version": "1.1",
                "source": "explicit",
            }
        }
        async with client as c:
            resp = await c.post("/agentic-audience/match", json=body)

        assert resp.status_code == 400
        # FastAPI wraps the dict body in `detail`.
        body_json = resp.json()
        assert body_json["detail"]["error"] == "invalid_audience_ref"

    @pytest.mark.asyncio
    async def test_missing_identifier_returns_400(
        self, client, agentic_supported_seller
    ):
        body = {
            "audience_ref": {
                "type": "agentic",
                "identifier": "",
                "taxonomy": "agentic-audiences",
                "version": "draft-2026-01",
                "source": "explicit",
                "compliance_context": {
                    "jurisdiction": "US",
                    "consent_framework": "IAB-TCFv2",
                },
            }
        }
        async with client as c:
            resp = await c.post("/agentic-audience/match", json=body)

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_audience_ref"
