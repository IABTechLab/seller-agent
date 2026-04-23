# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quote history storage for seller-side pricing validation.

Records every quote the seller issues and provides verification
against incoming buyer proposals. This is Layer 4 of the CPM
hallucination fix — defense in depth that catches fabricated pricing
even if buyer-side guards fail.

Bead: ar-hm9l (child of epic ar-rrgw)
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from ad_seller.storage.base import StorageBackend


class PricingVerificationResult(BaseModel):
    """Result of verifying a proposal's CPM against quote history."""

    pricing_verified: bool = False
    matched_quote_id: Optional[str] = None
    reason: str = ""


class QuoteHistoryStore:
    """Records and verifies quotes issued by the seller.

    Uses the existing StorageBackend (key-value) to persist quote
    history entries keyed by quote_id, with a secondary index by
    buyer_id + product_id for fast lookup during proposal validation.

    Tolerance: proposed CPM must be within 1% of a non-expired quote
    to be considered verified.
    """

    # Default CPM tolerance: 1% relative difference
    DEFAULT_TOLERANCE_PCT: float = 0.01

    def __init__(
        self,
        storage: StorageBackend,
        tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    ) -> None:
        self._storage = storage
        self._tolerance_pct = tolerance_pct

    async def record_quote(
        self,
        quote_id: str,
        buyer_id: str,
        product_id: str,
        quoted_cpm: float,
        expires_at: Optional[datetime] = None,
    ) -> dict:
        """Persist a quote to history when the seller issues it.

        Args:
            quote_id: Unique identifier for the quote.
            buyer_id: Buyer identifier (API key, seat ID, etc.).
            product_id: Product the quote is for.
            quoted_cpm: The final CPM in the quote.
            expires_at: When the quote expires (optional).

        Returns:
            The stored quote history record.
        """
        now = datetime.now(timezone.utc)
        record = {
            "quote_id": quote_id,
            "buyer_id": buyer_id,
            "product_id": product_id,
            "quoted_cpm": quoted_cpm,
            "quoted_at": now.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        }

        # Store by quote_id
        await self._storage.set(
            f"quote_history:{quote_id}", record
        )

        # Maintain a buyer+product index for fast lookup
        index_key = f"quote_history_index:{buyer_id}:{product_id}"
        existing_ids = await self._storage.get(index_key) or []
        if quote_id not in existing_ids:
            existing_ids.append(quote_id)
            await self._storage.set(index_key, existing_ids)

        return record

    async def get_quote(self, quote_id: str) -> Optional[dict]:
        """Retrieve a single quote history record by ID."""
        return await self._storage.get(f"quote_history:{quote_id}")

    async def find_quotes(
        self,
        buyer_id: str,
        product_id: str,
    ) -> list[dict]:
        """Find all quotes issued to a buyer for a product.

        Args:
            buyer_id: Buyer identifier.
            product_id: Product identifier.

        Returns:
            List of quote history records (may include expired).
        """
        index_key = f"quote_history_index:{buyer_id}:{product_id}"
        quote_ids = await self._storage.get(index_key) or []

        quotes = []
        for qid in quote_ids:
            record = await self._storage.get(f"quote_history:{qid}")
            if record is not None:
                quotes.append(record)
        return quotes

    async def verify_pricing(
        self,
        buyer_id: str,
        product_id: str,
        proposed_cpm: float,
        tolerance_pct: Optional[float] = None,
    ) -> PricingVerificationResult:
        """Verify a proposed CPM against quote history.

        Checks whether the seller previously quoted a CPM to this buyer
        for this product that is within tolerance of the proposed CPM
        and has not expired.

        Args:
            buyer_id: Buyer identifier.
            product_id: Product identifier.
            proposed_cpm: The CPM the buyer is proposing.
            tolerance_pct: Override for the default tolerance (0.01 = 1%).

        Returns:
            PricingVerificationResult with verified/unverified status.
        """
        tol = tolerance_pct if tolerance_pct is not None else self._tolerance_pct
        quotes = await self.find_quotes(buyer_id, product_id)

        if not quotes:
            return PricingVerificationResult(
                pricing_verified=False,
                reason="No matching quote found for this buyer and product.",
            )

        now = datetime.now(timezone.utc)

        for quote in quotes:
            # Check expiration
            expires_at_str = quote.get("expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str)
                # Ensure timezone-aware comparison
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if now > expires_at:
                    continue  # Skip expired quotes

            # Check CPM tolerance
            quoted_cpm = quote["quoted_cpm"]
            if quoted_cpm == 0:
                # Avoid division by zero; exact match only
                if proposed_cpm == 0:
                    return PricingVerificationResult(
                        pricing_verified=True,
                        matched_quote_id=quote["quote_id"],
                        reason="Exact match with quoted CPM of $0.00.",
                    )
                continue

            relative_diff = abs(proposed_cpm - quoted_cpm) / quoted_cpm
            if relative_diff <= tol:
                return PricingVerificationResult(
                    pricing_verified=True,
                    matched_quote_id=quote["quote_id"],
                    reason=(
                        f"Proposed CPM ${proposed_cpm:.2f} matches quote "
                        f"{quote['quote_id']} (${quoted_cpm:.2f}, "
                        f"{relative_diff*100:.1f}% difference)."
                    ),
                )

        # If we got here, we have quotes but none match
        # Determine if all are expired or all are outside tolerance
        all_expired = True
        for quote in quotes:
            expires_at_str = quote.get("expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if now <= expires_at:
                    all_expired = False
                    break
            else:
                all_expired = False
                break

        if all_expired:
            return PricingVerificationResult(
                pricing_verified=False,
                reason="All quotes for this buyer and product have expired.",
            )

        return PricingVerificationResult(
            pricing_verified=False,
            reason=(
                f"Proposed CPM ${proposed_cpm:.2f} is outside tolerance "
                f"of all active quotes for this buyer and product."
            ),
        )
