# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Seller-side `AudienceRef` and `ComplianceContext` data types.

These mirror the buyer's models (`ad_buyer.models.audience_plan.AudienceRef`,
`ad_buyer.models.audience_plan.ComplianceContext`) on the wire. The seller
defines its own copy rather than importing the buyer's models -- the wire
format spec at `docs/api/audience_plan_wire_format.md` is the source of
truth for cross-repo compatibility, and importing across repos would create
deployment coupling we explicitly do not want.

Same shape, same validation rules, same JSON output. If the wire spec
changes, both sides update independently to match.

Bead: ar-roi5 (proposal §5.7, §6 row 8).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Type aliases mirroring the buyer-side definitions.
AudienceType = Literal["standard", "contextual", "agentic"]
AudienceSource = Literal["explicit", "resolved", "inferred"]


class ComplianceContext(BaseModel):
    """Consent regime accompanying an audience reference.

    Required when `AudienceRef.type == 'agentic'`; optional otherwise
    (consent for standard/contextual is usually attached at the campaign
    level rather than per ref).

    Mirrors `ad_buyer.models.audience_plan.ComplianceContext` on the wire.
    """

    jurisdiction: str = Field(
        ...,
        description="Jurisdiction code, e.g. 'US', 'EU', 'GLOBAL'",
    )
    consent_framework: str = Field(
        ...,
        description="Consent framework: 'IAB-TCFv2', 'GPP', 'advertiser-1p', 'none'",
    )
    consent_string_ref: str | None = Field(
        default=None,
        description="Opaque pointer to the consent string (not the raw string)",
    )
    attestation: str | None = Field(
        default=None,
        description="Hash or signature carrying any required attestation",
    )
    embedding_provenance: Literal[
        "local_buyer", "advertiser_supplied", "hosted_external", "mock"
    ] | None = Field(
        default=None,
        description=(
            "Provenance of the embedding bytes (E2-7 Gap 6). Mirrors the "
            "buyer-side ComplianceContext.embedding_provenance per the wire spec."
        ),
    )

    model_config = {"populate_by_name": True}


class AudienceRef(BaseModel):
    """A single audience reference (matches the buyer's wire shape).

    The `type` field discriminates the meaning of `identifier`:
    - standard: IAB Audience Taxonomy ID (e.g. "3-7")
    - contextual: IAB Content Taxonomy ID (e.g. "IAB1-2")
    - agentic: embedding URI (e.g. "emb://buyer.example.com/audiences/x")

    Mirrors `ad_buyer.models.audience_plan.AudienceRef` on the wire.
    """

    type: AudienceType = Field(
        ...,
        description="Audience type: 'standard', 'contextual', or 'agentic'",
    )
    identifier: str = Field(
        ...,
        min_length=1,
        description="ID for standard/contextual; URI for agentic",
    )
    taxonomy: str = Field(
        ...,
        min_length=1,
        description="'iab-audience' | 'iab-content' | 'agentic-audiences'",
    )
    version: str = Field(
        ...,
        min_length=1,
        description="Taxonomy version, e.g. '1.1', '3.1', 'draft-2026-01'",
    )
    source: AudienceSource = Field(
        ...,
        description="Provenance: 'explicit', 'resolved', or 'inferred'",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Match confidence; set when source is resolved/inferred",
    )
    compliance_context: ComplianceContext | None = Field(
        default=None,
        description="Consent context; required when type='agentic'",
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_compliance_for_agentic(self) -> AudienceRef:
        """Agentic refs MUST carry a compliance_context."""

        if self.type == "agentic" and self.compliance_context is None:
            raise ValueError(
                "AudienceRef.compliance_context is required when type='agentic'"
            )
        return self

    @model_validator(mode="after")
    def _validate_confidence_provenance(self) -> AudienceRef:
        """Explicit refs should not carry a confidence score."""

        if self.source == "explicit" and self.confidence is not None:
            raise ValueError(
                "AudienceRef.confidence must be None when source='explicit'"
            )
        return self
