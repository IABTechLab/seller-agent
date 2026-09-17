# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""A negotiated price must actually book.

The defect: ``deal_service.book_deal`` honored an accepted negotiation by
calling ``storage.get_negotiation(request.quote_id)``. Negotiations are keyed
by the id the opening message led with, so that lookup only ever hit for a
QUOTE-LED negotiation. A proposal-led buyer -- which is what ours is -- has
its negotiation stored under a ``prop-`` id, so the lookup missed every time:
the booked deal carried the seller's standard price while the log said
"Negotiation succeeded".

Three things are pinned here:

1. ``NegotiationHistory.quote_id`` -- the quote id arrives on
   ``NegotiationMessage`` and used to be dropped, so nothing could correlate
   a negotiation to the quote a buyer books.
2. The quote index -- an accepted negotiation writes
   ``negotiation_by_quote:{quote_id}`` naming the key its record lives under,
   and booking resolves through it whichever id the buyer led with. A
   negotiation that concluded accepted but carries no price refuses to book
   rather than silently falling back to the quoted price.
3. Key resolution (fault A2) -- the storage key is resolved against the store
   rather than read off whichever ids a given message happened to carry, so a
   continuation leading with a different id continues the SAME negotiation
   instead of silently restarting it.
"""

import os
import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Settings require an Anthropic key even for key-less unit runs; same idiom
# as test_negotiation_cold_start.py (no LLM call is ever made here).
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

import httpx  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402
from ad_seller.services import deal_service, negotiation_service  # noqa: E402

pytestmark = pytest.mark.asyncio

QUOTE_ID = "qt-corr123456"
PROPOSAL_ID = "prop-corr01"
PRODUCT_ID = "ctv-premium-sports"
QUOTED_CPM = 28.26


# =============================================================================
# Helpers
# =============================================================================


def _available_quote(quote_id=QUOTE_ID, product_id=PRODUCT_ID, final_cpm=QUOTED_CPM):
    return {
        "quote_id": quote_id,
        "status": "available",
        "deal_type": "PD",
        "product": {
            "product_id": product_id,
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.0,
            "tier_discount_pct": 15.0,
            "volume_discount_pct": 5.0,
            "final_cpm": final_cpm,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base price: $35.00 CPM | Advertiser tier: -15%",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "advertiser",
        "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


def _stored_product(product_id=PRODUCT_ID):
    return {"product_id": product_id, "base_cpm": 35.0, "floor_cpm": 20.0}


def _stored_proposal(proposal_id=PROPOSAL_ID, product_id=PRODUCT_ID):
    return {
        "proposal_id": proposal_id,
        "product_id": product_id,
        "deal_type": "preferred_deal",
        "price": 24.0,
        "impressions": 5_000_000,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "buyer_id": "buyer-1",
    }


def _accepted_negotiation(
    proposal_id=PROPOSAL_ID,
    quote_id=QUOTE_ID,
    product_id=PRODUCT_ID,
    agreed=24.0,
    rounds=None,
):
    """A negotiation record that concluded ``accepted``.

    ``rounds=[]`` models the corrupt state step 3 guards: accepted with no
    agreed price anywhere on the record.
    """
    if rounds is None:
        rounds = [
            {
                "round_number": 1,
                "buyer_price": 22.0,
                "seller_price": 26.0,
                "action": "counter",
                "rationale": "Counter at $26.00",
                "timestamp": datetime.utcnow().isoformat(),
            },
            {
                "round_number": 2,
                "buyer_price": agreed,
                "seller_price": agreed,
                "action": "accept",
                "rationale": f"Buyer accepted at ${agreed:.2f} CPM.",
                "timestamp": datetime.utcnow().isoformat(),
            },
        ]
    return {
        "negotiation_id": "neg-agreed01",
        "proposal_id": proposal_id,
        "quote_id": quote_id,
        "product_id": product_id,
        "buyer_tier": "advertiser",
        "strategy": "premium",
        "limits": {
            "max_rounds": 6,
            "per_round_concession_cap": 0.06,
            "total_concession_cap": 0.20,
            "gap_split_buyer_share": 0.65,
        },
        "base_price": 29.75,
        "floor_price": 20.0,
        "rounds": rounds,
        "status": "accepted",
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": datetime.utcnow().isoformat(),
        "package_id": None,
    }


def _buyer_context(buyer_tier="advertiser"):
    from ad_seller.interfaces.api.deps import _build_buyer_context

    return _build_buyer_context(
        buyer_tier=buyer_tier, agency_id="agency-test-1", advertiser_id="adv-test-1"
    )


def _booking_request(quote_id=QUOTE_ID):
    request = MagicMock()
    request.quote_id = quote_id
    request.audience_plan = None
    return request


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.delete = AsyncMock(side_effect=lambda k: store.pop(k, None))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage.get_proposal = AsyncMock(side_effect=lambda pid: store.get(f"proposal:{pid}"))
    storage.set_proposal = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"proposal:{pid}", data)
    )
    storage.get_product = AsyncMock(side_effect=lambda pid: store.get(f"product:{pid}"))
    storage.set_product = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"product:{pid}", data)
    )
    storage.get_negotiation = AsyncMock(side_effect=lambda pid: store.get(f"negotiation:{pid}"))
    storage.set_negotiation = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"negotiation:{pid}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def client():
    """An authenticated ADVERTISER-tier buyer.

    ``BuyerContext.effective_tier`` needs agency_id AND advertiser_id to
    reach ADVERTISER (progressive revelation), which is what the fixture
    quote's ``buyer_tier`` requires of a caller booking it.
    """
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(
        identity=BuyerIdentity(
            seat_id="seat-test-1", agency_id="agency-test-1", advertiser_id="adv-test-1"
        )
    )
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _seed_negotiable(mock_storage):
    """Quote + product + proposal, the state a proposal-led buyer negotiates from."""
    mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
    mock_storage._store[f"product:{PRODUCT_ID}"] = _stored_product()
    mock_storage._store[f"proposal:{PROPOSAL_ID}"] = _stored_proposal()


# =============================================================================
# Step 1: the quote id arrives on the wire and must be retained
# =============================================================================


class TestQuoteIdRetained:
    async def test_open_records_the_quote_id_from_the_message(self, mock_storage):
        """A negotiation opened for a quote persists that quote id."""
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                PROPOSAL_ID,
                buyer_price=24.0,
                buyer_context=_buyer_context(),
                quote_id=QUOTE_ID,
            )
            status = await negotiation_service.get_negotiation_status(PROPOSAL_ID)

        assert mock_storage._store[f"negotiation:{PROPOSAL_ID}"]["quote_id"] == QUOTE_ID
        # Readable back out, not merely written.
        assert status["quote_id"] == QUOTE_ID

    async def test_quote_id_survives_open_counter_accept(self, mock_storage):
        """The id survives a full open -> counter -> accept cycle."""
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                PROPOSAL_ID, buyer_price=22.0, buyer_context=_buyer_context(), quote_id=QUOTE_ID
            )
            # A later round need not repeat the quote id.
            await negotiation_service.counter_proposal(
                PROPOSAL_ID, buyer_price=23.5, buyer_context=_buyer_context()
            )
            status = await negotiation_service.apply_terminal_action(PROPOSAL_ID, "accept")

        assert status["quote_id"] == QUOTE_ID
        assert status["status"] == "accepted"

    async def test_proposal_only_negotiation_stores_none(self, mock_storage):
        """A pure proposal-led negotiation still works, with quote_id None."""
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            result = await negotiation_service.counter_proposal(
                PROPOSAL_ID, buyer_price=24.0, buyer_context=_buyer_context()
            )

        assert result["round_number"] == 1
        assert mock_storage._store[f"negotiation:{PROPOSAL_ID}"]["quote_id"] is None

    async def test_quote_id_is_not_overwritten_by_a_later_round(self, mock_storage):
        """A later round naming a DIFFERENT quote must not silently move which
        quote the agreed price applies to."""
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                PROPOSAL_ID, buyer_price=22.0, buyer_context=_buyer_context(), quote_id=QUOTE_ID
            )
            await negotiation_service.counter_proposal(
                PROPOSAL_ID,
                buyer_price=23.0,
                buyer_context=_buyer_context(),
                quote_id="qt-someotherquote",
            )

        assert mock_storage._store[f"negotiation:{PROPOSAL_ID}"]["quote_id"] == QUOTE_ID


# =============================================================================
# Step 2: an accepted negotiation is findable from the quote it concerns
# =============================================================================


class TestQuoteIndex:
    async def test_accept_writes_the_quote_pointer(self, mock_storage):
        """Accepting indexes the negotiation by its quote, pointing at the key
        the record is actually stored under."""
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                PROPOSAL_ID, buyer_price=22.0, buyer_context=_buyer_context(), quote_id=QUOTE_ID
            )
            await negotiation_service.apply_terminal_action(PROPOSAL_ID, "accept")

        assert mock_storage._store[f"negotiation_by_quote:{QUOTE_ID}"] == PROPOSAL_ID

    async def test_active_negotiation_writes_no_pointer(self, mock_storage):
        """Only an ACCEPTED negotiation changes a booking price, so only an
        accepted one appears in the index."""
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            await negotiation_service.counter_proposal(
                PROPOSAL_ID, buyer_price=22.0, buyer_context=_buyer_context(), quote_id=QUOTE_ID
            )

        assert mock_storage._store[f"negotiation:{PROPOSAL_ID}"]["status"] == "active"
        assert f"negotiation_by_quote:{QUOTE_ID}" not in mock_storage._store

    async def test_booking_resolves_a_proposal_led_negotiation(self, mock_storage):
        """THE bug, at the service boundary: the negotiation is stored under a
        ``prop-`` id, so booking can only find it through the index."""
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
        mock_storage._store[f"negotiation:{PROPOSAL_ID}"] = _accepted_negotiation(agreed=24.0)
        mock_storage._store[f"negotiation_by_quote:{QUOTE_ID}"] = PROPOSAL_ID

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.book_deal(_booking_request())

        assert deal["pricing"]["final_cpm"] == 24.0
        assert deal["openrtb_params"]["bidfloor"] == 24.0
        assert "Negotiated to $24.00 CPM" in deal["pricing"]["rationale"]

    async def test_booking_ignores_a_pointer_to_an_active_negotiation(self, mock_storage):
        """A resolvable but still-active negotiation must not change the price."""
        neg = _accepted_negotiation()
        neg["status"] = "active"
        neg["rounds"] = neg["rounds"][:1]
        neg["completed_at"] = None
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
        mock_storage._store[f"negotiation:{PROPOSAL_ID}"] = neg
        mock_storage._store[f"negotiation_by_quote:{QUOTE_ID}"] = PROPOSAL_ID

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.book_deal(_booking_request())

        assert deal["pricing"]["final_cpm"] == QUOTED_CPM
        assert "Negotiated to" not in deal["pricing"]["rationale"]

    async def test_quote_led_negotiation_still_honored_without_a_pointer(self, mock_storage):
        """A quote-led negotiation is stored under the quote id itself. That
        path worked before the index existed and must keep working, including
        for records written before it."""
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
        mock_storage._store[f"negotiation:{QUOTE_ID}"] = _accepted_negotiation(
            proposal_id=QUOTE_ID, agreed=25.5
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal = await deal_service.book_deal(_booking_request())

        assert deal["pricing"]["final_cpm"] == 25.5

    async def test_negotiated_price_books_end_to_end(self, client, mock_storage):
        """The end-to-end case the defect broke: negotiate a price down over
        the wire, accept, then book that quote -- the deal must carry the
        agreed price and say so in its rationale."""
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with client as c:
                # Proposal-led, exactly what our buyer sends: proposal_id as
                # the negotiation key, quote_id naming the quote at stake.
                counter = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-corr-counter",
                        "action": "counter",
                        "proposal_id": PROPOSAL_ID,
                        "quote_id": QUOTE_ID,
                        "buyer_price": {"amount_micros": 22_000_000, "currency": "USD"},
                    },
                )
                assert counter.status_code == 200, counter.text

                accept = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-corr-accept",
                        "action": "accept",
                        "proposal_id": PROPOSAL_ID,
                        "quote_id": QUOTE_ID,
                    },
                )
                assert accept.status_code == 200, accept.text
                assert accept.json()["status"] == "accepted"

                booked = await c.post(
                    "/api/v1/deals",
                    json={
                        "idempotency_key": "idem-corr-book",
                        "quote_id": QUOTE_ID,
                        "buyer_identity": {
                            "seat_id": "seat-test-1",
                            "agency_id": "agency-test-1",
                            "advertiser_id": "adv-test-1",
                        },
                    },
                )

        assert booked.status_code == 200, booked.text
        stored_negotiation = mock_storage._store[f"negotiation:{PROPOSAL_ID}"]
        agreed = stored_negotiation["rounds"][-1]["seller_price"]
        # The agreed price must actually differ from the quoted one, or this
        # test could pass without the fix doing anything.
        assert agreed != QUOTED_CPM

        pricing = booked.json()["deal"]["pricing"]
        assert pricing["final_cpm"]["amount_micros"] == round(agreed * 1_000_000)
        assert f"Negotiated to ${agreed:.2f} CPM" in pricing["rationale"]

    async def test_quote_with_no_negotiation_books_at_the_quoted_price(self, client, mock_storage):
        """The negative case: no negotiation, no change. A quote books at the
        price it was quoted at."""
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            async with client as c:
                booked = await c.post(
                    "/api/v1/deals",
                    json={
                        "idempotency_key": "idem-corr-plain",
                        "quote_id": QUOTE_ID,
                        "buyer_identity": {
                            "seat_id": "seat-test-1",
                            "agency_id": "agency-test-1",
                            "advertiser_id": "adv-test-1",
                        },
                    },
                )

        assert booked.status_code == 200, booked.text
        pricing = booked.json()["deal"]["pricing"]
        assert pricing["final_cpm"]["amount_micros"] == round(QUOTED_CPM * 1_000_000)
        assert "Negotiated to" not in pricing["rationale"]


# =============================================================================
# Step 2b (fault A2): the storage key is resolved, not read off the message
# =============================================================================


class TestNegotiationKeyResolution:
    async def test_continuation_with_a_different_id_continues_the_same_negotiation(
        self, client, mock_storage
    ):
        """Round one opened quote-led, so the record is keyed by the quote. A
        continuation leading with the proposal_id must resolve to the SAME
        negotiation.

        Reading the key off the message (``proposal_id or negotiation_id or
        quote_id``) picked the proposal id here, found no record under it,
        found the stored PROPOSAL under it, and opened a second negotiation at
        round one -- a silent restart that discards every concession already
        made.
        """
        _seed_negotiable(mock_storage)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with client as c:
                first = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-cont-1",
                        "action": "counter",
                        "quote_id": QUOTE_ID,
                        "buyer_price": {"amount_micros": 22_000_000, "currency": "USD"},
                    },
                )
                assert first.status_code == 200, first.text
                negotiation_id = first.json()["negotiation_id"]

                second = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-cont-2",
                        "action": "counter",
                        "proposal_id": PROPOSAL_ID,
                        "quote_id": QUOTE_ID,
                        "buyer_price": {"amount_micros": 23_000_000, "currency": "USD"},
                    },
                )

        assert second.status_code == 200, second.text
        body = second.json()
        assert body["negotiation_id"] == negotiation_id, "continuation started a new negotiation"
        assert body["round"]["round_number"] == 2, "continuation restarted the round numbering"
        # One record, under the id round one opened it with. No second record
        # minted under the proposal id the continuation led with.
        assert len(mock_storage._store[f"negotiation:{QUOTE_ID}"]["rounds"]) == 2
        assert f"negotiation:{PROPOSAL_ID}" not in mock_storage._store

    async def test_resolution_prefers_an_existing_record_over_message_order(self, mock_storage):
        """Unit view of the same thing: an id that resolves to a stored
        negotiation wins over an id that merely came first on the message."""
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
        mock_storage._store[f"negotiation:{QUOTE_ID}"] = _accepted_negotiation(
            proposal_id=QUOTE_ID, rounds=[]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resolved = await negotiation_service.resolve_negotiation_key(
                proposal_id=PROPOSAL_ID, negotiation_id="neg-agreed01", quote_id=QUOTE_ID
            )

        assert resolved == QUOTE_ID

    async def test_a_negotiation_id_alone_is_not_a_storage_key(self, mock_storage):
        """Known limitation, deliberately left to the structural fix: no
        negotiation is stored under its own ``neg-`` id, so a message carrying
        ONLY negotiation_id has nothing to resolve against.

        This is not a silent restart -- there is no proposal or quote under a
        ``neg-`` id either, so the round is refused with a 404 rather than
        quietly beginning again. Re-keying storage on negotiation_id is what
        makes this case resolvable.
        """
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
        mock_storage._store[f"negotiation:{QUOTE_ID}"] = _accepted_negotiation(
            proposal_id=QUOTE_ID, rounds=[]
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resolved = await negotiation_service.resolve_negotiation_key(
                negotiation_id="neg-agreed01"
            )
            assert resolved == "neg-agreed01"

            with pytest.raises(HTTPException) as exc:
                await negotiation_service.counter_proposal(
                    resolved, buyer_price=23.0, buyer_context=_buyer_context()
                )

        assert exc.value.status_code == 404

    async def test_unresolvable_ids_mint_under_the_first_supplied_id(self, mock_storage):
        """Nothing stored under any supplied id: the key is the first one
        supplied, the previous or-chain order, so opens are unchanged."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resolved = await negotiation_service.resolve_negotiation_key(
                proposal_id=PROPOSAL_ID, negotiation_id="neg-fresh01", quote_id=QUOTE_ID
            )

        assert resolved == PROPOSAL_ID

    async def test_no_ids_at_all_resolves_to_none(self, mock_storage):
        """The router's 400 path: no negotiation context supplied."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            assert await negotiation_service.resolve_negotiation_key() is None


# =============================================================================
# Step 3: an accepted negotiation with no price must not book at list price
# =============================================================================


class TestAcceptedWithoutPriceFailsLoudly:
    async def test_accepted_negotiation_with_no_rounds_refuses_to_book(self, mock_storage):
        """A negotiation that concluded accepted with no agreed price is a
        corrupt record. Booking it at the un-negotiated quoted price is the
        worst available outcome, so booking fails instead."""
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
        mock_storage._store[f"negotiation:{PROPOSAL_ID}"] = _accepted_negotiation(rounds=[])
        mock_storage._store[f"negotiation_by_quote:{QUOTE_ID}"] = PROPOSAL_ID

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await deal_service.book_deal(_booking_request())

        assert exc.value.status_code == 500
        assert exc.value.detail["error"] == "negotiation_price_unresolved"
        # And nothing was booked at the list price behind the buyer's back.
        assert not [k for k in mock_storage._store if k.startswith("deal:")]
        assert mock_storage._store[f"quote:{QUOTE_ID}"]["status"] == "available"

    async def test_accepted_negotiation_with_a_null_seller_price_refuses_to_book(
        self, mock_storage
    ):
        """Same guard, reached via a final round whose price is missing rather
        than via an empty rounds list."""
        neg = _accepted_negotiation(
            rounds=[
                {
                    "round_number": 1,
                    "buyer_price": 22.0,
                    "seller_price": None,
                    "action": "accept",
                    "rationale": "corrupt",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            ]
        )
        mock_storage._store[f"quote:{QUOTE_ID}"] = _available_quote()
        mock_storage._store[f"negotiation:{PROPOSAL_ID}"] = neg
        mock_storage._store[f"negotiation_by_quote:{QUOTE_ID}"] = PROPOSAL_ID

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc:
                await deal_service.book_deal(_booking_request())

        assert exc.value.status_code == 500
        assert exc.value.detail["error"] == "negotiation_price_unresolved"
