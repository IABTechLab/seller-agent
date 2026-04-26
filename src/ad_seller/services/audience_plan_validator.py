# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Validate an incoming buyer `AudiencePlan` against the seller's capabilities.

Implements proposal §5.7 layer 3 (forward-compatible structured rejection):

> If a seller receives a plan with a field or type it doesn't recognize
> (because the buyer pre-flight is missing or stale), it rejects the
> booking with a structured error:
>
>     {
>       "error": "audience_plan_unsupported",
>       "unsupported": [
>         {"path": "extensions[0]", "reason": "extensions not supported by this seller"},
>         {"path": "primary.taxonomy", "reason": "version 3.2 not supported"}
>       ]
>     }

The buyer's orchestrator catches this, applies degradation, and retries.
This module produces the structured `unsupported` list; the API layer
turns it into the 400 response.

Bead: ar-sn8f (proposal §5.7 layer 3 + §6 row 11).
"""

from __future__ import annotations

from typing import Any

from ..models.audience_capabilities import CapabilityAudienceBlock

# JSON-path-ish keys we surface in the structured error.
_PRIMARY = "primary"
_CONSTRAINTS = "constraints"
_EXTENSIONS = "extensions"
_EXCLUSIONS = "exclusions"


def _ref_taxonomy_supported(
    ref: dict[str, Any],
    capabilities: CapabilityAudienceBlock,
) -> tuple[bool, str | None]:
    """Return (ok, reason) for a single ref's taxonomy/version compatibility.

    Standard refs check `standard_taxonomy_versions`; contextual refs check
    `contextual_taxonomy_versions`; agentic refs check `agentic.supported`.
    Unknown ref types are reported as unsupported (forward-compat: the seller
    doesn't know how to interpret them).
    """

    ref_type = ref.get("type")
    version = ref.get("version", "")

    if ref_type == "standard":
        if version and version not in capabilities.standard_taxonomy_versions:
            return False, (
                f"standard taxonomy version {version!r} not supported "
                f"(seller supports {sorted(capabilities.standard_taxonomy_versions)})"
            )
        return True, None

    if ref_type == "contextual":
        if version and version not in capabilities.contextual_taxonomy_versions:
            return False, (
                f"contextual taxonomy version {version!r} not supported "
                f"(seller supports {sorted(capabilities.contextual_taxonomy_versions)})"
            )
        return True, None

    if ref_type == "agentic":
        if not capabilities.agentic.supported:
            return False, "agentic refs not supported by this seller"
        return True, None

    # Unknown type -- forward-compat reject.
    return False, f"unknown audience ref type {ref_type!r}"


def validate_audience_plan(
    audience_plan: dict[str, Any] | None,
    capabilities: CapabilityAudienceBlock,
) -> list[dict[str, str]]:
    """Compare an audience_plan against the seller's capability block.

    Returns a list of `{"path": str, "reason": str}` entries describing every
    unsupported part of the plan. An empty list means the plan is fully
    supportable.

    The function is robust to a missing or empty plan (returns []) so callers
    can pre-flight even on legacy booking requests that don't carry an
    audience_plan.
    """

    if not audience_plan:
        return []

    unsupported: list[dict[str, str]] = []

    # ---- primary ----
    primary = audience_plan.get(_PRIMARY)
    if isinstance(primary, dict):
        ok, reason = _ref_taxonomy_supported(primary, capabilities)
        if not ok:
            unsupported.append({"path": "primary.taxonomy", "reason": reason or ""})

    # ---- per-role gates + per-ref taxonomy checks ----
    role_pairs = [
        (_CONSTRAINTS, capabilities.supports_constraints),
        (_EXTENSIONS, capabilities.supports_extensions),
        (_EXCLUSIONS, capabilities.supports_exclusions),
    ]
    role_caps = capabilities.max_refs_per_role.model_dump()

    for role, role_supported in role_pairs:
        refs = audience_plan.get(role) or []
        if not isinstance(refs, list):
            continue

        # Role gate: if role is non-empty but seller can't honor it, reject.
        if refs and not role_supported:
            unsupported.append(
                {
                    "path": f"{role}[0]",
                    "reason": f"{role} not supported by this seller",
                }
            )
            # Once the role is gated off, no point reporting per-ref errors.
            continue

        # Cardinality cap.
        max_for_role = role_caps.get(role, 0)
        if len(refs) > max_for_role:
            unsupported.append(
                {
                    "path": f"{role}",
                    "reason": (
                        f"{len(refs)} refs in {role} exceeds max_refs_per_role.{role}"
                        f"={max_for_role}"
                    ),
                }
            )

        # Per-ref taxonomy/version checks.
        for idx, ref in enumerate(refs):
            if not isinstance(ref, dict):
                unsupported.append(
                    {
                        "path": f"{role}[{idx}]",
                        "reason": "ref is not an object",
                    }
                )
                continue
            ok, reason = _ref_taxonomy_supported(ref, capabilities)
            if not ok:
                unsupported.append(
                    {"path": f"{role}[{idx}].taxonomy", "reason": reason or ""}
                )

    return unsupported
