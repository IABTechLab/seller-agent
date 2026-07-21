# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Negotiation Engine - Multi-round buyer-seller negotiation.

Stateless engine that evaluates buyer offers and generates counter-proposals
using strategy-per-buyer-tier, concession tracking, and walk-away logic.
All state is externalized via NegotiationHistory.

Composes PricingRulesEngine (for tier-adjusted base prices) and
YieldOptimizer (for yield scoring). No duplicated pricing logic.
"""

import logging
from datetime import datetime
from typing import Optional

from ..models.buyer_identity import AccessTier, BuyerContext
from ..models.negotiation import (
    STRATEGY_LIMITS,
    TIER_STRATEGY_MAP,
    NegotiationAction,
    NegotiationHistory,
    NegotiationRound,
    NegotiationStrategy,
)
from .pricing_rules_engine import PricingRulesEngine
from .yield_optimizer import YieldOptimizer

logger = logging.getLogger(__name__)


class NegotiationEngine:
    """Stateless engine for multi-round negotiation.

    Strategy per buyer tier:
    - PUBLIC → AGGRESSIVE: 30% buyer share, 3% per-round cap, 8% total cap, 3 rounds
    - SEAT → STANDARD: 40% buyer share, 4% per-round cap, 12% total cap, 4 rounds
    - AGENCY → COLLABORATIVE: 50% buyer share, 5% per-round cap, 15% total cap, 5 rounds
    - ADVERTISER → PREMIUM: 65% buyer share, 6% per-round cap, 20% total cap, 6 rounds

    Example:
        engine = NegotiationEngine(pricing_engine, yield_optimizer)
        history = engine.start_negotiation(
            proposal_id="prop-123",
            product_id="ctv-premium",
            buyer_context=buyer_ctx,
            base_price=35.0,
            floor_price=20.0,
        )
        round_result = engine.evaluate_buyer_offer(history, buyer_price=25.0, buyer_context=buyer_ctx)
        history = engine.record_round(history, round_result)
    """

    def __init__(
        self,
        pricing_engine: PricingRulesEngine,
        yield_optimizer: YieldOptimizer,
    ) -> None:
        self._pricing = pricing_engine
        self._yield = yield_optimizer

    def start_negotiation(
        self,
        proposal_id: str,
        product_id: str,
        buyer_context: Optional[BuyerContext],
        base_price: float,
        floor_price: float,
        package_id: Optional[str] = None,
    ) -> NegotiationHistory:
        """Initialize a negotiation. Picks strategy from buyer tier, sets limits.

        Args:
            proposal_id: The proposal being negotiated
            product_id: Product under negotiation
            buyer_context: Buyer identity context
            base_price: Seller's starting price (before tier adjustment)
            floor_price: Absolute floor price
            package_id: Optional package being negotiated

        Returns:
            Initialized NegotiationHistory ready for rounds
        """
        tier = buyer_context.effective_tier if buyer_context else AccessTier.PUBLIC
        strategy = TIER_STRATEGY_MAP.get(tier, NegotiationStrategy.AGGRESSIVE)
        limits = STRATEGY_LIMITS[strategy]

        # Apply tier discount to get the effective base price
        tier_adjusted_price = base_price
        if buyer_context:
            price_display = self._pricing.get_price_display(base_price, buyer_context=buyer_context)
            if price_display["type"] == "exact":
                tier_adjusted_price = price_display["price"]

        history = NegotiationHistory(
            proposal_id=proposal_id,
            product_id=product_id,
            buyer_tier=tier,
            strategy=strategy,
            limits=limits,
            base_price=tier_adjusted_price,
            floor_price=floor_price,
            package_id=package_id,
        )

        logger.info(
            "Negotiation started: %s | strategy=%s | base=$%.2f | floor=$%.2f",
            history.negotiation_id,
            strategy.value,
            tier_adjusted_price,
            floor_price,
        )
        return history

    def evaluate_buyer_offer(
        self,
        history: NegotiationHistory,
        buyer_price: float,
        buyer_context: Optional[BuyerContext] = None,
    ) -> NegotiationRound:
        """Evaluate a buyer's offer and decide accept/counter/reject/final_offer.

        Logic:
        1. If buyer_price >= base_price → ACCEPT
        2. If buyer_price <= 0 → REJECT (not a valid offer)
        3. If max_rounds exceeded → REJECT (walk-away)
        3.5 If buyer_price < floor_price → COUNTER at the floor — invite
           the buyer up (ALL below-floor offers
           counter at the floor, no walk-away threshold); FINAL_OFFER on
           the strategy's last round
        4. If cumulative concession would exceed total_cap → FINAL_OFFER
        5. Otherwise → COUNTER with gap-split, capped by per_round_cap

        Args:
            history: Current negotiation state
            buyer_price: Buyer's offered price
            buyer_context: Buyer identity context

        Returns:
            NegotiationRound with the action and counter price
        """
        round_number = len(history.rounds) + 1
        limits = history.limits
        cumulative_concession = self._cumulative_concession(history)

        # 1. Accept if buyer meets or exceeds base price, or meets last counter
        last_seller_price = (
            history.rounds[-1].seller_price if history.rounds else history.base_price
        )
        if buyer_price >= history.base_price or buyer_price >= last_seller_price:
            return NegotiationRound(
                round_number=round_number,
                buyer_price=buyer_price,
                seller_price=buyer_price,
                action=NegotiationAction.ACCEPT,
                concession_pct=0.0,
                cumulative_concession_pct=cumulative_concession,
                rationale="Buyer price meets or exceeds seller target. Deal accepted.",
            )

        # 2. Reject nonpositive offers — not a valid price to negotiate.
        #    Every VALID below-floor offer is countered at the floor in 3.5
        # (no deep-lowball walk-away threshold).
        below_floor = buyer_price < history.floor_price
        if buyer_price <= 0:
            return NegotiationRound(
                round_number=round_number,
                buyer_price=buyer_price,
                seller_price=history.base_price,
                action=NegotiationAction.REJECT,
                concession_pct=0.0,
                cumulative_concession_pct=cumulative_concession,
                rationale=(
                    f"Buyer price ${buyer_price:.2f} is not a valid offer. "
                    f"Cannot negotiate."
                ),
            )

        # 3. Reject if max rounds exceeded (bounds BOTH the gap-split path
        #    and repeated below-floor counters — no counter loops)
        if round_number > limits.max_rounds:
            return NegotiationRound(
                round_number=round_number,
                buyer_price=buyer_price,
                seller_price=history.base_price,
                action=NegotiationAction.REJECT,
                concession_pct=0.0,
                cumulative_concession_pct=cumulative_concession,
                rationale=(
                    f"Maximum {limits.max_rounds} rounds reached. "
                    f"Negotiation concluded without agreement."
                ),
            )

        # 3.5 Below-floor offer: counter AT the floor — the seller's minimum
        # viable price — inviting the buyer up (this policy is
        #     universal for ALL below-floor offers). The floor is
        #     firm, so the strategy's last round is a FINAL_OFFER; round
        #     bounding stays with check 3 above.
        if below_floor:
            floor_counter = round(history.floor_price, 2)
            this_round_concession = (
                max(0.0, (history.base_price - floor_counter) / history.base_price)
                if history.base_price > 0
                else 0.0
            )
            incremental = max(0.0, this_round_concession - cumulative_concession)
            is_last_round = round_number == limits.max_rounds
            return NegotiationRound(
                round_number=round_number,
                buyer_price=buyer_price,
                seller_price=floor_counter,
                action=(
                    NegotiationAction.FINAL_OFFER if is_last_round else NegotiationAction.COUNTER
                ),
                concession_pct=incremental,
                cumulative_concession_pct=cumulative_concession + incremental,
                rationale=(
                    f"Final offer at our floor price ${floor_counter:.2f} CPM. "
                    f"We cannot go lower."
                    if is_last_round
                    else (
                        f"Buyer offer ${buyer_price:.2f} is below our floor. "
                        f"Countering at the floor price ${floor_counter:.2f} CPM — "
                        f"the minimum viable price for this inventory "
                        f"(round {round_number}/{limits.max_rounds})."
                    )
                ),
            )

        # Generate counter price
        counter_price = self._generate_counter(history, buyer_price)

        # Calculate this round's concession
        if history.base_price > 0:
            this_round_concession = (history.base_price - counter_price) / history.base_price
        else:
            this_round_concession = 0.0

        # Subtract prior concession to get the incremental concession
        incremental_concession = max(0.0, this_round_concession - cumulative_concession)
        new_cumulative = cumulative_concession + incremental_concession

        # 4. Final offer if approaching total cap
        remaining_cap = limits.total_concession_cap - cumulative_concession
        if incremental_concession >= remaining_cap * 0.8 or round_number == limits.max_rounds:
            # Make a final offer at the best we can do
            final_price = max(
                history.floor_price,
                history.base_price * (1 - limits.total_concession_cap),
            )
            final_concession = (
                (history.base_price - final_price) / history.base_price
                if history.base_price > 0
                else 0.0
            )

            # Accept if buyer is already at or above our final offer
            if buyer_price >= final_price:
                return NegotiationRound(
                    round_number=round_number,
                    buyer_price=buyer_price,
                    seller_price=buyer_price,
                    action=NegotiationAction.ACCEPT,
                    concession_pct=incremental_concession,
                    cumulative_concession_pct=new_cumulative,
                    rationale="Buyer price is acceptable within concession limits.",
                )

            return NegotiationRound(
                round_number=round_number,
                buyer_price=buyer_price,
                seller_price=round(final_price, 2),
                action=NegotiationAction.FINAL_OFFER,
                concession_pct=final_concession - cumulative_concession,
                cumulative_concession_pct=final_concession,
                rationale=(
                    f"Final offer at ${final_price:.2f} CPM. "
                    f"This represents our maximum concession of "
                    f"{limits.total_concession_cap * 100:.0f}%."
                ),
            )

        # 5. Counter with gap-split
        return NegotiationRound(
            round_number=round_number,
            buyer_price=buyer_price,
            seller_price=round(counter_price, 2),
            action=NegotiationAction.COUNTER,
            concession_pct=incremental_concession,
            cumulative_concession_pct=new_cumulative,
            rationale=(
                f"Counter at ${counter_price:.2f} CPM "
                f"({history.strategy.value} strategy, "
                f"round {round_number}/{limits.max_rounds})."
            ),
        )

    def record_round(
        self,
        history: NegotiationHistory,
        negotiation_round: NegotiationRound,
    ) -> NegotiationHistory:
        """Append a round and update status if terminal.

        Args:
            history: Current negotiation state
            negotiation_round: Round to record

        Returns:
            Updated NegotiationHistory (new instance)
        """
        updated = history.model_copy(deep=True)
        updated.rounds.append(negotiation_round)

        if negotiation_round.action == NegotiationAction.ACCEPT:
            updated.status = "accepted"
            updated.completed_at = datetime.utcnow()
        elif negotiation_round.action == NegotiationAction.REJECT:
            updated.status = "rejected"
            updated.completed_at = datetime.utcnow()
        # COUNTER and FINAL_OFFER keep status as "active"

        logger.info(
            "Negotiation %s round %d: %s | buyer=$%.2f seller=$%.2f | cumulative=%.1f%%",
            history.negotiation_id,
            negotiation_round.round_number,
            negotiation_round.action.value,
            negotiation_round.buyer_price,
            negotiation_round.seller_price,
            negotiation_round.cumulative_concession_pct * 100,
        )
        return updated

    def suggest_alternative_packages(
        self,
        history: NegotiationHistory,
        available_packages: list[dict],
    ) -> list[str]:
        """Suggest packages near the buyer's price point when negotiation stalls.

        Args:
            history: Current negotiation (with at least 1 round)
            available_packages: List of package dicts from storage

        Returns:
            List of package_ids that are within buyer's budget
        """
        if not history.rounds:
            return []

        buyer_budget = history.rounds[-1].buyer_price
        suggestions = []

        for pkg in available_packages:
            pkg_price = pkg.get("base_price", 0)
            if pkg.get("package_id") and 0 < pkg_price <= buyer_budget * 1.1:
                suggestions.append(pkg["package_id"])

        return suggestions[:5]  # Top 5 alternatives

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _generate_counter(
        self,
        history: NegotiationHistory,
        buyer_price: float,
    ) -> float:
        """Compute counter price via strategy-tuned gap-split.

        counter = buyer_price + gap * (1 - buyer_share)

        Then clamp the concession to per_round_cap and total_cap,
        and respect floor_price.
        """
        limits = history.limits

        # Current seller position (last counter, or base_price if first round)
        if history.rounds:
            last_seller_price = history.rounds[-1].seller_price
        else:
            last_seller_price = history.base_price

        # Gap between buyer offer and seller's last position
        gap = last_seller_price - buyer_price
        if gap <= 0:
            return buyer_price  # Buyer meets seller

        # Gap-split: seller concedes (buyer_share) of the gap
        seller_concession = gap * limits.gap_split_buyer_share
        counter_price = last_seller_price - seller_concession

        # Clamp per-round concession
        max_per_round_drop = history.base_price * limits.per_round_concession_cap
        if last_seller_price - counter_price > max_per_round_drop:
            counter_price = last_seller_price - max_per_round_drop

        # Clamp cumulative concession
        cumulative = self._cumulative_concession(history)
        _ = history.base_price * (limits.total_concession_cap - cumulative)
        if history.base_price - counter_price > history.base_price * limits.total_concession_cap:
            counter_price = history.base_price * (1 - limits.total_concession_cap)

        # Never go below floor
        counter_price = max(counter_price, history.floor_price)

        return round(counter_price, 2)

    def _cumulative_concession(self, history: NegotiationHistory) -> float:
        """Get the cumulative concession percentage so far."""
        if not history.rounds:
            return 0.0
        return history.rounds[-1].cumulative_concession_pct
