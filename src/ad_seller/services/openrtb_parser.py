# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenRTB parser: BidRequest fragments -> seller-side `AudienceRef` list.

Per proposal §5.1 Step 4 / §6 row 15 / wire-format spec §9, the buyer flattens
audience semantics into three OpenRTB carriers at impression time:

| Audience type | OpenRTB carrier | Seller parse rule |
|---------------|-----------------|-------------------|
| `standard`    | ``user.data[].segment[].id``       | Honor only when ``data.name == "IAB_Taxonomy"`` |
| `contextual`  | ``site.cat`` + ``site.cattax = 7`` | Honor only when ``cattax == 7`` (Content Taxonomy 3.1) |
| `agentic`     | ``user.ext.iab_agentic_audiences.refs[]`` | Namespaced extension |

The parser is intentionally **defensive**: unknown ``cattax`` values, missing
or malformed extension keys, and partial fragments all log a warning and
parse what they can without raising. Sellers downstream of this parser may
choose to enforce stricter validation by inspecting the warnings list.

Future: this parser is invokable from tests today. When a real OpenRTB
ingestion endpoint lands on the seller, it imports `parse_openrtb_audience`
and feeds the resulting `AudienceRef` list into the existing audience-plan
matcher.

Bead: ar-8vzg (proposal §6 row 15).
"""

from __future__ import annotations

import logging
from typing import Any

from ad_seller.models.audience_ref import AudienceRef, ComplianceContext

logger = logging.getLogger(__name__)

# Seller-side mirror of buyer's constants (kept independent on purpose --
# the wire-format spec is the source of truth, NOT a Python import).
CONTENT_TAXONOMY_31_CATTAX = 7
IAB_AUDIENCE_TAXONOMY_DATA_NAME = "IAB_Taxonomy"
AGENTIC_USER_EXT_KEY = "iab_agentic_audiences"

# When the wire payload omits ``compliance_context`` for an agentic ref --
# which the buyer's Pydantic model forbids but a malformed third-party
# emitter MAY do -- we synthesize a minimal placeholder so downstream code
# does not crash. The synthesized value is clearly marked so audit trails
# can flag it. Sellers SHOULD reject such requests if strictness applies.
_FALLBACK_AGENTIC_COMPLIANCE = ComplianceContext(
    jurisdiction="UNKNOWN",
    consent_framework="none",
    consent_string_ref=None,
    attestation=None,
)


def _parse_standard_data_entries(
    user_data: list[dict[str, Any]] | None,
    warnings: list[str],
) -> list[AudienceRef]:
    """Extract standard-type AudienceRefs from ``bidrequest.user.data[]``.

    Honors only entries whose ``name`` equals ``IAB_Taxonomy`` (per the
    builder's emission rule). Reads ``ext.taxonomy_version`` to set the
    ref's ``version`` field; falls back to ``"1.1"`` when absent.
    """

    refs: list[AudienceRef] = []
    if not user_data:
        return refs

    for i, data_entry in enumerate(user_data):
        if not isinstance(data_entry, dict):
            warnings.append(f"user.data[{i}] is not an object; skipped")
            continue
        name = data_entry.get("name")
        if name != IAB_AUDIENCE_TAXONOMY_DATA_NAME:
            # Other data providers may legitimately appear in user.data --
            # we only consume the IAB_Taxonomy ones.
            continue

        ext = data_entry.get("ext") or {}
        version = (
            ext.get("taxonomy_version") if isinstance(ext, dict) else None
        ) or "1.1"

        segments = data_entry.get("segment") or []
        if not isinstance(segments, list):
            warnings.append(
                f"user.data[{i}].segment is not an array; skipped"
            )
            continue

        for j, seg in enumerate(segments):
            if not isinstance(seg, dict):
                warnings.append(
                    f"user.data[{i}].segment[{j}] is not an object; skipped"
                )
                continue
            seg_id = seg.get("id")
            if not seg_id or not isinstance(seg_id, str):
                warnings.append(
                    f"user.data[{i}].segment[{j}] missing 'id'; skipped"
                )
                continue
            try:
                refs.append(
                    AudienceRef(
                        type="standard",
                        identifier=seg_id,
                        taxonomy="iab-audience",
                        version=version,
                        source="explicit",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - validation already strict
                warnings.append(
                    f"user.data[{i}].segment[{j}] failed validation: {exc}"
                )
    return refs


def _parse_contextual(
    site: dict[str, Any] | None,
    warnings: list[str],
) -> list[AudienceRef]:
    """Extract contextual-type AudienceRefs from ``bidrequest.site``.

    Honors only ``cattax == 7`` (IAB Content Taxonomy 3.1) per the wire-format
    spec. Other ``cattax`` values are ignored with a warning -- they may be
    valid Content Taxonomy 2.x or other taxonomies, but the seller-side
    parser does not implement those.
    """

    refs: list[AudienceRef] = []
    if not site or not isinstance(site, dict):
        return refs

    cattax = site.get("cattax")
    cats = site.get("cat") or []

    if not cats:
        return refs

    if cattax != CONTENT_TAXONOMY_31_CATTAX:
        warnings.append(
            f"site.cattax={cattax!r} not honored; only "
            f"{CONTENT_TAXONOMY_31_CATTAX} (Content Taxonomy 3.1) supported"
        )
        return refs

    if not isinstance(cats, list):
        warnings.append("site.cat is not an array; skipped")
        return refs

    for i, cat in enumerate(cats):
        if not isinstance(cat, str) or not cat:
            warnings.append(f"site.cat[{i}] is not a non-empty string; skipped")
            continue
        try:
            refs.append(
                AudienceRef(
                    type="contextual",
                    identifier=cat,
                    taxonomy="iab-content",
                    version="3.1",
                    source="explicit",
                )
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"site.cat[{i}] failed validation: {exc}")
    return refs


def _parse_agentic_ext(
    user_ext: dict[str, Any] | None,
    warnings: list[str],
) -> list[AudienceRef]:
    """Extract agentic-type AudienceRefs from the namespaced user.ext slot.

    Reads ``user.ext.iab_agentic_audiences.refs[]``. Each entry MUST carry
    ``identifier`` and ``version``; ``source`` defaults to ``"explicit"``
    when absent; ``compliance_context`` defaults to a clearly-marked
    placeholder (``jurisdiction='UNKNOWN'``, ``consent_framework='none'``)
    when absent so the AudienceRef validator does not crash on a malformed
    third-party request. Sellers that require strict consent SHOULD inspect
    the warnings list and reject placeholders.
    """

    refs: list[AudienceRef] = []
    if not user_ext or not isinstance(user_ext, dict):
        return refs

    agentic_block = user_ext.get(AGENTIC_USER_EXT_KEY)
    if agentic_block is None:
        return refs
    if not isinstance(agentic_block, dict):
        warnings.append(
            f"user.ext.{AGENTIC_USER_EXT_KEY} is not an object; skipped"
        )
        return refs

    entries = agentic_block.get("refs") or []
    if not isinstance(entries, list):
        warnings.append(
            f"user.ext.{AGENTIC_USER_EXT_KEY}.refs is not an array; skipped"
        )
        return refs

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            warnings.append(
                f"user.ext.{AGENTIC_USER_EXT_KEY}.refs[{i}] is not an object; skipped"
            )
            continue
        identifier = entry.get("identifier")
        version = entry.get("version")
        source = entry.get("source") or "explicit"
        cc_payload = entry.get("compliance_context")

        if not identifier or not isinstance(identifier, str):
            warnings.append(
                f"user.ext.{AGENTIC_USER_EXT_KEY}.refs[{i}] missing 'identifier'; skipped"
            )
            continue
        if not version or not isinstance(version, str):
            warnings.append(
                f"user.ext.{AGENTIC_USER_EXT_KEY}.refs[{i}] missing 'version'; skipped"
            )
            continue

        if cc_payload is None:
            warnings.append(
                f"user.ext.{AGENTIC_USER_EXT_KEY}.refs[{i}] missing "
                "'compliance_context'; using fallback placeholder"
            )
            compliance = _FALLBACK_AGENTIC_COMPLIANCE
        else:
            try:
                compliance = ComplianceContext.model_validate(cc_payload)
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"user.ext.{AGENTIC_USER_EXT_KEY}.refs[{i}] "
                    f"compliance_context invalid: {exc}; using fallback"
                )
                compliance = _FALLBACK_AGENTIC_COMPLIANCE

        try:
            refs.append(
                AudienceRef(
                    type="agentic",
                    identifier=identifier,
                    taxonomy="agentic-audiences",
                    version=version,
                    source=source,
                    compliance_context=compliance,
                )
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"user.ext.{AGENTIC_USER_EXT_KEY}.refs[{i}] failed validation: {exc}"
            )
    return refs


def parse_openrtb_audience(bidrequest: dict[str, Any]) -> dict[str, Any]:
    """Parse a partial OpenRTB BidRequest into a flat list of `AudienceRef`s.

    Args:
        bidrequest: The parsed JSON body of an OpenRTB v2.6 BidRequest. Only
            the ``user`` and ``site`` top-level keys are consumed. Other
            slots (``imp``, ``device``, ``app``, etc.) are ignored here --
            the matcher receives whatever shape its caller hands it.

    Returns:
        A dict with two keys:
          - ``"refs"``: a flat list of `AudienceRef` objects in the order
            (standard from user.data, contextual from site.cat, agentic
            from user.ext). Roles are NOT preserved -- OpenRTB does not
            carry primary/constraints/extensions/exclusions semantics on
            the wire (those live on the booking-time `AudiencePlan`
            snapshot referenced by ``deal_id``). All refs returned here are
            ``source='explicit'``.
          - ``"warnings"``: a list of human-readable strings describing
            anything skipped or fallback-substituted during parsing.
            Sellers MAY surface these to the audit trail.

    Notes:
        - This parser does NOT reconstruct an `AudiencePlan` -- the lossy
          OpenRTB mapping cannot recover the role distinction. Callers
          that need a full plan should look up the booked plan snapshot by
          ``deal_id`` instead.
        - For the round-trip parity tested in `test_openrtb_parser`, we
          treat all refs as if they were primary-or-equivalent positive
          targeting hints.
    """

    if not isinstance(bidrequest, dict):
        return {
            "refs": [],
            "warnings": ["bidrequest is not an object"],
        }

    warnings: list[str] = []

    user = bidrequest.get("user") if isinstance(bidrequest.get("user"), dict) else None
    site = bidrequest.get("site") if isinstance(bidrequest.get("site"), dict) else None

    refs: list[AudienceRef] = []
    refs.extend(
        _parse_standard_data_entries(
            (user or {}).get("data") if isinstance((user or {}).get("data"), list) else None,
            warnings,
        )
    )
    refs.extend(_parse_contextual(site, warnings))
    refs.extend(
        _parse_agentic_ext(
            (user or {}).get("ext") if isinstance((user or {}).get("ext"), dict) else None,
            warnings,
        )
    )

    return {"refs": refs, "warnings": warnings}


__all__ = [
    "AGENTIC_USER_EXT_KEY",
    "CONTENT_TAXONOMY_31_CATTAX",
    "IAB_AUDIENCE_TAXONOMY_DATA_NAME",
    "parse_openrtb_audience",
]
