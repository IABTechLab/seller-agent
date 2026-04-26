"""E2-10: cross-repo schema-drift hardening (seller-side mirror).

Asserts that the seller's mirror models (`AudienceRef`, `ComplianceContext`)
emit JSON Schemas compatible with the canonical buyer-emitted snapshot at
`agent_range/docs/api/audience_plan_schemas.json`. The seller doesn't ship
its own `AudiencePlan` (the seller stores the buyer's plan as a snapshot,
not as a typed model), so this test only checks the two mirrored types.

Compatibility, not byte-equality: the seller's `model_json_schema()` may
have minor stylistic differences (description text, $defs ordering) but
the `properties` shape MUST match — same field names, same types, same
required-flag, same enums.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_seller.models.audience_ref import AudienceRef, ComplianceContext


CANONICAL_SCHEMA_PATH = Path(
    "/Users/aidancardella/dev/agent_range/.worktrees/audience-extension/docs/api/audience_plan_schemas.json"
)


def _load_canonical() -> dict:
    if not CANONICAL_SCHEMA_PATH.exists():
        pytest.skip(f"Canonical schema snapshot not present: {CANONICAL_SCHEMA_PATH}")
    return json.loads(CANONICAL_SCHEMA_PATH.read_text())


def _shape(schema: dict) -> dict:
    """Reduce a schema to its drift-relevant shape: properties + required + enums."""

    props = schema.get("properties", {})
    reduced = {}
    for name, p in props.items():
        if "enum" in p:
            reduced[name] = {"enum": sorted(p["enum"])}
        elif "anyOf" in p:
            # Capture the type names + required flag, ignore description
            types = sorted(
                a.get("type", a.get("$ref", "ref")) for a in p.get("anyOf", [])
            )
            reduced[name] = {"anyOf_types": types}
        elif "type" in p:
            reduced[name] = {"type": p["type"]}
        else:
            # $ref or fallback
            reduced[name] = {"shape": "complex"}
    return {
        "properties": reduced,
        "required": sorted(schema.get("required", [])),
    }


class TestSellerMirrorMatchesCanonical:
    def test_compliance_context_field_shape(self):
        canonical = _load_canonical()["ComplianceContext"]
        live = ComplianceContext.model_json_schema()
        assert _shape(live) == _shape(canonical)

    def test_audience_ref_field_shape(self):
        canonical = _load_canonical()["AudienceRef"]
        live = AudienceRef.model_json_schema()
        assert _shape(live) == _shape(canonical)
