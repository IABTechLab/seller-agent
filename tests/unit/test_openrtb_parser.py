# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the seller's OpenRTB BidRequest -> AudienceRef parser.

Mirror image of the buyer's `test_openrtb_builder.py`. Together they form a
round-trip parity check for the carrier mapping defined in
``docs/api/audience_plan_wire_format.md`` §9 and proposal §5.1 Step 4.

Bead: ar-8vzg.
"""

from __future__ import annotations

from ad_seller.services.openrtb_parser import (
    AGENTIC_USER_EXT_KEY,
    parse_openrtb_audience,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bidrequest_with(
    *,
    user: dict | None = None,
    site: dict | None = None,
) -> dict:
    """Minimal BidRequest fragment for parser tests."""
    out: dict = {}
    if user is not None:
        out["user"] = user
    if site is not None:
        out["site"] = site
    return out


# ---------------------------------------------------------------------------
# 1. Standard segments -> standard AudienceRefs
# ---------------------------------------------------------------------------


def test_parses_standard_segments_from_user_data() -> None:
    bidrequest = _bidrequest_with(
        user={
            "data": [
                {
                    "name": "IAB_Taxonomy",
                    "ext": {"taxonomy_version": "1.1"},
                    "segment": [{"id": "3-7"}, {"id": "4-2"}],
                }
            ]
        }
    )
    result = parse_openrtb_audience(bidrequest)
    refs = result["refs"]

    assert len(refs) == 2
    assert all(r.type == "standard" for r in refs)
    assert all(r.taxonomy == "iab-audience" for r in refs)
    assert all(r.version == "1.1" for r in refs)
    assert all(r.source == "explicit" for r in refs)
    assert {r.identifier for r in refs} == {"3-7", "4-2"}
    assert result["warnings"] == []


def test_ignores_non_iab_taxonomy_user_data_entries() -> None:
    bidrequest = _bidrequest_with(
        user={
            "data": [
                {
                    "name": "ThirdPartyDataProvider",
                    "segment": [{"id": "tpdp-99"}],
                },
                {
                    "name": "IAB_Taxonomy",
                    "ext": {"taxonomy_version": "1.1"},
                    "segment": [{"id": "3-7"}],
                },
            ]
        }
    )
    result = parse_openrtb_audience(bidrequest)
    # Only the IAB_Taxonomy entry contributes.
    assert [r.identifier for r in result["refs"]] == ["3-7"]


def test_standard_default_version_when_ext_absent() -> None:
    bidrequest = _bidrequest_with(
        user={
            "data": [
                {
                    "name": "IAB_Taxonomy",
                    "segment": [{"id": "3-7"}],
                }
            ]
        }
    )
    result = parse_openrtb_audience(bidrequest)
    refs = result["refs"]
    assert len(refs) == 1
    # Default to 1.1 when the buyer omits ext.taxonomy_version (defensive).
    assert refs[0].version == "1.1"


# ---------------------------------------------------------------------------
# 2. Contextual: site.cat + cattax=7 -> contextual refs
# ---------------------------------------------------------------------------


def test_parses_contextual_when_cattax_is_7() -> None:
    bidrequest = _bidrequest_with(site={"cat": ["IAB1-2", "IAB1-7"], "cattax": 7})
    result = parse_openrtb_audience(bidrequest)
    refs = result["refs"]
    assert len(refs) == 2
    assert all(r.type == "contextual" for r in refs)
    assert all(r.taxonomy == "iab-content" for r in refs)
    assert all(r.version == "3.1" for r in refs)
    assert {r.identifier for r in refs} == {"IAB1-2", "IAB1-7"}
    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# 3. Unknown cattax -> ignore + warning
# ---------------------------------------------------------------------------


def test_unknown_cattax_logs_warning_and_drops_cats() -> None:
    bidrequest = _bidrequest_with(site={"cat": ["IAB1-2"], "cattax": 6})
    result = parse_openrtb_audience(bidrequest)
    assert result["refs"] == []
    assert any("cattax" in w for w in result["warnings"]), result["warnings"]


def test_missing_cattax_logs_warning_and_drops_cats() -> None:
    bidrequest = _bidrequest_with(site={"cat": ["IAB1-2"]})  # no cattax
    result = parse_openrtb_audience(bidrequest)
    assert result["refs"] == []
    assert any("cattax" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 4. Agentic refs from user.ext.iab_agentic_audiences.refs[]
# ---------------------------------------------------------------------------


def test_parses_agentic_refs_from_namespaced_user_ext() -> None:
    bidrequest = _bidrequest_with(
        user={
            "ext": {
                AGENTIC_USER_EXT_KEY: {
                    "refs": [
                        {
                            "identifier": "emb://buyer.example.com/q1",
                            "version": "draft-2026-01",
                            "source": "explicit",
                            "compliance_context": {
                                "jurisdiction": "US",
                                "consent_framework": "IAB-TCFv2",
                                "consent_string_ref": "tcf:CPxxxx",
                                "attestation": None,
                            },
                        }
                    ]
                }
            }
        }
    )
    result = parse_openrtb_audience(bidrequest)
    refs = result["refs"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref.type == "agentic"
    assert ref.identifier == "emb://buyer.example.com/q1"
    assert ref.version == "draft-2026-01"
    assert ref.taxonomy == "agentic-audiences"
    assert ref.compliance_context is not None
    assert ref.compliance_context.jurisdiction == "US"
    assert ref.compliance_context.consent_framework == "IAB-TCFv2"
    assert result["warnings"] == []


def test_agentic_ref_without_compliance_uses_fallback_with_warning() -> None:
    """Spec mandates compliance_context for agentic refs, but a malformed
    request MAY omit it. The parser substitutes a clearly-marked fallback
    (``jurisdiction='UNKNOWN'``) so downstream code does not crash, and
    surfaces a warning so the audit trail can flag the entry."""
    bidrequest = _bidrequest_with(
        user={
            "ext": {
                AGENTIC_USER_EXT_KEY: {
                    "refs": [
                        {
                            "identifier": "emb://buyer.example.com/q1",
                            "version": "draft-2026-01",
                            "source": "explicit",
                            # NOTE: compliance_context omitted.
                        }
                    ]
                }
            }
        }
    )
    result = parse_openrtb_audience(bidrequest)
    refs = result["refs"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref.type == "agentic"
    assert ref.compliance_context is not None
    assert ref.compliance_context.jurisdiction == "UNKNOWN"
    assert ref.compliance_context.consent_framework == "none"
    assert any("compliance_context" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 5. Round-trip: builder -> parser parity
# ---------------------------------------------------------------------------


def test_round_trip_builder_then_parser_recovers_refs() -> None:
    """Build a BidRequest from a multi-role plan and parse it back.

    Confirms the carrier mapping is symmetric for the per-ref content
    (identifier / version / type / taxonomy). Roles are expected NOT to
    survive the trip -- OpenRTB's lossy mapping does not preserve them
    (see parser docstring).
    """
    # Lazy import the buyer-side builder (test executes in seller venv;
    # adjust import path so the buyer source is on PYTHONPATH).
    import sys
    from pathlib import Path

    # tests/unit/test_openrtb_parser.py -> ascend 6 to agent_range parent.
    AGENT_RANGE = Path(__file__).resolve().parents[5]
    BUYER_SRC = (
        AGENT_RANGE
        / "ad_buyer_system" / ".worktrees" / "audience-extension" / "src"
    )
    sys.path.insert(0, str(BUYER_SRC))
    try:
        from ad_buyer.clients.openrtb_builder import (  # type: ignore[import-not-found]
            build_openrtb_audience_targeting,
        )
        from ad_buyer.models.audience_plan import (  # type: ignore[import-not-found]
            AudiencePlan,
            AudienceRef as BuyerAudienceRef,
            ComplianceContext as BuyerComplianceContext,
        )
    finally:
        sys.path.remove(str(BUYER_SRC))

    plan = AudiencePlan(
        primary=BuyerAudienceRef(
            type="standard", identifier="3-7", taxonomy="iab-audience",
            version="1.1", source="explicit",
        ),
        constraints=[
            BuyerAudienceRef(
                type="contextual", identifier="IAB1-2",
                taxonomy="iab-content", version="3.1", source="explicit",
            )
        ],
        extensions=[
            BuyerAudienceRef(
                type="agentic",
                identifier="emb://buyer.example.com/q1-converters",
                taxonomy="agentic-audiences", version="draft-2026-01",
                source="explicit",
                compliance_context=BuyerComplianceContext(
                    jurisdiction="US",
                    consent_framework="IAB-TCFv2",
                    consent_string_ref="tcf:CPxxxx",
                ),
            )
        ],
    )

    fragment = build_openrtb_audience_targeting(plan, enable_agentic_ext=True)
    parsed = parse_openrtb_audience(fragment)

    # 3 refs out (standard + contextual + agentic).
    assert len(parsed["refs"]) == 3, parsed["refs"]
    by_type = {r.type: r for r in parsed["refs"]}

    assert by_type["standard"].identifier == "3-7"
    assert by_type["standard"].taxonomy == "iab-audience"
    assert by_type["standard"].version == "1.1"

    assert by_type["contextual"].identifier == "IAB1-2"
    assert by_type["contextual"].taxonomy == "iab-content"
    assert by_type["contextual"].version == "3.1"

    assert by_type["agentic"].identifier == "emb://buyer.example.com/q1-converters"
    assert by_type["agentic"].taxonomy == "agentic-audiences"
    assert by_type["agentic"].version == "draft-2026-01"
    assert by_type["agentic"].compliance_context is not None
    assert by_type["agentic"].compliance_context.jurisdiction == "US"
    assert parsed["warnings"] == []


# ---------------------------------------------------------------------------
# Edge cases / defensive parsing
# ---------------------------------------------------------------------------


def test_empty_bidrequest_returns_empty_refs() -> None:
    result = parse_openrtb_audience({})
    assert result == {"refs": [], "warnings": []}


def test_non_dict_bidrequest_handled_safely() -> None:
    result = parse_openrtb_audience("not a dict")  # type: ignore[arg-type]
    assert result["refs"] == []
    assert any("not an object" in w for w in result["warnings"])


def test_malformed_user_data_entries_skipped_with_warnings() -> None:
    bidrequest = _bidrequest_with(
        user={
            "data": [
                "not an object",  # entry-level malformation
                {
                    "name": "IAB_Taxonomy",
                    "segment": "not an array",
                },
                {
                    "name": "IAB_Taxonomy",
                    "segment": [
                        {"id": "3-7"},
                        {"id": ""},  # empty id
                        "not an object",  # segment-level malformation
                    ],
                },
            ]
        }
    )
    result = parse_openrtb_audience(bidrequest)
    # Only the well-formed segment with id="3-7" survives.
    assert [r.identifier for r in result["refs"]] == ["3-7"]
    # Multiple warnings recorded.
    assert len(result["warnings"]) >= 3


def test_malformed_agentic_refs_skipped_with_warnings() -> None:
    bidrequest = _bidrequest_with(
        user={
            "ext": {
                AGENTIC_USER_EXT_KEY: {
                    "refs": [
                        "not an object",
                        {"identifier": "", "version": "draft-2026-01"},
                        {"identifier": "emb://x", "version": ""},
                        {
                            "identifier": "emb://valid",
                            "version": "draft-2026-01",
                            "source": "explicit",
                            "compliance_context": {
                                "jurisdiction": "US",
                                "consent_framework": "IAB-TCFv2",
                            },
                        },
                    ]
                }
            }
        }
    )
    result = parse_openrtb_audience(bidrequest)
    # Only the valid one survives.
    valid = [r for r in result["refs"] if r.type == "agentic"]
    assert len(valid) == 1
    assert valid[0].identifier == "emb://valid"
    assert len(result["warnings"]) >= 3
