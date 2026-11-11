"""Issue #87: POST /api/v1/deals/from-template honours idempotency_key.

Runs the real app against a real SQLite backend with real buyer API keys
seeded in storage, so authentication and the per-buyer key scope are the
production code paths.
"""

import logging
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

_broken_flows = ["ad_seller.flows.execution_activation_flow"]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.events.bus import InMemoryEventBus  # noqa: E402
from ad_seller.events.models import EventType  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.models.api_key import (  # noqa: E402
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    ApiKeyRole,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402
from ad_seller.storage.sqlite_backend import SQLiteBackend  # noqa: E402
from tests.unit.test_trust_tier_verification import _mock_catalog  # noqa: E402

_URL = "/api/v1/deals/from-template"


@pytest.fixture
async def storage(tmp_path):
    backend = SQLiteBackend(f"sqlite:///{tmp_path}/template.db")
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


async def _seed_key(storage, key_id, **identity):
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(**identity),
        role=ApiKeyRole.BUYER,
    )
    await storage.set(f"{API_KEY_STORAGE_PREFIX}{key_hash}", record.model_dump(mode="json"))
    return raw_key


@pytest.fixture
async def keys(storage):
    return {
        "agency_a": await _seed_key(storage, "key-a", agency_id="ag-a", agency_name="A"),
        "agency_b": await _seed_key(storage, "key-b", agency_id="ag-b", agency_name="B"),
        "bare_1": await _seed_key(storage, "key-bare-1"),
        "bare_2": await _seed_key(storage, "key-bare-2"),
    }


@pytest.fixture
async def client(storage, event_bus):
    with (
        patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
        patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=event_bus)),
        patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            AsyncMock(return_value=_mock_catalog()),
        ),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            yield c


def _body(**overrides):
    return {
        "idempotency_key": "tpl-1",
        "deal_type": "PD",
        "product_id": "ctv-premium-sports",
        "impressions": 1_000_000,
        **overrides,
    }


async def _post(client, key, body):
    return await client.post(_URL, json=body, headers={"X-Api-Key": key})


async def _deal_ids(storage):
    return sorted(d["deal_id"] for d in await storage.list_deals())


async def _created_events(event_bus):
    return await event_bus.list_events(event_type=EventType.DEAL_CREATED.value)


class TestReplay:
    async def test_same_key_and_body_replays_the_original_deal(
        self, client, storage, event_bus, keys
    ):
        first = await _post(client, keys["agency_a"], _body())
        second = await _post(client, keys["agency_a"], _body())

        assert first.status_code == second.status_code == 201, (first.text, second.text)
        assert second.json() == first.json()
        assert await _deal_ids(storage) == [first.json()["deal_id"]]
        assert len(await _created_events(event_bus)) == 1

    async def test_equivalent_bodies_replay(self, client, storage, keys):
        first = await _post(client, keys["agency_a"], _body())
        reordered = dict(reversed(list(_body(notes=None, max_cpm=None).items())))
        second = await _post(client, keys["agency_a"], reordered)

        assert second.status_code == 201, second.text
        assert second.json()["deal_id"] == first.json()["deal_id"]
        assert len(await _deal_ids(storage)) == 1

    async def test_replay_reports_the_deals_current_status(self, client, keys):
        first = await _post(client, keys["agency_a"], _body())
        deal_id = first.json()["deal_id"]
        deprecated = await client.post(
            f"/api/v1/deals/{deal_id}/deprecate", json={"reason": "sunset"}
        )
        assert deprecated.status_code == 200, deprecated.text

        replay = await _post(client, keys["agency_a"], _body())

        assert replay.json()["deal_id"] == deal_id
        assert replay.json()["status"] == "deprecated"


class TestWithoutKey:
    @pytest.mark.parametrize("explicit_null", [False, True])
    async def test_no_key_still_creates_a_deal_per_call(self, client, storage, keys, explicit_null):
        body = _body()
        if explicit_null:
            body["idempotency_key"] = None
        else:
            del body["idempotency_key"]

        first = await _post(client, keys["agency_a"], body)
        second = await _post(client, keys["agency_a"], body)

        assert first.status_code == second.status_code == 201
        assert first.json()["deal_id"] != second.json()["deal_id"]
        assert len(await _deal_ids(storage)) == 2
        assert await storage.keys("idempotency:*") == []

    async def test_different_keys_create_different_deals(self, client, storage, keys):
        first = await _post(client, keys["agency_a"], _body(idempotency_key="k-1"))
        second = await _post(client, keys["agency_a"], _body(idempotency_key="k-2"))

        assert first.json()["deal_id"] != second.json()["deal_id"]
        assert len(await _deal_ids(storage)) == 2


class TestConflict:
    @pytest.mark.parametrize(
        "change",
        [
            {"deal_type": "PG"},
            {"deal_type": "pd"},
            {"impressions": 2_000_000},
            {"max_cpm": 99.0},
            {"flight_start": "2027-01-01"},
            {"flight_end": "2027-02-01"},
            {"notes": "rush"},
            {"buyer_identity": {"agency_id": "ag-a"}},
        ],
    )
    async def test_same_key_with_a_different_body_is_409(
        self, client, storage, event_bus, keys, change
    ):
        first = await _post(client, keys["agency_a"], _body())

        resp = await _post(client, keys["agency_a"], _body(**change))

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "idempotency_conflict"
        assert await _deal_ids(storage) == [first.json()["deal_id"]]
        assert len(await _created_events(event_bus)) == 1


class TestScope:
    async def test_two_buyers_with_the_same_key_get_their_own_deals(self, client, storage, keys):
        a = await _post(client, keys["agency_a"], _body())
        b = await _post(client, keys["agency_b"], _body())
        a_again = await _post(client, keys["agency_a"], _body())

        assert a.json()["deal_id"] != b.json()["deal_id"]
        assert a_again.json()["deal_id"] == a.json()["deal_id"]
        assert len(await _deal_ids(storage)) == 2

    async def test_keys_without_identity_are_scoped_per_api_key(self, client, storage, keys):
        one = await _post(client, keys["bare_1"], _body())
        two = await _post(client, keys["bare_2"], _body())
        one_again = await _post(client, keys["bare_1"], _body())

        assert one.json()["deal_id"] != two.json()["deal_id"]
        assert one_again.json()["deal_id"] == one.json()["deal_id"]

    async def test_a_quote_booking_record_with_the_same_key_is_ignored(self, client, storage, keys):
        await storage.set(
            "idempotency:deal:agency:ag-a:tpl-1",
            {"deal_id": "DEAL-FROM-QUOTE", "payload_hash": "other"},
        )

        resp = await _post(client, keys["agency_a"], _body())

        assert resp.status_code == 201, resp.text
        assert resp.json()["deal_id"] != "DEAL-FROM-QUOTE"


class TestKeyFormat:
    @pytest.mark.parametrize("key", ["", "x" * 256, 5, ["k"], {"k": 1}])
    async def test_invalid_key_is_422_and_nothing_is_created(self, client, storage, keys, key):
        resp = await _post(client, keys["agency_a"], _body(idempotency_key=key))

        assert resp.status_code == 422, resp.text
        assert await _deal_ids(storage) == []

    async def test_255_character_key_is_accepted(self, client, keys):
        body = _body(idempotency_key="k" * 255)

        first = await _post(client, keys["agency_a"], body)
        second = await _post(client, keys["agency_a"], body)

        assert first.status_code == 201, first.text
        assert second.json()["deal_id"] == first.json()["deal_id"]


class TestFailures:
    @pytest.mark.parametrize(
        "bad, status",
        [
            ({"max_cpm": 1.0}, 422),
            ({"deal_type": "BOGUS"}, 400),
            ({"product_id": "no-such-product"}, 404),
        ],
    )
    async def test_a_failed_attempt_records_nothing_so_the_corrected_retry_creates(
        self, client, storage, keys, bad, status
    ):
        failed = await _post(client, keys["agency_a"], _body(**bad))
        assert failed.status_code == status, failed.text
        assert await storage.keys("idempotency:*") == []

        retried = await _post(client, keys["agency_a"], _body())

        assert retried.status_code == 201, retried.text
        assert len(await _deal_ids(storage)) == 1

    async def test_unauthenticated_attempt_records_nothing(self, client, storage):
        resp = await client.post(_URL, json=_body())

        assert resp.status_code == 401
        assert await storage.keys("idempotency:*") == []

    async def test_event_failure_after_persist_is_replayed_on_retry(self, client, storage, keys):
        with patch(
            "ad_seller.services.deal_service._emit_deal_created",
            AsyncMock(side_effect=RuntimeError("bus and audit fallback both down")),
        ):
            first = await _post(client, keys["agency_a"], _body())
        assert first.status_code == 500
        [persisted] = await _deal_ids(storage)

        retried = await _post(client, keys["agency_a"], _body())

        assert retried.status_code == 201, retried.text
        assert retried.json()["deal_id"] == persisted
        assert await _deal_ids(storage) == [persisted]

    async def test_record_write_failure_still_returns_the_deal_and_logs(
        self, client, storage, keys, caplog
    ):
        real_set = storage.set

        async def _set(key, value, ttl=None):
            if key.startswith("idempotency:"):
                raise RuntimeError("kv write failed")
            return await real_set(key, value, ttl=ttl)

        with (
            patch.object(storage, "set", side_effect=_set),
            caplog.at_level(logging.WARNING, logger="ad_seller.services.deal_service"),
        ):
            resp = await _post(client, keys["agency_a"], _body())

        assert resp.status_code == 201, resp.text
        assert any("Could not record idempotency key" in r.message for r in caplog.records)

    async def test_read_failure_is_treated_as_no_prior_record(self, client, storage, keys):
        first = await _post(client, keys["agency_a"], _body())
        real_get = storage.get

        async def _get(key):
            if key.startswith("idempotency:"):
                raise RuntimeError("kv read failed")
            return await real_get(key)

        with patch.object(storage, "get", side_effect=_get):
            second = await _post(client, keys["agency_a"], _body())

        assert second.status_code == 201, second.text
        assert second.json()["deal_id"] != first.json()["deal_id"]

    async def test_record_expires_after_24_hours(self, client, storage, keys):
        real_set = storage.set
        ttls = []

        async def _set(key, value, ttl=None):
            if key.startswith("idempotency:"):
                ttls.append(ttl)
            return await real_set(key, value, ttl=ttl)

        with patch.object(storage, "set", side_effect=_set):
            await _post(client, keys["agency_a"], _body())

        assert ttls == [86400]
