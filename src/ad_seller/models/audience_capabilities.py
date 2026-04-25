# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Typed `AudienceCapabilities` for seller `Package` positioning.

Replaces the flat `audience_segment_ids: list[str]` on `Package` with a
typed three-dimension structure parallel to how `cat`/`cattax` already
handles content taxonomy versioning:

- standard segments (IAB Audience Taxonomy 1.1 IDs, the existing dimension)
- contextual segments (IAB Content Taxonomy 3.1 IDs as audience-intent,
  distinct from `cat` which describes the content itself)
- agentic capabilities (declares whether the package can match against
  buyer-supplied embeddings, and on what signal types)

A package optimized for direct response declares dense `standard_segment_ids`
and leaves `agentic_capabilities` null. A package selling content adjacency
declares `contextual_segment_ids`. A package supporting advertiser
first-party activation declares `agentic_capabilities` -- the premium tier.

See proposal §5.7 and bead ar-roi5.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Signal type discriminator mirrors the IAB Agentic Audiences (DRAFT 2026-01)
# spec's three-axis model. Held here as a Literal rather than an Enum so it
# stays JSON-friendly and matches the seller code style elsewhere.
SignalType = Literal["identity", "contextual", "reinforcement"]


class AgenticCapabilities(BaseModel):
    """Declares a package's agentic-audience matching capabilities.

    A null `Package.audience_capabilities.agentic_capabilities` means the
    seller does not support agentic matching for this package. A populated
    object declares which signal types, embedding dimensions, and consent
    modes the seller can match on.

    Defaults are conservative: empty signal types, the spec's documented
    embedding-dim range, the current draft spec version.
    """

    supported_signal_types: list[SignalType] = Field(
        default_factory=list,
        description="Signal types this package can match on: 'identity', 'contextual', 'reinforcement'",
    )
    embedding_dim_range: tuple[int, int] = Field(
        default=(256, 1024),
        description="Inclusive (min, max) embedding dimensions accepted",
    )
    spec_version: str = Field(
        default="draft-2026-01",
        description="IAB Agentic Audiences spec version; bumped when ratified",
    )
    consent_modes: list[str] = Field(
        default_factory=list,
        description="Accepted consent frameworks, e.g. ['IAB-TCFv2', 'GPP', 'advertiser-1p']",
    )

    model_config = {"populate_by_name": True}


class AudienceCapabilities(BaseModel):
    """Typed audience-capability declaration for a `Package`.

    Replaces the flat `audience_segment_ids: list[str]` field. Carries:

    - standard_segment_ids + standard_taxonomy_version: IAB Audience
      Taxonomy IDs the package can target. Was the legacy
      `audience_segment_ids` field; defaults to AT 1.1.
    - contextual_segment_ids + contextual_taxonomy_version: IAB Content
      Taxonomy IDs interpreted as *audience intent* (what the audience is
      reading), distinct from `cat` which describes the content itself.
      Defaults to CT 3.1.
    - agentic_capabilities: optional declaration that the package can match
      against buyer-supplied embeddings. Null = not supported.

    The presence of any of these dimensions is what the seller advertises in
    the public capability discovery response (versions + supports flags
    only); segment lists are exposed in the authenticated view only.
    """

    standard_segment_ids: list[str] = Field(
        default_factory=list,
        description="IAB Audience Taxonomy 1.1 segment IDs, e.g. ['3', '4', '5']",
    )
    standard_taxonomy_version: str = Field(
        default="1.1",
        description="IAB Audience Taxonomy version pinned by this package",
    )
    contextual_segment_ids: list[str] = Field(
        default_factory=list,
        description="IAB Content Taxonomy 3.1 IDs as audience intent, e.g. ['IAB1-2']",
    )
    contextual_taxonomy_version: str = Field(
        default="3.1",
        description="IAB Content Taxonomy version pinned by this package",
    )
    agentic_capabilities: AgenticCapabilities | None = Field(
        default=None,
        description="Agentic embedding-match capabilities; null when unsupported",
    )

    model_config = {"populate_by_name": True}
