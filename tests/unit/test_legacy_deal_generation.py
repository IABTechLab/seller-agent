"""Legacy ``POST /deals`` builds the deal from the caller's accepted proposal.

The route took any ``proposal_id`` without authentication and minted a
deal from hardcoded demo terms ($15 PD on "display"), whether or not the
proposal existed, was accepted, or belonged to the caller. It now requires
an API key, loads the stored proposal (404 if missing), refuses with 409
unless the proposal is accepted/approved and was submitted by the same
key, and carries the proposal's real terms.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity

OWNER = BuyerIdentity(agency_id="agency-1")
OTHER = BuyerIdentity(agency_id="agency-2")


def _owner_key(identity=OWNER):
    return BuyerContext(
        identity=identity, is_authenticated=True, authentication_method="api_key"
    ).get_pricing_key()


def _proposal(status="accepted", **overrides):
    return {
        "proposal_id": "prop-1",
        "product_id": "ctv-premium",
        "deal_type": "programmatic_guaranteed",
        "price": 31.25,
        "impressions": 2_000_000,
        "start_date": "2027-03-01",
        "end_date": "2027-03-31",
        "buyer_id": "buyer-1",
        "status": status,
        "buyer_key": _owner_key(),
        "buyer_auth": "api_key",
        "buyer_key_id": "key-a",
        **overrides,
    }


@pytest.fixture
def storage():
    store = {}
    s = AsyncMock()
    s.get_proposal = AsyncMock(side_effect=lambda p: store.get(f"proposal:{p}"))
    s.set_proposal = AsyncMock(side_effect=lambda p, d: store.__setitem__(f"proposal:{p}", d))
    s.get_negotiation = AsyncMock(side_effect=lambda p: store.get(f"negotiation:{p}"))
    s.get = AsyncMock(side_effect=lambda k: store.get(k))
    s.set = AsyncMock(side_effect=lambda k, v, *a, **kw: store.__setitem__(k, v))
    s._store = store
    return s


async def _post(storage, identity=OWNER, proposal_id="prop-1", key_id="key-a"):
    record = MagicMock(identity=identity, key_id=key_id) if identity else None
    app.dependency_overrides[_get_optional_api_key_record] = lambda: record
    try:
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch(
                "ad_seller.flows.deal_generation_flow.DealGenerationFlow._od_register_deal",
                new=AsyncMock(),
            ),
            patch("ad_seller.flows.deal_generation_flow.emit_event", new=AsyncMock()),
        ):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                return await c.post("/deals", json={"proposal_id": proposal_id})
    finally:
        app.dependency_overrides.clear()


async def test_requires_an_api_key(storage):
    storage._store["proposal:prop-1"] = _proposal()
    resp = await _post(storage, identity=None)
    assert resp.status_code == 401


async def test_unknown_proposal_is_404(storage):
    resp = await _post(storage, proposal_id="prop-missing")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "proposal_not_found"


@pytest.mark.parametrize("status", ["countered", "counter_pending", "pending_approval", "rejected"])
async def test_unaccepted_proposal_is_409(storage, status):
    storage._store["proposal:prop-1"] = _proposal(status=status)
    resp = await _post(storage)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_accepted"


async def test_other_buyers_proposal_is_409(storage):
    storage._store["proposal:prop-1"] = _proposal()
    resp = await _post(storage, identity=OTHER, key_id="key-b")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_bookable"


async def test_keyless_proposal_has_no_owner(storage):
    storage._store["proposal:prop-1"] = _proposal(
        buyer_key="public", buyer_auth=None, buyer_key_id=None
    )
    resp = await _post(storage)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_bookable"


@pytest.mark.parametrize("identity", [OWNER, BuyerIdentity()])
async def test_proposal_stored_before_ownership_was_recorded_is_owner_unknown(storage, identity):
    legacy = _proposal()
    for field in ("buyer_key", "buyer_auth", "buyer_key_id"):
        del legacy[field]
    storage._store["proposal:prop-1"] = legacy

    resp = await _post(storage, identity=identity)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "proposal_owner_unknown"
    assert "Resubmit" in detail["message"]
    assert "deal" not in storage._store["proposal:prop-1"]


async def test_key_sharing_the_owners_advertiser_cannot_book(storage):
    key_a = BuyerIdentity(agency_id="ag-1", advertiser_id="adv-1")
    key_b = BuyerIdentity(agency_id="ag-2", advertiser_id="adv-1")
    storage._store["proposal:prop-1"] = _proposal(buyer_key=_owner_key(key_a))
    resp = await _post(storage, identity=key_b, key_id="key-b")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_bookable"


async def test_identityless_keys_cannot_book_each_others_proposals(storage):
    storage._store["proposal:prop-1"] = _proposal(
        buyer_key=_owner_key(BuyerIdentity()), buyer_key_id="key-c"
    )
    resp = await _post(storage, identity=BuyerIdentity(), key_id="key-d")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_bookable"

    resp = await _post(storage, identity=BuyerIdentity(), key_id="key-c")
    assert resp.status_code == 200, resp.text


async def test_record_without_key_id_falls_back_to_the_pricing_key(storage):
    legacy = _proposal()
    del legacy["buyer_key_id"]
    storage._store["proposal:prop-1"] = legacy

    other = await _post(storage, identity=OTHER, key_id="key-b")
    assert other.status_code == 409
    assert other.json()["detail"]["error"] == "proposal_not_bookable"

    resp = await _post(storage, key_id="key-new")
    assert resp.status_code == 200, resp.text


async def test_submit_proposal_route_records_the_submitting_key_id(storage):
    from ad_seller.services import negotiation_service

    submit = AsyncMock(
        return_value={"proposal_id": "prop-1", "recommendation": "accept", "status": "accepted"}
    )
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(
        identity=OWNER, key_id="key-a"
    )
    try:
        with (
            patch.object(negotiation_service, "submit_proposal", new=submit),
            patch(
                "ad_seller.interfaces.api.deps.get_product_catalog",
                new=AsyncMock(return_value={"products": {}}),
            ),
        ):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/proposals",
                    json={
                        "product_id": "ctv-premium",
                        "deal_type": "preferred_deal",
                        "price": 12.0,
                        "impressions": 1_000_000,
                        "start_date": "2027-03-01",
                        "end_date": "2027-03-31",
                    },
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert submit.await_args.kwargs["api_key_id"] == "key-a"


@pytest.mark.parametrize("status", ["accepted", "approved"])
async def test_deal_carries_the_proposal_terms(storage, status):
    storage._store["proposal:prop-1"] = _proposal(status=status)

    resp = await _post(storage)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deal_type"] == "programmaticguaranteed"
    assert body["price"] == 31.25
    assert body["openrtb_params"]["bidfloor"] == 31.25
    assert body["openrtb_params"]["ext"]["impressions"] == 2_000_000


async def test_accepted_negotiation_price_is_used(storage):
    storage._store["proposal:prop-1"] = _proposal(status="counter_pending")
    storage._store["negotiation:prop-1"] = {
        "status": "accepted",
        "rounds": [{"buyer_price": 30.0, "seller_price": 32.5}],
    }

    resp = await _post(storage)

    assert resp.status_code == 200, resp.text
    assert resp.json()["price"] == 32.5


async def _gated_proposal(storage, decision=None):
    """Store a proposal behind a proposal_decision approval, optionally decided."""
    from ad_seller.events.approval import ApprovalGate

    gate = ApprovalGate(storage)
    approval = await gate.request_approval(
        flow_id="flow-1",
        flow_type="proposal_handling",
        gate_name="proposal_decision",
        context={"proposal_id": "prop-1"},
        flow_state_snapshot={},
        proposal_id="prop-1",
    )
    status = "pending_approval"
    if decision is not None:
        with patch("ad_seller.events.bus.get_event_bus", new=AsyncMock()):
            await gate.submit_decision(approval.approval_id, decision, decided_by="op")
        status = {"approve": "approved", "reject": "rejected"}.get(decision, status)
    storage._store["proposal:prop-1"] = _proposal(status=status, approval_id=approval.approval_id)
    storage._store["negotiation:prop-1"] = {
        "status": "accepted",
        "rounds": [{"buyer_price": 11.0, "seller_price": 11.98}],
    }


@pytest.mark.parametrize("decision", [None, "reject"])
async def test_unapproved_proposal_stays_unbookable_after_accepted_negotiation(storage, decision):
    await _gated_proposal(storage, decision)
    resp = await _post(storage)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_accepted"
    assert "deal" not in storage._store["proposal:prop-1"]


@pytest.mark.parametrize("decision", ["approve", "counter"])
async def test_approved_or_countered_proposal_books_at_negotiated_price(storage, decision):
    await _gated_proposal(storage, decision)
    resp = await _post(storage)
    assert resp.status_code == 200, resp.text
    assert resp.json()["price"] == 11.98


async def test_pending_proposal_without_approval_id_is_409(storage):
    storage._store["proposal:prop-1"] = _proposal(status="pending_approval")
    storage._store["negotiation:prop-1"] = {
        "status": "accepted",
        "rounds": [{"buyer_price": 11.0, "seller_price": 11.98}],
    }
    resp = await _post(storage)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_accepted"


async def test_submit_proposal_records_the_approval_id(storage):
    from types import SimpleNamespace

    from ad_seller.services import negotiation_service

    flow = MagicMock()
    flow.handle_proposal_async = AsyncMock(
        return_value={
            "proposal_id": "ignored",
            "recommendation": "accept",
            "status": "pending_approval",
            "pending_approval": True,
            "flow_id": "flow-1",
            "_flow_state_snapshot": {},
        }
    )
    verification = SimpleNamespace(pricing_verified=True, reason=None)
    quote_history = MagicMock(verify_pricing=AsyncMock(return_value=verification))
    request = SimpleNamespace(
        product_id="ctv-premium",
        deal_type="preferred_deal",
        price=12.0,
        impressions=1_000_000,
        start_date="2027-03-01",
        end_date="2027-03-31",
        buyer_id="buyer-1",
    )
    context = BuyerContext(identity=OWNER, is_authenticated=True, authentication_method="api_key")
    with (
        patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow),
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.storage.quote_history.QuoteHistoryStore", return_value=quote_history),
    ):
        result = await negotiation_service.submit_proposal(
            request, context, {"products": {}}, api_key_id="key-a"
        )

    stored = storage._store[f"proposal:{result['proposal_id']}"]
    assert stored["approval_id"] == result["approval_id"]
    assert stored["buyer_key_id"] == "key-a"


async def test_repeat_call_returns_the_same_deal(storage):
    storage._store["proposal:prop-1"] = _proposal()
    first = (await _post(storage)).json()
    second = (await _post(storage)).json()
    assert second["deal_id"] == first["deal_id"]


async def test_concurrent_calls_mint_one_deal(storage):
    import asyncio

    storage._store["proposal:prop-1"] = _proposal()
    read, write = storage.get_proposal.side_effect, storage.set_proposal.side_effect

    async def slow_get(p):
        await asyncio.sleep(0)
        return read(p)

    async def slow_set(p, d):
        await asyncio.sleep(0)
        write(p, d)

    storage.get_proposal = AsyncMock(side_effect=slow_get)
    storage.set_proposal = AsyncMock(side_effect=slow_set)
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(
        identity=OWNER, key_id="key-a"
    )
    try:
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch(
                "ad_seller.flows.deal_generation_flow.DealGenerationFlow._od_register_deal",
                new=AsyncMock(),
            ),
            patch("ad_seller.flows.deal_generation_flow.emit_event", new=AsyncMock()),
        ):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                responses = await asyncio.gather(
                    *(c.post("/deals", json={"proposal_id": "prop-1"}) for _ in range(6))
                )
    finally:
        app.dependency_overrides.clear()

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    deal_ids = {r.json()["deal_id"] for r in responses}
    assert len(deal_ids) == 1
    assert storage._store["proposal:prop-1"]["deal"]["deal_id"] in deal_ids


@pytest.mark.parametrize("decision, status", [("approve", "approved"), ("reject", "rejected")])
async def test_approval_decision_updates_the_stored_proposal(storage, decision, status):
    from types import SimpleNamespace

    from ad_seller.services.approval_service import _resume_proposal_flow

    storage._store["proposal:prop-1"] = _proposal(status="pending_approval")
    request = SimpleNamespace(flow_state_snapshot={"proposal_id": "prop-1"}, approval_id="apr-1")
    response = SimpleNamespace(decision=decision, decided_by="op", reason=None, modifications=None)
    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.events.helpers.emit_event", new=AsyncMock()),
    ):
        await _resume_proposal_flow(request, response)

    assert storage._store["proposal:prop-1"]["status"] == status


async def test_deal_carries_the_callers_verified_allow_lists(storage):
    identity = BuyerIdentity(
        agency_id="agency-1", seat_id="seat-1", advertiser_domains=["brand.test"]
    )
    storage._store["proposal:prop-1"] = _proposal(buyer_key=_owner_key(identity))

    resp = await _post(storage, identity=identity)

    assert resp.status_code == 200, resp.text
    params = resp.json()["openrtb_params"]
    assert params["wseat"] == ["seat-1"]
    assert params["wadomain"] == ["brand.test"]
