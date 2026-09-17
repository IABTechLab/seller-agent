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

KNOWN REMAINING GAP, deliberately not closed here: the engine's
``NegotiationRound.rationale`` strings state the floor and the strategy in
prose -- e.g. *"Countering at the floor price $28.00 CPM ... (round 1/5)"* and
*"Counter at $30.00 CPM (collaborative strategy, round 1/5)"*. Those strings
are built in ``engines/negotiation_engine.py`` and reach the buyer through
``counter_terms["reason"]``, so the floor is still derivable in prose on the
counter path. The field-name sweep below cannot catch that, and neither can a
value assertion without changing engine output. Fixing it means changing how
the engine phrases its rationales, which is a separate change on a separate
surface -- recorded here rather than half-built.
"""

import os
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


def _chat_text(action_wanted, buyer_price):
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
        base_price=BASE_PRICE,
        floor_price=FLOOR_PRICE,
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
        """The rewritten REJECT walk-away, swept by name.

        Only the REJECT branch is swept as prose. The other three branches
        interpolate ``round_result.rationale``, and the engine phrases its
        rationales with the guardrails in them -- "Counter at $X CPM
        (collaborative strategy, round 1/5)" names the strategy and states the
        round budget, and the below-floor rationale states the floor value.
        Those strings are built in ``engines/negotiation_engine.py``, not on
        either path fixed here, so sweeping them would be asserting on another
        surface's defect. See the module docstring's known-gap note.
        """
        text = _chat_text(NegotiationAction.REJECT, buyer_price=0.0).lower()
        for field in sorted(FORBIDDEN_FIELDS):
            assert field not in text, f"the chat walk-away names the guardrail {field!r}: {text!r}"

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
