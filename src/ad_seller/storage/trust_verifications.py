# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Auditable store for buyer trust-tier verification outcomes (EP-5.2).

Every time a price-moving path verifies a buyer agent against the registry,
the verdict is persisted as the shared contract library's ``VerifiedTrust``
primitive, wrapped in an envelope that records what was claimed, what
ceiling was enforced, and on which endpoint. This is the audit trail for
"why did this buyer get this tier?".

Persistence is fail-closed, following the EP-0.2 audit-event pattern
(events/audit_fallback.py): if the storage backend write fails, the record
is appended to the durable audit-fallback JSONL; if that also fails, the
error propagates to the caller instead of silently losing audit trail.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from iab_agentic_primitives.registry_client import VerifiedTrust

from ..events.audit_fallback import write_audit_fallback
from .base import StorageBackend

logger = logging.getLogger(__name__)

STORAGE_PREFIX = "verified_trust:"
INDEX_PREFIX = "verified_trust_index:"


def _url_hash(url: str) -> str:
    """Deterministic short hash of a URL for index keys (matches registry)."""
    return hashlib.sha256(url.rstrip("/").encode()).hexdigest()[:16]


class TrustVerificationStore:
    """Records registry trust-verification outcomes for audit.

    Uses the existing StorageBackend (key-value) to persist one record per
    verification keyed by a fresh verification_id, with a secondary index
    by agent URL for history lookups (same pattern as QuoteHistoryStore).
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    async def record_verification(
        self,
        verified_trust: VerifiedTrust,
        *,
        agent_url: str,
        claimed_tier: Optional[str],
        effective_ceiling: Optional[str],
        endpoint: str,
    ) -> dict[str, Any]:
        """Persist one verification outcome (fail-closed).

        Args:
            verified_trust: The shared-contract trust verdict primitive.
            agent_url: The buyer agent URL that was verified.
            claimed_tier: The tier the buyer's identity claims implied.
            effective_ceiling: The enforced ceiling (None = blocked/rejected).
            endpoint: The price-moving path that requested verification.

        Returns:
            The stored record.
        """
        verification_id = f"vt-{uuid.uuid4().hex[:12]}"
        record: dict[str, Any] = {
            "verification_id": verification_id,
            "agent_url": agent_url,
            "claimed_tier": claimed_tier,
            "effective_ceiling": effective_ceiling,
            "endpoint": endpoint,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            # The auditable primitive itself — re-validates as VerifiedTrust.
            "verified_trust": verified_trust.model_dump(mode="json"),
        }

        try:
            await self._storage.set(f"{STORAGE_PREFIX}{verification_id}", record)
            index_key = f"{INDEX_PREFIX}{_url_hash(agent_url)}"
            existing_ids = await self._storage.get(index_key) or []
            if verification_id not in existing_ids:
                existing_ids.append(verification_id)
                await self._storage.set(index_key, existing_ids)
        except Exception as e:
            # EP-0.2 fail-closed pattern: durable JSONL fallback; a fallback
            # failure propagates rather than losing the audit record.
            fallback = dict(record)
            fallback["record_type"] = "trust_verification"
            fallback["storage_error"] = str(e)
            write_audit_fallback(fallback)
            logger.warning(
                "Trust-verification storage write failed; wrote audit fallback: %s", e
            )

        return record

    async def get_verification(self, verification_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a single verification record by id."""
        return await self._storage.get(f"{STORAGE_PREFIX}{verification_id}")

    async def list_for_agent(self, agent_url: str) -> list[dict[str, Any]]:
        """List all verification records for an agent URL."""
        index_key = f"{INDEX_PREFIX}{_url_hash(agent_url)}"
        ids = await self._storage.get(index_key) or []
        records = []
        for vid in ids:
            record = await self._storage.get(f"{STORAGE_PREFIX}{vid}")
            if record is not None:
                records.append(record)
        return records
