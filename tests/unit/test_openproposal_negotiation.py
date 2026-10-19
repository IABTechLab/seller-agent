"""OpenProposal negotiation threads over the seller-agent engine."""

import copy
import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.auth.api_key_service import ApiKeyService
from ad_seller.interfaces.api import deps
from ad_seller.interfaces.api.main import app
from ad_seller.interfaces.api.routers.openproposal_negotiation import OPEN_EXAMPLES
from ad_seller.models.api_key import ApiKeyCreateRequest
from ad_seller.models.core import DealType
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.storage.factory import get_storage


def _catalog():
    product = ProductDefinition(
        product_id="inv-ctv-apex-series",
        name="Apex Premium Series",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL, DealType.PROGRAMMATIC_GUARANTEED],
        base_cpm=45.0,
        floor_cpm=38.25,
    )
    return {"products": {product.product_id: product}, "inventory_types": ["ctv"]}


async def _key(**identity) -> dict:
    storage = await get_storage()
    created = await ApiKeyService(storage).create_key(
        ApiKeyCreateRequest(label=f"op-neg-{uuid.uuid4().hex[:6]}", **identity)
    )
    return {"X-Api-Key": created.api_key}


def _open_body(**overrides) -> dict:
    body = copy.deepcopy(OPEN_EXAMPLES["ask_lower_price"]["value"])
    body["idempotency_key"] = f"open-{uuid.uuid4().hex[:8]}"
    body.update(overrides)
    return body


@pytest.fixture
async def client():
    with patch.object(deps, "get_product_catalog", AsyncMock(return_value=_catalog())):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def agency():
    return await _key(seat_id="seat-demo", agency_id="agency-demo")


async def _open(client, headers, **overrides):
    resp = await client.post(
        "/api/v1/op-negotiations", headers=headers, json=_open_body(**overrides)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _message(client, headers, thread, **body):
    body.setdefault("idempotency_key", f"m-{uuid.uuid4().hex[:8]}")
    body.setdefault("version", thread["version"])
    return await client.post(
        f"/api/v1/op-negotiations/{thread['negotiation_id']}/messages", headers=headers, json=body
    )


class TestOpen:
    async def test_requires_key(self, client):
        resp = await client.post("/api/v1/op-negotiations", json=_open_body())
        assert resp.status_code == 401

    async def test_low_ask_is_countered(self, client, agency):
        thread = await _open(client, agency)
        assert thread["state"] == "open"
        assert thread["version"] == 2
        assert thread["line_item"]["status"] == "under_review"
        pricing = thread["line_item"]["pricing"][0]
        assert pricing["gross_rate"] == 40.5
        assert pricing["agreed_rate"] == 38.25
        buyer, seller = thread["negotiation_history"]
        assert (buyer["actor"], buyer["action"], buyer["version"]) == ("buyer", "proposed", 1)
        assert buyer["ext"]["seller_agent"]["rate"] == 36.0
        assert (seller["actor"], seller["action"], seller["version"]) == ("seller", "countered", 2)
        assert seller["fields_changed"] == ["pricing[].agreed_rate"]

    async def test_ask_at_list_price_is_accepted(self, client, agency):
        thread = await _open(client, agency, requested_rate=40.5)
        assert thread["state"] == "accepted"
        assert thread["negotiation_history"][-1]["action"] == "accepted"

    async def test_replay_returns_same_thread(self, client, agency):
        body = _open_body()
        first = await client.post("/api/v1/op-negotiations", headers=agency, json=body)
        second = await client.post("/api/v1/op-negotiations", headers=agency, json=body)
        assert first.json()["negotiation_id"] == second.json()["negotiation_id"]
        body["requested_rate"] = 20.0
        conflict = await client.post("/api/v1/op-negotiations", headers=agency, json=body)
        assert conflict.status_code == 409

    async def test_open_auction_is_rejected(self, client, agency):
        resp = await client.post(
            "/api/v1/op-negotiations",
            headers=agency,
            json=_open_body(transaction_mechanism="OPEN_AUCTION"),
        )
        assert resp.status_code == 400

    async def test_seller_secrets_never_leak(self, client, agency):
        thread = await _open(client, agency)
        text = json.dumps(thread)
        for secret in ("floor", "concession", "rounds_remaining", "strategy", "max_rounds"):
            assert secret not in text


class TestMessages:
    async def test_counter_then_accept_then_commit(self, client, agency):
        thread = await _open(client, agency)
        countered = await _message(client, agency, thread, action="counter", requested_rate=39.0)
        thread = countered.json()
        assert thread["state"] == "accepted"
        assert thread["line_item"]["pricing"][0]["agreed_rate"] == 39.0

        commit = await client.post(
            f"/api/v1/op-negotiations/{thread['negotiation_id']}/commit",
            headers=agency,
            json={"idempotency_key": "commit-1", "version": thread["version"]},
        )
        assert commit.status_code == 200, commit.text
        body = commit.json()
        assert body["state"] == "committed"
        assert body["line_item"]["status"] == "agreed"
        assert body["line_item"]["hold_status"]["state"] == "converted"
        assert body["assent"]["version"] == thread["version"]
        deal_id = next(
            r["id"] for r in body["line_item"]["external_references"] if r["role"] == "deal"
        )
        deal = (await client.get(f"/api/v1/deals/{deal_id}")).json()["deal"]
        assert deal["pricing"]["final_cpm"]["amount_micros"] == 39_000_000

        replay = await client.post(
            f"/api/v1/op-negotiations/{thread['negotiation_id']}/commit",
            headers=agency,
            json={"idempotency_key": "commit-1", "version": thread["version"]},
        )
        assert replay.status_code == 200

    async def test_buyer_accepts_seller_counter(self, client, agency):
        thread = await _open(client, agency)
        accepted = (await _message(client, agency, thread, action="accept")).json()
        assert accepted["state"] == "accepted"
        assert accepted["line_item"]["pricing"][0]["agreed_rate"] == 38.25
        assert accepted["negotiation_history"][-1]["actor"] == "buyer"

    async def test_stale_version_is_409(self, client, agency):
        thread = await _open(client, agency)
        resp = await _message(
            client, agency, thread, action="counter", requested_rate=37.0, version=1
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["current_version"] == 2

    async def test_counter_needs_rate(self, client, agency):
        thread = await _open(client, agency)
        resp = await _message(client, agency, thread, action="counter")
        assert resp.status_code == 422

    @pytest.mark.parametrize("action,state", [("decline", "declined"), ("withdraw", "withdrawn")])
    async def test_buyer_ends_thread(self, client, agency, action, state):
        thread = await _open(client, agency)
        ended = (await _message(client, agency, thread, action=action)).json()
        assert ended["state"] == state
        assert ended["line_item"]["status"] == "published"
        again = await _message(client, agency, ended, action="counter", requested_rate=37.0)
        assert again.status_code == 409

    async def test_commit_needs_accepted_thread(self, client, agency):
        thread = await _open(client, agency)
        resp = await client.post(
            f"/api/v1/op-negotiations/{thread['negotiation_id']}/commit",
            headers=agency,
            json={"idempotency_key": "c", "version": thread["version"]},
        )
        assert resp.status_code == 409

    async def test_other_buyer_cannot_see_thread(self, client, agency):
        thread = await _open(client, agency)
        other = await _key(seat_id="seat-other", agency_id="agency-other")
        resp = await client.get(
            f"/api/v1/op-negotiations/{thread['negotiation_id']}", headers=other
        )
        assert resp.status_code == 404

    async def test_message_replay_is_idempotent(self, client, agency):
        thread = await _open(client, agency)
        body = {
            "idempotency_key": "same",
            "version": 2,
            "action": "counter",
            "requested_rate": 37.0,
        }
        first = await _message(client, agency, thread, **body)
        second = await _message(client, agency, thread, **body)
        assert first.json()["negotiation_history"] == second.json()["negotiation_history"]
