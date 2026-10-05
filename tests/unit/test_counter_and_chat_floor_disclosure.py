# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""The chat walk-away and the counter-offer payload must not disclose the
seller's internal negotiation guardrails.

Two leaks on two different surfaces, both on paths a buyer actually reaches:

1. ``interfaces/chat/main.py`` stated the floor outright in prose on a
   negotiation REJECT -- *"Our floor for this inventory is $X CPM"* --
   formatted from ``history.floor_price``. The seller told its counterparty
   its own floor, mid-negotiation.
2. ``flows/proposal_handling_flow.py`` put ``"floor_price":
   product.floor_cpm`` into ``state.counter_terms``, which is returned to the
   buyer on EVERY counter-offer. Unlike an obscure GET, this crossed the wire
   on the normal negotiation path.

A counterparty who knows the floor concedes nothing above it, so either leak
makes the seller's negotiating position structurally worthless. ``floor_price``,
``base_price``, ``strategy`` and ``NegotiationLimits`` (``max_rounds``) are the
four fields the shared ``Negotiation`` primitive excludes deliberately, per
``RECONCILIATION.md``: they are the seller's internal guardrails and must never
cross the wire.

Complements ``test_negotiation_status_disclosure.py`` (PR #95), which covers
``GET /proposals/{proposal_id}/negotiation``. That file pins one route's
response; this one pins the chat text and the counter payload, and adds the
by-field-name sweep below.

THE PROSE LEAK IS NOW CLOSED with two rationales (section 4 below). The
engine's ``NegotiationRound.rationale`` used to state the floor, the strategy
and the round budget in prose -- e.g. *"Countering at the floor price $28.00
CPM ... (round 1/5)"* -- and that sentence shipped to the buyer as
``counter_terms["reason"]``, in the chat COUNTER/FINAL_OFFER text, and on the
REST negotiation responses. The engine now emits BOTH an internal
``rationale`` (unchanged wording, kept for logs, stored history and audit)
and a deliberately constructed ``buyer_rationale`` that never states the
seller's price in prose (the structured ``seller_price`` carries the number
unlabeled), never says floor/minimum/strategy, and never discloses the round
budget or the concession caps. Every outbound surface sends only
``buyer_rationale``, so a future engine string cannot leak: the buyer-facing
sentence is constructed, not filtered.
"""

import os
import re
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_negotiation_lowball_counter.py (no LLM call is ever made here).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

# Stub execution_activation_flow (cancel-scope leak on ad-server
# connection failure, unresolved -- issue #60 part 2).
_broken_flows = [
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from ad_seller.engines.negotiation_engine import NegotiationEngine  # noqa: E402
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine  # noqa: E402
from ad_seller.engines.yield_optimizer import YieldOptimizer  # noqa: E402
from ad_seller.interfaces.chat.main import ChatInterface  # noqa: E402
from ad_seller.models.negotiation import NegotiationAction  # noqa: E402
from ad_seller.models.pricing_tiers import TieredPricingConfig  # noqa: E402

# The four fields the shared Negotiation primitive excludes, by NAME. Asserting
# on names rather than on today's two leak sites is the point: a per-site test
# catches the two leaks we know about, whereas this catches the next guardrail
# someone adds to an outbound payload.
FORBIDDEN_FIELDS = {"floor_price", "base_price", "strategy", "max_rounds"}

# Distinctive values, so a leaked number is unmistakable in the payload and
# cannot be confused with an incidental price.
BASE_PRICE = 41.37
FLOOR_PRICE = 33.11


# =============================================================================
# Helpers
# =============================================================================


def _all_keys(payload) -> set[str]:
    """Every mapping key anywhere in a nested payload, at any depth.

    Recurses so a guardrail nested under ``evaluation`` or inside a
    ``rounds[]`` entry is caught as readily as a top-level key.
    """
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= _all_keys(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found |= _all_keys(item)
    return found


def _assert_no_guardrail_fields(payload, surface: str) -> None:
    """Fail if any of the four guardrails appears as a field name."""
    leaked = _all_keys(payload) & FORBIDDEN_FIELDS
    assert not leaked, (
        f"{surface} carries the seller's internal negotiation guardrail(s) "
        f"{sorted(leaked)} by field name. floor_price, base_price, strategy "
        f"and max_rounds must never cross the wire. Payload: {payload!r}"
    )


def _make_engine() -> NegotiationEngine:
    config = TieredPricingConfig(seller_organization_id="test-seller")
    return NegotiationEngine(PricingRulesEngine(config=config), MagicMock(spec=YieldOptimizer))


def _make_product(product_id="ctv-premium-sports", base_cpm=BASE_PRICE, floor_cpm=FLOOR_PRICE):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id=product_id,
        name="Premium CTV - Sports",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=base_cpm,
        floor_cpm=floor_cpm,
        minimum_impressions=100000,
    )


def _make_buyer_context(buyer_tier="agency", agency_id="agency-1"):
    from ad_seller.interfaces.api.deps import _build_buyer_context

    return _build_buyer_context(buyer_tier=buyer_tier, agency_id=agency_id)


def _proposal_data(price, product_id="ctv-premium-sports"):
    return {
        "product_id": product_id,
        "deal_type": "preferred_deal",
        "price": price,
        "impressions": 1_000_000,
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    }


def _run_flow(price):
    """Run ProposalHandlingFlow to a counter with the review crew stubbed.

    The crew is made to fail so the deterministic fallback runs; no LLM call
    is made. Mirrors the harness in test_negotiation_lowball_counter.py.
    """
    import asyncio

    from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow

    crew = MagicMock()
    crew.kickoff_async = AsyncMock(side_effect=RuntimeError("no crew in tests"))

    async def _go():
        with (
            patch(
                "ad_seller.flows.proposal_handling_flow.create_proposal_review_crew",
                return_value=crew,
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.emit_event",
                new_callable=AsyncMock,
            ),
        ):
            flow = ProposalHandlingFlow()
            return await flow.handle_proposal_async(
                proposal_id="prop-floor-disclosure",
                proposal_data=_proposal_data(price=price),
                buyer_context=_make_buyer_context(),
                products={"ctv-premium-sports": _make_product()},
            )

    return asyncio.run(_go())


def _chat_text(action_wanted, buyer_price, base_price=BASE_PRICE, floor_price=FLOOR_PRICE):
    """Produce the chat text for a real engine round of the wanted action.

    Drives the actual engine rather than a hand-built round, so the text is
    formatted from a history that genuinely carries the guardrails.
    """
    engine = _make_engine()
    buyer_context = _make_buyer_context()
    history = engine.start_negotiation(
        proposal_id="chat-1",
        product_id="ctv-premium-sports",
        buyer_context=buyer_context,
        base_price=base_price,
        floor_price=floor_price,
    )
    for _ in range(history.limits.max_rounds + 2):
        round_result = engine.evaluate_buyer_offer(history, buyer_price, buyer_context)
        history_next = engine.record_round(history, round_result)
        if round_result.action == action_wanted:
            return ChatInterface._format_negotiation_response(round_result, history_next)
        history = history_next
    pytest.fail(f"engine never produced a {action_wanted} round for buyer_price={buyer_price}")


# =============================================================================
# (1) The chat walk-away must not state the floor
# =============================================================================


class TestChatRejectDoesNotDiscloseFloor:
    def test_reject_text_omits_the_floor_figure(self):
        """The REJECT walk-away must not contain the floor value.

        This is the leak: the branch used to render "Our floor for this
        inventory is $33.11 CPM" straight to the counterparty.
        """
        text = _chat_text(NegotiationAction.REJECT, buyer_price=0.0)
        assert f"{FLOOR_PRICE:.2f}" not in text, (
            f"the chat REJECT walk-away discloses the seller's floor ({FLOOR_PRICE}): {text!r}"
        )
        assert f"{BASE_PRICE:.2f}" not in text, (
            f"the chat REJECT walk-away discloses the seller's base price ({BASE_PRICE}): {text!r}"
        )

    def test_reject_text_does_not_name_a_floor_at_all(self):
        """Not merely the number: the walk-away must not tell the buyer that
        a floor exists for this inventory, since "our floor is" invites the
        buyer to probe for it."""
        text = _chat_text(NegotiationAction.REJECT, buyer_price=0.0)
        assert "floor" not in text.lower(), (
            f"the chat REJECT walk-away still refers to the seller's floor: {text!r}"
        )

    def test_reject_text_is_still_a_usable_walk_away(self):
        """The fix must not leave an empty or contentless message -- the buyer
        still has to be told the negotiation is over and offered an out."""
        text = _chat_text(NegotiationAction.REJECT, buyer_price=0.0)
        assert text.strip()
        assert "?" in text, f"the walk-away offers the buyer no next step: {text!r}"

    def test_reject_text_omits_max_rounds(self):
        """The engine's max-rounds REJECT rationale reads "Maximum N rounds
        reached", which publishes the concession budget. It must not be
        interpolated into the buyer-facing walk-away."""
        engine = _make_engine()
        buyer_context = _make_buyer_context()
        history = engine.start_negotiation(
            proposal_id="chat-1",
            product_id="ctv-premium-sports",
            buyer_context=buyer_context,
            base_price=BASE_PRICE,
            floor_price=FLOOR_PRICE,
        )
        max_rounds = history.limits.max_rounds
        for _ in range(max_rounds + 1):
            round_result = engine.evaluate_buyer_offer(history, 1.0, buyer_context)
            history = engine.record_round(history, round_result)
            if round_result.action == NegotiationAction.REJECT:
                break

        assert round_result.action == NegotiationAction.REJECT
        text = ChatInterface._format_negotiation_response(round_result, history)
        assert f"Maximum {max_rounds}" not in text
        assert str(max_rounds) not in text, (
            f"the chat REJECT walk-away discloses the round budget ({max_rounds}): {text!r}"
        )


# =============================================================================
# (2) The counter payload must not carry the floor
# =============================================================================


class TestCounterTermsDoesNotCarryFloor:
    def test_counter_terms_has_no_floor_price_key(self):
        """counter_terms is returned to the buyer on every counter-offer and
        used to carry "floor_price": product.floor_cpm."""
        result = _run_flow(price=25.0)
        counter_terms = result["counter_terms"]
        assert counter_terms is not None, "expected a counter, got none"
        assert "floor_price" not in counter_terms, (
            f"counter_terms still hands the buyer the seller's floor: {counter_terms!r}"
        )

    def test_counter_terms_still_carries_what_the_buyer_needs(self):
        """Regression guard on the removal itself: the buyer reads
        proposed_price and negotiation_id out of counter_terms (the buyer
        agent's multi_seller orchestrator reads exactly those two), so
        dropping floor_price must not have disturbed them."""
        result = _run_flow(price=25.0)
        counter_terms = result["counter_terms"]
        assert counter_terms["proposed_price"] == FLOOR_PRICE
        assert counter_terms["negotiation_id"]
        assert counter_terms["action"] == "counter"


# =============================================================================
# (3) The by-field-name sweep -- the part that catches the NEXT leak
# =============================================================================


class TestNoGuardrailFieldNamesOnEitherPath:
    """Neither path may carry floor_price, base_price, strategy or max_rounds
    as a field name, at any nesting depth.

    Sections (1) and (2) pin the two leaks that were found. This section is
    the standing guard: it does not care where in the payload a guardrail
    appears, so a guardrail added to counter_terms, to the evaluation block,
    or to the chat negotiation envelope later fails here without anyone
    having to remember to write a new test for it.
    """

    def test_counter_terms_carries_no_guardrail_field(self):
        result = _run_flow(price=25.0)
        _assert_no_guardrail_fields(result["counter_terms"], "counter_terms")

    def test_full_counter_response_carries_no_guardrail_field(self):
        """The whole buyer-facing proposal response, not just counter_terms.

        The internal-only keys the service strips before answering
        (``_negotiation_history``, ``_flow_state_snapshot``) are removed here
        the same way, so this asserts on what actually reaches the buyer.
        """
        result = _run_flow(price=25.0)
        outbound = {k: v for k, v in result.items() if not k.startswith("_")}
        assert outbound.get("counter_terms") is not None, "expected a counter, got none"
        _assert_no_guardrail_fields(outbound, "the proposal counter response")

    def test_internal_history_is_not_part_of_the_response(self):
        """Proof the previous test is meaningful rather than vacuous: the
        history DOES hold all four guardrails, and is exactly what must stay
        behind the service boundary."""
        result = _run_flow(price=25.0)
        history = result.get("_negotiation_history")
        assert history is not None, "expected the flow to surface a history"
        # All four really are in the history (max_rounds nested under limits),
        # so the sweep above is asserting absence of something that exists.
        assert FORBIDDEN_FIELDS <= _all_keys(history)
        assert history["floor_price"] == FLOOR_PRICE
        assert all(
            not k.startswith("_") or k in ("_negotiation_history", "_flow_state_snapshot")
            for k in result
        )

    def test_chat_walk_away_names_no_guardrail_field(self):
        """Every chat branch's prose, swept by name.

        The COUNTER and FINAL_OFFER branches used to interpolate the engine's
        internal ``rationale``, which names the strategy, the round budget
        and (below floor) the floor -- so only REJECT could be swept when
        this file was first written. They now interpolate the deliberately
        constructed ``buyer_rationale``, so all branches are swept.
        """
        for action, price in (
            (NegotiationAction.REJECT, 0.0),
            (NegotiationAction.COUNTER, 25.0),
            (NegotiationAction.FINAL_OFFER, 25.0),
            (NegotiationAction.ACCEPT, BASE_PRICE + 1.0),
        ):
            text = _chat_text(action, buyer_price=price).lower()
            for field in sorted(FORBIDDEN_FIELDS):
                assert field not in text, (
                    f"the chat {action.value} text names the guardrail {field!r}: {text!r}"
                )

    def test_chat_negotiation_envelope_carries_no_guardrail_field(self):
        """The structured ``negotiation`` block the chat surface returns
        alongside its text."""
        engine = _make_engine()
        buyer_context = _make_buyer_context()
        history = engine.start_negotiation(
            proposal_id="chat-1",
            product_id="ctv-premium-sports",
            buyer_context=buyer_context,
            base_price=BASE_PRICE,
            floor_price=FLOOR_PRICE,
        )
        round_result = engine.evaluate_buyer_offer(history, 25.0, buyer_context)
        history = engine.record_round(history, round_result)

        # The envelope as interfaces/chat/main.py assembles it.
        envelope = {
            "text": ChatInterface._format_negotiation_response(round_result, history),
            "type": "negotiation",
            "negotiation": {
                "negotiation_id": history.negotiation_id,
                "round_number": round_result.round_number,
                "action": round_result.action.value,
                "buyer_price": round_result.buyer_price,
                "seller_price": round_result.seller_price,
                "status": history.status,
            },
        }
        _assert_no_guardrail_fields(envelope, "the chat negotiation envelope")


# =============================================================================
# (4) Two rationales: the buyer-facing one never carries a guardrail
# =============================================================================

# "(round 1/5)"-style round-budget disclosure, anywhere in a string.
ROUND_BUDGET_RE = re.compile(r"\b\d+\s*/\s*\d+\b")

# Words that label a guardrail in prose. "minimum" covers "the minimum viable
# price" (a floor synonym); "concession" covers the cap percentages.
FORBIDDEN_WORDS = ("floor", "minimum", "strategy", "concession")


# A second distinctive floor, deep enough that the concession-cap FINAL_OFFER
# and the gap-split COUNTER land ABOVE it (the agency tier discount pulls the
# effective base to ~$35.16, and the 15% collaborative cap bottoms out at
# ~$29.89): with this floor those prices are not the floor, so "the floor
# value is absent" is a real assertion rather than a coincidence.
DEEP_FLOOR_PRICE = 21.47


def _drive_engine_to(action_wanted, buyer_price, base_price=BASE_PRICE, floor_price=FLOOR_PRICE):
    """Drive the real engine until it emits the wanted action.

    Returns (round, history) with the round already recorded, so assertions
    run against genuine engine output, not hand-built rounds.
    """
    engine = _make_engine()
    buyer_context = _make_buyer_context()
    history = engine.start_negotiation(
        proposal_id="prop-two-rationales",
        product_id="ctv-premium-sports",
        buyer_context=buyer_context,
        base_price=base_price,
        floor_price=floor_price,
    )
    for _ in range(history.limits.max_rounds + 2):
        round_result = engine.evaluate_buyer_offer(history, buyer_price, buyer_context)
        history = engine.record_round(history, round_result)
        if round_result.action == action_wanted:
            return round_result, history
    pytest.fail(f"engine never produced {action_wanted} for buyer_price={buyer_price}")


def _assert_buyer_facing(text: str, surface: str) -> None:
    """The buyer-facing rationale contract, in one place.

    No floor value, no base-price value, no guardrail word, no round-budget
    pattern -- and the string is still a usable explanation, not empty.
    """
    low = text.lower()
    assert f"{FLOOR_PRICE:.2f}" not in text, f"{surface} states the floor value: {text!r}"
    assert f"{BASE_PRICE:.2f}" not in text, f"{surface} states the base price: {text!r}"
    for word in FORBIDDEN_WORDS:
        assert word not in low, f"{surface} says {word!r}: {text!r}"
    assert not ROUND_BUDGET_RE.search(text), f"{surface} discloses the round budget: {text!r}"
    assert text.strip(), f"{surface} is empty -- the buyer gets no explanation"


class TestEngineEmitsTwoRationales:
    """Engine level: for every action the engine can take, the buyer-facing
    rationale is clean while the internal one still says everything it said
    before (the internal assertions double as proof the clean checks are
    non-vacuous)."""

    def test_below_floor_counter(self):
        round_result, _ = _drive_engine_to(NegotiationAction.COUNTER, buyer_price=25.0)
        assert round_result.seller_price == FLOOR_PRICE  # countering AT the floor
        _assert_buyer_facing(round_result.buyer_rationale, "the below-floor COUNTER")
        # Internal keeps the full story: floor value, the word, the budget.
        assert f"{FLOOR_PRICE:.2f}" in round_result.rationale
        assert "floor" in round_result.rationale.lower()
        assert ROUND_BUDGET_RE.search(round_result.rationale)

    def test_below_floor_final_offer(self):
        round_result, _ = _drive_engine_to(NegotiationAction.FINAL_OFFER, buyer_price=25.0)
        assert round_result.seller_price == FLOOR_PRICE
        _assert_buyer_facing(round_result.buyer_rationale, "the below-floor FINAL_OFFER")
        assert f"{FLOOR_PRICE:.2f}" in round_result.rationale
        assert "floor" in round_result.rationale.lower()

    def test_concession_cap_final_offer(self):
        """FINAL_OFFER via the concession-cap path, where the final price sits
        ABOVE the floor -- so the floor value is a distinct secret and its
        absence from the buyer-facing string is a real assertion."""
        round_result, _ = _drive_engine_to(
            NegotiationAction.FINAL_OFFER, buyer_price=25.0, floor_price=DEEP_FLOOR_PRICE
        )
        assert round_result.seller_price > DEEP_FLOOR_PRICE
        _assert_buyer_facing(round_result.buyer_rationale, "the cap-path FINAL_OFFER")
        assert f"{DEEP_FLOOR_PRICE:.2f}" not in round_result.buyer_rationale, (
            f"the cap-path FINAL_OFFER states the floor value: {round_result.buyer_rationale!r}"
        )
        assert "%" not in round_result.buyer_rationale, (
            f"the cap-path FINAL_OFFER discloses a concession percentage: "
            f"{round_result.buyer_rationale!r}"
        )
        # Internal still records the cap.
        assert "concession" in round_result.rationale.lower()
        assert "%" in round_result.rationale

    def test_gap_split_counter(self):
        round_result, history = _drive_engine_to(
            NegotiationAction.COUNTER, buyer_price=25.0, floor_price=DEEP_FLOOR_PRICE
        )
        assert round_result.seller_price > DEEP_FLOOR_PRICE  # gap-split, not floor
        _assert_buyer_facing(round_result.buyer_rationale, "the gap-split COUNTER")
        assert f"{DEEP_FLOOR_PRICE:.2f}" not in round_result.buyer_rationale
        # Internal still names the strategy and the budget.
        assert history.strategy.value in round_result.rationale
        assert ROUND_BUDGET_RE.search(round_result.rationale)

    def test_accept(self):
        round_result, _ = _drive_engine_to(NegotiationAction.ACCEPT, buyer_price=BASE_PRICE + 1.0)
        _assert_buyer_facing(round_result.buyer_rationale, "the ACCEPT rationale")

    def test_reject_max_rounds(self):
        round_result, history = _drive_engine_to(NegotiationAction.REJECT, buyer_price=1.0)
        _assert_buyer_facing(round_result.buyer_rationale, "the max-rounds REJECT")
        max_rounds = history.limits.max_rounds
        assert str(max_rounds) not in round_result.buyer_rationale, (
            f"the max-rounds REJECT discloses the round budget ({max_rounds}): "
            f"{round_result.buyer_rationale!r}"
        )
        assert f"Maximum {max_rounds}" in round_result.rationale

    def test_reject_invalid_offer(self):
        round_result, _ = _drive_engine_to(NegotiationAction.REJECT, buyer_price=0.0)
        _assert_buyer_facing(round_result.buyer_rationale, "the invalid-offer REJECT")


class TestCounterReasonIsBuyerFacing:
    """Flow level: counter_terms["reason"] -- the widest surface, shipped on
    every counter-offer -- carries the buyer-facing rationale, while the
    stored history keeps the internal one untouched."""

    def test_below_floor_reason_carries_no_guardrail(self):
        result = _run_flow(price=25.0)
        reason = result["counter_terms"]["reason"]
        _assert_buyer_facing(reason, 'counter_terms["reason"]')

    def test_stored_history_keeps_the_internal_rationale(self):
        """The audit trail is unchanged: the persisted round still names the
        floor, the word and the budget, and also carries the buyer-facing
        string that actually went to the wire."""
        result = _run_flow(price=25.0)
        stored_round = result["_negotiation_history"]["rounds"][0]
        assert f"{FLOOR_PRICE:.2f}" in stored_round["rationale"]
        assert "floor" in stored_round["rationale"].lower()
        assert ROUND_BUDGET_RE.search(stored_round["rationale"])
        assert stored_round["buyer_rationale"] == result["counter_terms"]["reason"]


class TestChatProseIsBuyerFacing:
    """Chat level: the COUNTER and FINAL_OFFER texts no longer interpolate
    the internal rationale.

    The floor-value assertions use scenarios where the offered price sits
    ABOVE the floor: on a below-floor round the counter price IS the floor,
    so the number legitimately appears as the offer itself (unlabeled) and
    only the word-level checks apply there.
    """

    def test_counter_text_above_floor(self):
        text = _chat_text(NegotiationAction.COUNTER, buyer_price=34.0)
        assert f"{FLOOR_PRICE:.2f}" not in text, (
            f"the chat COUNTER discloses the floor ({FLOOR_PRICE}): {text!r}"
        )
        low = text.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in low, f"the chat COUNTER says {word!r}: {text!r}"
        assert not ROUND_BUDGET_RE.search(text)

    def test_final_offer_text_above_floor(self):
        text = _chat_text(
            NegotiationAction.FINAL_OFFER, buyer_price=25.0, floor_price=DEEP_FLOOR_PRICE
        )
        assert f"{DEEP_FLOOR_PRICE:.2f}" not in text, (
            f"the chat FINAL_OFFER discloses the floor ({DEEP_FLOOR_PRICE}): {text!r}"
        )
        low = text.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in low, f"the chat FINAL_OFFER says {word!r}: {text!r}"
        assert not ROUND_BUDGET_RE.search(text)

    def test_counter_text_below_floor_labels_nothing(self):
        """Below floor the counter price equals the floor, so the value is
        inherently visible as the offer -- what must not appear is any label
        telling the buyer that it IS the bottom."""
        text = _chat_text(NegotiationAction.COUNTER, buyer_price=25.0)
        low = text.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in low, f"the below-floor chat COUNTER says {word!r}: {text!r}"
        assert not ROUND_BUDGET_RE.search(text)


class TestRestNegotiationResponseIsBuyerFacing:
    """REST level: the counter-offer response dict (returned verbatim on the
    legacy route and mapped into the shared NegotiationRoundResponse on the
    canonical route) carries the buyer-facing rationale, and the terminal
    mapper never falls back to a stored internal rationale."""

    async def test_counter_proposal_response_rationale(self):
        from ad_seller.services import negotiation_service

        engine = _make_engine()
        buyer_context = _make_buyer_context()
        history = engine.start_negotiation(
            proposal_id="prop-rest-rationale",
            product_id="ctv-premium-sports",
            buyer_context=buyer_context,
            base_price=BASE_PRICE,
            floor_price=FLOOR_PRICE,
        )

        mock_storage = AsyncMock()
        mock_storage.get_negotiation.return_value = history.model_dump(mode="json")
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            response = await negotiation_service.counter_proposal(
                proposal_id="prop-rest-rationale",
                buyer_price=25.0,
                buyer_context=buyer_context,
            )

        _assert_buyer_facing(response["rationale"], "the REST counter response rationale")
        # The PERSISTED history still holds the full internal rationale.
        persisted = mock_storage.set_negotiation.call_args.args[1]
        assert f"{FLOOR_PRICE:.2f}" in persisted["rounds"][0]["rationale"]
        assert "floor" in persisted["rounds"][0]["rationale"].lower()

    def test_terminal_round_response_never_leaks_a_stored_internal_rationale(self):
        """A buyer 'reject' on an already-terminal negotiation answers off the
        LAST STORED round, which can be an engine walk-away whose internal
        rationale reads "Maximum N rounds reached". The mapper must send the
        stored buyer_rationale -- and fall back to empty, never to the
        internal one, for rounds persisted before this field existed."""
        from ad_seller.interfaces.api import contract_mappers as cm

        status_data = {
            "negotiation_id": "neg-terminal-1",
            "rounds": [
                {
                    "round_number": 5,
                    "buyer_price": 25.0,
                    "seller_price": FLOOR_PRICE,
                    "action": "reject",
                    "rationale": "Maximum 5 rounds reached. Negotiation concluded without agreement.",
                    # No buyer_rationale key: a round stored by an older build.
                }
            ],
        }
        response = cm.terminal_round_response(status_data, cm.NegotiationAction.REJECT)
        assert response.round.rationale == "", (
            f"the terminal mapper fell back to the internal rationale: {response.round.rationale!r}"
        )
