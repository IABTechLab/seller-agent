# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Fulfillment-adjacent helpers for deal-time audience-plan handling.

Per proposal §5.1 Step 2 (snapshot honor policy):

> If the seller drops support for an audience type after booking but before
> fulfillment (e.g., agentic capability is decommissioned), the seller honors
> the snapshot frozen at booking time. The buyer's `audience_plan_id` hash is
> the proof of what was agreed.

`honor_audience_plan_snapshot()` is the minimal helper fulfillment paths can
call to retrieve the frozen plan and emit a structured warning when the
seller's *current* capabilities are weaker than what was promised at booking.

Bead: ar-sn8f (proposal §5.1 Step 2 + §6 row 11).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..models.audience_capabilities import CapabilityAudienceBlock

logger = logging.getLogger(__name__)


def _capabilities_have_degraded(
    snapshot_plan: dict[str, Any],
    current_capabilities: CapabilityAudienceBlock,
) -> list[str]:
    """Compare a frozen audience_plan against the seller's *current* caps.

    Returns a list of human-readable degradation messages. Empty list means
    no degradation (current capabilities still cover the snapshot).

    The check is intentionally conservative: it flags any case where the
    snapshot expects a capability the seller no longer advertises. Subtle
    questions (e.g., a seller that *narrowed* its standard taxonomy versions)
    are out of scope for this helper -- callers can layer richer checks on
    top.
    """

    degradations: list[str] = []

    # Did the snapshot include extensions, but the seller no longer supports them?
    if snapshot_plan.get("extensions") and not current_capabilities.supports_extensions:
        degradations.append(
            "snapshot includes extensions[] but seller no longer supports extensions"
        )

    # Constraints
    if snapshot_plan.get("constraints") and not current_capabilities.supports_constraints:
        degradations.append(
            "snapshot includes constraints[] but seller no longer supports constraints"
        )

    # Exclusions
    if snapshot_plan.get("exclusions") and not current_capabilities.supports_exclusions:
        degradations.append(
            "snapshot includes exclusions[] but seller no longer supports exclusions"
        )

    # Agentic refs anywhere in the plan vs. seller-level agentic flag
    def _has_agentic(refs: Any) -> bool:
        if not refs:
            return False
        if isinstance(refs, dict):
            refs = [refs]
        return any(
            isinstance(r, dict) and r.get("type") == "agentic" for r in refs
        )

    has_agentic_in_snapshot = (
        _has_agentic(snapshot_plan.get("primary"))
        or _has_agentic(snapshot_plan.get("constraints"))
        or _has_agentic(snapshot_plan.get("extensions"))
        or _has_agentic(snapshot_plan.get("exclusions"))
    )
    if has_agentic_in_snapshot and not current_capabilities.agentic.supported:
        degradations.append(
            "snapshot includes agentic refs but seller no longer supports agentic"
        )

    return degradations


def honor_audience_plan_snapshot(
    deal_id: str,
    deal_record: Optional[dict[str, Any]],
    current_capabilities: CapabilityAudienceBlock,
) -> Optional[dict[str, Any]]:
    """Return the frozen audience_plan snapshot for a booked deal.

    Per §5.1 Step 2: the snapshot is what the buyer and seller agreed to at
    booking time. Even if the seller's *current* capabilities have shrunk, the
    snapshot is honored -- the deal_id + audience_plan_id hash are the
    forensic anchor.

    Args:
        deal_id: The booked deal's ID. Used for log correlation.
        deal_record: The seller's deal storage record (or None when missing).
            Expected to carry `audience_plan_snapshot` keyed off the booking.
        current_capabilities: The seller's *current* capability block. Used
            only for the degradation warning -- it does NOT modify the
            returned snapshot.

    Returns:
        The frozen `audience_plan_snapshot` dict if present; None when the
        deal record is missing or carries no snapshot. The returned object
        is the snapshot exactly as stored (no rewriting, no degradation).

    Side effect: logs a WARNING when the seller's current capabilities are
    weaker than the snapshot. The deal still proceeds; the warning is the
    forensic surface fulfillment-adjacent code or audit jobs can scrape.
    """

    if not deal_record:
        logger.info("honor_audience_plan_snapshot: deal_record missing for %s", deal_id)
        return None

    snapshot = deal_record.get("audience_plan_snapshot")
    if not snapshot:
        # Pre-§11 deals booked before audience_plan_snapshot was wired. Not an
        # error; just nothing to honor. Caller decides what to do.
        logger.info(
            "honor_audience_plan_snapshot: no snapshot on deal %s (pre-§11 booking)",
            deal_id,
        )
        return None

    degradations = _capabilities_have_degraded(snapshot, current_capabilities)
    if degradations:
        # Single structured warning so audit log scrapers can correlate by
        # deal_id and audience_plan_id.
        plan_id = snapshot.get("audience_plan_id", "<unhashed>")
        logger.warning(
            "honor_audience_plan_snapshot: capabilities degraded for deal=%s "
            "audience_plan_id=%s degradations=%s -- snapshot honored per §5.1",
            deal_id,
            plan_id,
            degradations,
        )

    return snapshot
