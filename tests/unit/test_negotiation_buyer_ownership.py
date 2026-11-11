"""Negotiation identity must be checked independently of a newly bound quote."""

import copy
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity  # noqa: E402
from ad_seller.models.negotiation import (  # noqa: E402
    NegotiationHistory,
    NegotiationLimits,
    NegotiationRound,
)
from ad_seller.services import negotiation_service  # noqa: E402

pytestmark = pytest.mark.asyncio


def buyer(name, authenticated=True):
    return BuyerContext(
        identity=BuyerIdentity(agency_id=name),
        is_authenticated=authenticated,
        authentication_method="api_key" if authenticated else None,
    )


def history(owner="agency:A", quote_id=None):
    data = NegotiationHistory(
        proposal_id="prop-A",
        product_id="p1",
        buyer_tier="agency",
        strategy="collaborative",
        base_price=45,
        floor_price=38.25,
        quote_id=quote_id,
        limits=NegotiationLimits(
            max_rounds=5,
            per_round_concession_cap=0.05,
            total_concession_cap=0.15,
            gap_split_buyer_share=0.5,
        ),
        rounds=[
            NegotiationRound(round_number=1, buyer_price=40, seller_price=40.25, action="counter")
        ],
    ).model_dump(mode="json")
    if owner is not None:
        data["buyer_key"] = owner
    return data


@pytest.fixture
def storage():
    values = {
        "proposal:prop-A": {"product_id": "p1", "buyer_id": "untrusted", "buyer_key": "agency:A"},
        "negotiation:prop-A": history(),
        "quote:qt-B": {"quote_id": "qt-B"},
        "quote_history:qt-B": {"buyer_id": "agency:B"},
        "product:p1": {"product_id": "p1", "base_cpm": 45, "floor_cpm": 38.25},
    }
    mock = MagicMock()

    async def get(key):
        return copy.deepcopy(values.get(key))

    async def put(key, value, **_kwargs):
        values[key] = copy.deepcopy(value)

    mock.get = AsyncMock(side_effect=get)
    mock.set = AsyncMock(side_effect=put)
    for kind in ["proposal", "negotiation", "quote", "product"]:

        async def read(key, prefix=kind):
            return await get(f"{prefix}:{key}")

        async def write(key, value, prefix=kind):
            await put(f"{prefix}:{key}", value)

        setattr(mock, "get_" + kind, AsyncMock(side_effect=read))
        setattr(mock, "set_" + kind, AsyncMock(side_effect=write))
    mock.values = values
    return mock


@pytest.mark.parametrize("action", ["accept", "reject"])
async def test_another_buyer_cannot_mutate_or_bind_its_own_quote(storage, action):
    before = copy.deepcopy(storage.values)
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        with pytest.raises(HTTPException) as exc:
            await negotiation_service.apply_terminal_action(
                "prop-A", action, quote_id="qt-B", buyer_context=buyer("B")
            )
    assert exc.value.status_code == 404
    assert storage.values == before
    storage.set_negotiation.assert_not_called()


async def test_another_buyer_cannot_counter(storage):
    before = copy.deepcopy(storage.values)
    with (
        patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)),
        patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock) as event,
    ):
        with pytest.raises(HTTPException) as exc:
            await negotiation_service.counter_proposal("prop-A", 40, buyer("B"))
    assert exc.value.status_code == 404
    assert storage.values == before
    event.assert_not_called()


async def test_owner_can_accept_and_owner_stays_persisted(storage):
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        result = await negotiation_service.apply_terminal_action(
            "prop-A", "accept", buyer_context=buyer("A")
        )
    assert result["status"] == "accepted"
    assert storage.values["negotiation:prop-A"]["buyer_key"] == "agency:A"


async def test_self_asserted_identity_does_not_authorize_the_owner(storage):
    before = copy.deepcopy(storage.values)
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        with pytest.raises(HTTPException) as exc:
            await negotiation_service.apply_terminal_action(
                "prop-A", "accept", buyer_context=buyer("A", False)
            )
    assert exc.value.status_code == 404
    assert storage.values == before


async def test_legacy_proposal_body_and_incoming_quote_cannot_establish_ownership(storage):
    storage.values["proposal:prop-A"] = {"product_id": "p1", "buyer_id": "agency:B"}
    storage.values["negotiation:prop-A"] = history(None)
    before = copy.deepcopy(storage.values)
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        with pytest.raises(HTTPException) as exc:
            await negotiation_service.apply_terminal_action(
                "prop-A", "accept", quote_id="qt-B", buyer_context=buyer("B")
            )
    assert exc.value.status_code == 404
    assert storage.values == before


async def test_legacy_proposal_cannot_recover_owner_from_a_previously_bound_quote(storage):
    storage.values["proposal:prop-A"].pop("buyer_key")
    storage.values["negotiation:prop-A"] = history(None, quote_id="qt-B")
    before = copy.deepcopy(storage.values)
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        with pytest.raises(HTTPException):
            await negotiation_service.apply_terminal_action(
                "prop-A", "accept", buyer_context=buyer("B")
            )
    assert storage.values == before


async def test_legacy_history_recovers_a_verified_proposal_owner(storage):
    storage.values["negotiation:prop-A"] = history(None)
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        result = await negotiation_service.apply_terminal_action(
            "prop-A", "accept", buyer_context=buyer("A")
        )
    assert result["status"] == "accepted"
    assert storage.values["negotiation:prop-A"]["buyer_key"] == "agency:A"


async def test_conflicting_stored_owners_fail_closed(storage):
    storage.values["negotiation:prop-A"] = history("agency:B")
    before = copy.deepcopy(storage.values)
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        with pytest.raises(HTTPException):
            await negotiation_service.apply_terminal_action(
                "prop-A", "accept", buyer_context=buyer("B")
            )
    assert storage.values == before


async def test_quote_led_legacy_history_recovers_from_its_own_quote_history(storage):
    data = history(None, quote_id="qt-B")
    data["proposal_id"] = "qt-B"
    storage.values["negotiation:qt-B"] = data
    with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
        result = await negotiation_service.apply_terminal_action(
            "qt-B", "accept", buyer_context=buyer("B")
        )
    assert result["status"] == "accepted"
    assert storage.values["negotiation:qt-B"]["buyer_key"] == "agency:B"


async def test_submission_persists_verified_owner_instead_of_body_identity(storage):
    request = SimpleNamespace(
        product_id="p1",
        deal_type="preferreddeal",
        price=42.08,
        impressions=100000,
        start_date="2027-01-01",
        end_date="2027-02-01",
        buyer_id="agency:B",
    )
    flow = MagicMock()
    flow.handle_proposal_async = AsyncMock(
        return_value={
            "recommendation": "counter",
            "status": "completed",
            "_negotiation_history": history(None),
        }
    )
    with (
        patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)),
        patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow),
    ):
        result = await negotiation_service.submit_proposal(request, buyer("A"), {"products": {}})
    proposal = storage.values["proposal:" + result["proposal_id"]]
    assert proposal["buyer_key"] == "agency:A"
    assert proposal["buyer_id"] == "agency:B"
    assert storage.values["negotiation:" + result["proposal_id"]]["buyer_key"] == "agency:A"


async def test_http_sqlite_two_buyers_and_same_buyer_booking(tmp_path):
    import sys
    from datetime import datetime, timedelta
    from types import ModuleType

    import httpx

    from ad_seller.auth.api_key_service import ApiKeyService
    from ad_seller.models.api_key import ApiKeyCreateRequest
    from ad_seller.storage.sqlite_backend import SQLiteBackend

    # Booking this regression does not activate an external ad server.
    module_name = "ad_seller.flows.execution_activation_flow"
    if module_name not in sys.modules:
        stub = ModuleType(module_name)
        stub.ExecutionActivationFlow = type("ExecutionActivationFlow", (), {})
        sys.modules[module_name] = stub
    from ad_seller.interfaces.api.main import app

    backend = SQLiteBackend(str(tmp_path / "negotiation-owner.db"))
    await backend.connect()
    try:
        keys = ApiKeyService(backend)
        key_a = await keys.create_key(
            ApiKeyCreateRequest(seat_id="seat-A", agency_id="A", label="buyer-A")
        )
        key_b = await keys.create_key(
            ApiKeyCreateRequest(seat_id="seat-B", agency_id="B", label="buyer-B")
        )
        await backend.set_proposal(
            "prop-A", {"product_id": "p1", "buyer_key": "agency:A", "buyer_id": "agency:B"}
        )
        await backend.set_product("p1", {"product_id": "p1", "base_cpm": 45, "floor_cpm": 38.25})
        quote = {
            "quote_id": "qt-B",
            "status": "available",
            "deal_type": "PD",
            "product": {"product_id": "p1", "name": "Test CTV", "inventory_type": "ctv"},
            "pricing": {
                "base_cpm": 45,
                "final_cpm": 44,
                "currency": "USD",
                "pricing_model": "cpm",
                "rationale": "Quoted",
            },
            "terms": {
                "impressions": 100000,
                "flight_start": "2027-01-01",
                "flight_end": "2027-02-01",
                "guaranteed": False,
            },
            "buyer_tier": "agency",
            "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        await backend.set_quote("qt-B", quote)
        await backend.set(
            "quote_history:qt-B", {"buyer_id": "agency:B", "product_id": "p1", "quoted_cpm": 44}
        )
        headers_a = {"X-Api-Key": key_a.api_key}
        headers_b = {"X-Api-Key": key_b.api_key}
        with (
            patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=backend)),
            patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                counter = await client.post(
                    "/api/v1/negotiations/messages",
                    headers=headers_a,
                    json={
                        "idempotency_key": "owner-counter-A",
                        "action": "counter",
                        "proposal_id": "prop-A",
                        "buyer_price": {"amount_micros": 40_000_000, "currency": "USD"},
                    },
                )
                assert counter.status_code == 200, counter.text
                before = await backend.get_negotiation("prop-A")
                foreign = await client.post(
                    "/api/v1/negotiations/messages",
                    headers=headers_b,
                    json={
                        "idempotency_key": "owner-accept-B",
                        "action": "accept",
                        "proposal_id": "prop-A",
                        "quote_id": "qt-B",
                    },
                )
                assert foreign.status_code == 404, foreign.text
                assert await backend.get_negotiation("prop-A") == before
                assert await backend.get("negotiation_by_quote:qt-B") is None
                assert await backend.get("idempotency:negotiation:agency:B:owner-accept-B") is None
                booked_b = await client.post(
                    "/api/v1/deals",
                    headers=headers_b,
                    json={
                        "idempotency_key": "owner-book-B",
                        "quote_id": "qt-B",
                    },
                )
                assert booked_b.status_code == 200, booked_b.text
                assert (
                    booked_b.json()["deal"]["pricing"]["final_cpm"]["amount_micros"] == 44_000_000
                )

                quote_a = {**quote, "quote_id": "qt-A"}
                await backend.set_quote("qt-A", quote_a)
                await backend.set(
                    "quote_history:qt-A",
                    {"buyer_id": "agency:A", "product_id": "p1", "quoted_cpm": 44},
                )
                body = {
                    "idempotency_key": "owner-accept-A",
                    "action": "accept",
                    "proposal_id": "prop-A",
                    "quote_id": "qt-A",
                }
                accepted = await client.post(
                    "/api/v1/negotiations/messages", headers=headers_a, json=body
                )
                assert accepted.status_code == 200, accepted.text
                replay = await client.post(
                    "/api/v1/negotiations/messages", headers=headers_a, json=body
                )
                assert replay.json() == accepted.json()
                booked_a = await client.post(
                    "/api/v1/deals",
                    headers=headers_a,
                    json={
                        "idempotency_key": "owner-book-A",
                        "quote_id": "qt-A",
                    },
                )
                assert booked_a.status_code == 200, booked_a.text
                stored = await backend.get_negotiation("prop-A")
                agreed = stored["rounds"][-1]["seller_price"]
                assert booked_a.json()["deal"]["pricing"]["final_cpm"]["amount_micros"] == round(
                    agreed * 1_000_000
                )
                assert agreed != 44
    finally:
        await backend.disconnect()


async def test_foreign_cached_accept_is_rejected_before_replay(storage):
    import httpx
    from iab_agentic_primitives.protocol import NegotiationMessage

    from ad_seller.interfaces.api import contract_mappers as cm
    from ad_seller.interfaces.api.main import _get_optional_api_key_record, app

    body = {"action": "accept", "proposal_id": "prop-A", "idempotency_key": "old-foreign-accept"}
    message = NegotiationMessage.model_validate(body)
    response = cm.terminal_round_response(
        {"negotiation_id": "neg-A", "rounds": history()["rounds"], "status": "accepted"},
        message.action,
        None,
    )
    cache_key = "idempotency:negotiation:agency:B:old-foreign-accept"
    storage.values[cache_key] = {
        "response": response.model_dump(mode="json"),
        "payload_hash": cm.request_payload_hash(
            message.model_dump(mode="json", exclude={"idempotency_key"})
        ),
    }
    before = copy.deepcopy(storage.values)
    previous = app.dependency_overrides.get(_get_optional_api_key_record)
    app.dependency_overrides[_get_optional_api_key_record] = lambda: SimpleNamespace(
        identity=buyer("B").identity
    )
    try:
        with patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=storage)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                result = await client.post("/api/v1/negotiations/messages", json=body)
        assert result.status_code == 404, result.text
        assert storage.values == before
        storage.get.assert_not_awaited()
    finally:
        if previous is None:
            app.dependency_overrides.pop(_get_optional_api_key_record, None)
        else:
            app.dependency_overrides[_get_optional_api_key_record] = previous
