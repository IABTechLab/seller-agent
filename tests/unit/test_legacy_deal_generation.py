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
        **overrides,
    }


@pytest.fixture
def storage():
    store = {}
    s = AsyncMock()
    s.get_proposal = AsyncMock(side_effect=lambda p: store.get(f"proposal:{p}"))
    s.set_proposal = AsyncMock(side_effect=lambda p, d: store.__setitem__(f"proposal:{p}", d))
    s.get_negotiation = AsyncMock(side_effect=lambda p: store.get(f"negotiation:{p}"))
    s._store = store
    return s


async def _post(storage, identity=OWNER, proposal_id="prop-1"):
    record = MagicMock(identity=identity) if identity else None
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
    resp = await _post(storage, identity=OTHER)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_bookable"


async def test_keyless_proposal_has_no_owner(storage):
    storage._store["proposal:prop-1"] = _proposal(buyer_key="public", buyer_auth=None)
    resp = await _post(storage)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "proposal_not_bookable"


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


async def test_repeat_call_returns_the_same_deal(storage):
    storage._store["proposal:prop-1"] = _proposal()
    first = (await _post(storage)).json()
    second = (await _post(storage)).json()
    assert second["deal_id"] == first["deal_id"]


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
