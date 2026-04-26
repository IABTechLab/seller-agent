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

import json
import threading
from pathlib import Path
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

    @classmethod
    def modern_default(cls) -> "AgenticCapabilities":
        """Build a 'modern' agentic-capable seller declaration.

        Per E2-2 + E2-6: a seller running the agent_range stack can match
        against agentic refs minted by the buyer's locked sentence-transformers
        model (384-dim, in [256, 1024]) on all three IAB Agentic Audiences
        signal types (identity / contextual / reinforcement). Sellers opt in
        by setting their package's `agentic_capabilities = AgenticCapabilities.modern_default()`;
        the global default on `Package` stays None for backward compat.
        """

        return cls(
            supported_signal_types=["identity", "contextual", "reinforcement"],
            embedding_dim_range=(256, 1024),
            spec_version="draft-2026-01",
            consent_modes=["IAB-TCFv2", "GPP", "advertiser-1p"],
        )


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


# =============================================================================
# Capability discovery block (proposal §5.7 layer 1)
# =============================================================================
#
# `CapabilityAudienceBlock` is the seller-level audience capability advertised
# on the agent card / capability discovery response. It is *separate* from
# `AudienceCapabilities` (which is per-Package); this block describes what
# this seller agent as a whole can negotiate over.
#
# Buyers pre-flight an `AudiencePlan` against these flags before sending a
# `DealBookingRequest`. A seller that does not ship this block is treated as
# legacy (standard segments only, no constraints/extensions/exclusions, no
# agentic). See proposal §5.7 layers 1-3.
#
# `taxonomy_lock_hashes` carries the sha256 of the seller's vendored taxonomy
# files, loaded dynamically from `data/taxonomies/taxonomies.lock.json` so
# they stay in sync if the lock file is updated. Mismatch with the buyer's
# own lock-file hashes is the cheap inline drift-detection that keeps Epic 1
# from needing heavier schema-hardening up front.


class AgenticCapabilityFlag(BaseModel):
    """Top-level agentic-support flag for the capability discovery block.

    Distinct from per-package `AgenticCapabilities`: this is the seller's
    *overall* declaration of whether agentic matching is on the menu at all.
    Per-package detail (signal types, embedding dim range, spec version) lives
    on each `Package.audience_capabilities.agentic_capabilities`.
    """

    supported: bool = Field(
        default=False,
        description="True if the seller offers agentic matching on any package",
    )

    model_config = {"populate_by_name": True}


class MaxRefsPerRole(BaseModel):
    """Per-role reference cardinality limits the seller will accept.

    Mirrors the four `AudiencePlan` roles (primary / constraints / extensions
    / exclusions). A seller that doesn't support extensions advertises 0 here;
    the buyer's `degrade_plan_for_seller()` (§5.7 layer 2) drops refs in roles
    where the seller's max is 0 before sending the plan.
    """

    primary: int = Field(default=1, ge=0, description="Max primary refs (typically 1)")
    constraints: int = Field(default=3, ge=0, description="Max constraint refs")
    extensions: int = Field(default=0, ge=0, description="Max extension refs")
    exclusions: int = Field(default=0, ge=0, description="Max exclusion refs")

    model_config = {"populate_by_name": True}


class TaxonomyLockHashes(BaseModel):
    """sha256 hashes of the seller's vendored taxonomy files.

    Cheap schema-drift backstop per proposal §5.7. Buyer compares against its
    own lock-file hashes; mismatch logs a warning ("seller is on Content
    Taxonomy 3.0, you are on 3.1") without blocking the deal.

    Always sourced dynamically from `data/taxonomies/taxonomies.lock.json` --
    never hard-coded -- so it stays in sync if the lock file is regenerated.
    """

    audience: str = Field(
        description="sha256 of vendored Audience Taxonomy file, prefixed 'sha256:'",
    )
    content: str = Field(
        description="sha256 of vendored Content Taxonomy file, prefixed 'sha256:'",
    )

    model_config = {"populate_by_name": True}


class CapabilityAudienceBlock(BaseModel):
    """`audience_capabilities` block on the seller's capability discovery response.

    Per proposal §5.7 layer 1. Carries:

    - `schema_version`: bumped on breaking changes; buyer degrades expectations
      conservatively on unknown future versions. Missing/legacy = v0.
    - `standard_taxonomy_versions` / `contextual_taxonomy_versions`: which
      taxonomy versions the seller can interpret. Multi-version lists let a
      seller advertise "I read both 3.0 and 3.1" during a migration window.
    - `agentic.supported`: whether agentic matching is offered at all.
    - `supports_constraints` / `supports_extensions` / `supports_exclusions`:
      per-role gates so the buyer's `degrade_plan_for_seller()` can drop
      unsupported roles before sending the plan.
    - `max_refs_per_role`: cardinality caps per role.
    - `taxonomy_lock_hashes`: sha256 of vendored taxonomy files (cheap inline
      drift backstop).

    A seller that doesn't ship this block is treated as legacy: standard
    segments only, no constraints/extensions/exclusions, no agentic. That's
    the safe default.
    """

    schema_version: str = Field(
        default="1",
        description="Capability-block schema version; bumped on breaking changes",
    )
    standard_taxonomy_versions: list[str] = Field(
        default_factory=lambda: ["1.1"],
        description="IAB Audience Taxonomy versions the seller can interpret",
    )
    contextual_taxonomy_versions: list[str] = Field(
        default_factory=lambda: ["3.1"],
        description="IAB Content Taxonomy versions the seller can interpret",
    )
    agentic: AgenticCapabilityFlag = Field(
        default_factory=AgenticCapabilityFlag,
        description="Top-level agentic-support flag; per-package detail lives on Package",
    )
    supports_constraints: bool = Field(
        default=True,
        description="Whether the seller honors AudiencePlan.constraints (intersect)",
    )
    supports_extensions: bool = Field(
        default=False,
        description="Whether the seller honors AudiencePlan.extensions (union)",
    )
    supports_exclusions: bool = Field(
        default=False,
        description="Whether the seller honors AudiencePlan.exclusions (set-difference)",
    )
    max_refs_per_role: MaxRefsPerRole = Field(
        default_factory=MaxRefsPerRole,
        description="Per-role reference cardinality caps",
    )
    taxonomy_lock_hashes: TaxonomyLockHashes = Field(
        description=(
            "sha256 of seller's vendored taxonomy files (loaded dynamically "
            "from data/taxonomies/taxonomies.lock.json -- never hard-coded)"
        ),
    )

    model_config = {"populate_by_name": True}


# =============================================================================
# Lock-file loader (process-level cache, lock-file-mtime invalidation)
# =============================================================================

# Default location of the vendored taxonomy lock file, relative to the seller
# package root. Tests pass an explicit path to avoid the cache.
_DEFAULT_LOCK_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "taxonomies" / "taxonomies.lock.json"
)

# Process-level cache: (mtime_ns, parsed_lock_dict). The mtime_ns key forces a
# reload when the lock file is rewritten. Wrapped in a lock so concurrent
# capability requests at startup don't double-read.
_lock_cache: dict[Path, tuple[int, dict]] = {}
_lock_cache_mutex = threading.Lock()


def _load_taxonomy_lock(path: Path | None = None) -> dict:
    """Read the vendored taxonomy lock file, with mtime-keyed caching.

    Cache is keyed by absolute path AND mtime_ns, so a regenerated lock file
    on disk (different mtime) forces a reload on next call. Tests can pass
    an explicit path to bypass the cache for the default path.
    """

    resolved = (path or _DEFAULT_LOCK_PATH).resolve()
    mtime_ns = resolved.stat().st_mtime_ns
    with _lock_cache_mutex:
        cached = _lock_cache.get(resolved)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]
        with resolved.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _lock_cache[resolved] = (mtime_ns, data)
        return data


def load_taxonomy_lock_hashes(
    lock_path: Path | None = None,
) -> TaxonomyLockHashes:
    """Build a `TaxonomyLockHashes` from the vendored lock file.

    Reads `data/taxonomies/taxonomies.lock.json` (or `lock_path` when
    provided), pulls the `audience.sha256` and `content.sha256` fields, and
    returns a `TaxonomyLockHashes` with the `sha256:` prefix the wire spec
    requires.

    The lock file is the single source of truth: this function NEVER returns
    hard-coded hashes. If the file is missing or malformed, the underlying
    OSError / KeyError / json.JSONDecodeError surfaces to the caller -- a
    seller that can't read its own taxonomy lock is mis-configured.
    """

    data = _load_taxonomy_lock(lock_path)
    return TaxonomyLockHashes(
        audience=f"sha256:{data['audience']['sha256']}",
        content=f"sha256:{data['content']['sha256']}",
    )


def _versions_from_lock(
    section: str, lock_path: Path | None = None
) -> list[str]:
    """Read a single taxonomy version from the lock file, return as list.

    The lock file pins a single version per taxonomy (e.g., audience 1.1).
    Returning a list lets the seller advertise additional versions later
    without changing the wire shape.
    """

    data = _load_taxonomy_lock(lock_path)
    return [data[section]["version"]]


def build_capability_audience_block(
    *,
    lock_path: Path | None = None,
    agentic_supported: bool = False,
    supports_constraints: bool = True,
    supports_extensions: bool = False,
    supports_exclusions: bool = False,
    max_refs_per_role: MaxRefsPerRole | None = None,
) -> CapabilityAudienceBlock:
    """Build the `audience_capabilities` block for capability discovery.

    Demo / MVP defaults match the locked-in decisions from the proposal:
    - `schema_version` = "1"
    - taxonomy versions sourced from `data/taxonomies/taxonomies.lock.json`
    - `agentic.supported` = False (match endpoint lands in §11)
    - `supports_constraints` = True (filter lands in §10)
    - `supports_extensions` = False, `supports_exclusions` = False
    - `max_refs_per_role` = (1 / 3 / 0 / 0)
    - `taxonomy_lock_hashes` ALWAYS read from the lock file (never hard-coded)

    The kwargs let future beads (§10, §11) flip individual flags without
    changing the call sites that build the agent card.
    """

    return CapabilityAudienceBlock(
        schema_version="1",
        standard_taxonomy_versions=_versions_from_lock("audience", lock_path),
        contextual_taxonomy_versions=_versions_from_lock("content", lock_path),
        agentic=AgenticCapabilityFlag(supported=agentic_supported),
        supports_constraints=supports_constraints,
        supports_extensions=supports_extensions,
        supports_exclusions=supports_exclusions,
        max_refs_per_role=max_refs_per_role or MaxRefsPerRole(),
        taxonomy_lock_hashes=load_taxonomy_lock_hashes(lock_path),
    )
