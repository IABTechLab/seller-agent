# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""EP-12.2: the canonical negotiation wire edge accepts the shared
``NegotiationMessage`` — the seller side of the historical 422.

POST /api/v1/negotiations/messages must validate the SAME message the
buyer emits (required ``action`` enum + ``buyer_price`` as Money) and
answer the shared ``NegotiationRoundResponse``. The malformed legacy
payload (bare ``{"price": <float>}`` with no ``action``) must fail
validation; a well-formed counter must NOT.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI mismatch).
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402

_COUNTER_RESULT = {
    "negotiation_id": "neg-1",
    "round_number": 1,
    "action": "counter",
    "buyer_price": 25.0,
    "seller_price": 30.0,
    "concession_pct": 0.1,
    "cumulative_concession_pct": 0.1,
    "rationale": "Counter at $30.00",
    "status": "active",
    "rounds_remaining": 4,
}


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


class TestSharedNegotiationMessage:
    async def test_counter_with_money_and_action_is_accepted(self, client):
        """A well-formed shared counter validates and returns the shared round."""
        counter = AsyncMock(return_value=_COUNTER_RESULT)
        with patch(
            "ad_seller.services.negotiation_service.counter_proposal", new=counter
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-neg-1",
                        "action": "counter",
                        "proposal_id": "prop-1",
                        "buyer_price": {"amount_micros": 25_000_000, "currency": "USD"},
                    },
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["negotiation_id"] == "neg-1"
        assert body["status"] == "active"
        assert body["round"]["action"] == "counter"
        # Money in micros both ways.
        assert body["round"]["buyer_price"]["amount_micros"] == 25_000_000
        assert body["round"]["seller_price"]["amount_micros"] == 30_000_000
        assert body["rounds_remaining"] == 4
        # buyer_price (Money) was decoded to internal float dollars for the
        # untouched service.
        assert counter.await_args.kwargs["buyer_price"] == 25.0

    async def test_missing_action_is_422(self, client):
        """The action-less payload that caused the historical 422 still 422s."""
        async with client as c:
            resp = await c.post(
                "/api/v1/negotiations/messages",
                json={
                    "idempotency_key": "idem-neg-2",
                    "proposal_id": "prop-1",
                    "buyer_price": {"amount_micros": 25_000_000, "currency": "USD"},
                },
            )
        assert resp.status_code == 422

    async def test_legacy_bare_price_payload_is_422(self, client):
        """The retired buyer payload ``{"price": <float>}`` does not validate."""
        async with client as c:
            resp = await c.post(
                "/api/v1/negotiations/messages",
                json={"price": 5.0},
            )
        assert resp.status_code == 422

    async def test_counter_without_buyer_price_is_422(self, client):
        """Shared validator: 'counter' requires buyer_price."""
        async with client as c:
            resp = await c.post(
                "/api/v1/negotiations/messages",
                json={
                    "idempotency_key": "idem-neg-3",
                    "action": "counter",
                    "proposal_id": "prop-1",
                },
            )
        assert resp.status_code == 422

    async def test_reject_records_terminal_round(self, client):
        """A walk-away is recorded as a terminal round, not silently dropped."""
        status_data = {
            "negotiation_id": "neg-9",
            "status": "active",
            "rounds": [
                {
                    "round_number": 2,
                    "buyer_price": 24.0,
                    "seller_price": 28.0,
                    "rationale": "prior round",
                }
            ],
        }
        with patch(
            "ad_seller.services.negotiation_service.get_negotiation_status",
            new=AsyncMock(return_value=status_data),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-neg-4",
                        "action": "reject",
                        "negotiation_id": "neg-9",
                    },
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["round"]["action"] == "reject"
        assert body["round"]["buyer_price"]["amount_micros"] == 24_000_000
