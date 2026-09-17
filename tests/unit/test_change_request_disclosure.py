# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Change request disclosure and actor-spoofing regression tests (seller-928).

Before the fix, ``GET /api/v1/change-requests`` took OPTIONAL auth,
required no filter, and declared no ``response_model``. One anonymous
request therefore returned every order's change requests, each carrying
``rollback_snapshot`` — a full copy of the order including its
state-machine audit log (every transition's actor and reason), its quote
id, deal id and metadata.

``POST /api/v1/change-requests`` was likewise anonymous and took
``requested_by`` straight off the wire, so a caller could file a change
request attributed to any actor it cared to name — and the audit trail
that would catch it was the thing being disclosed.

These tests pin all four halves: the auth gate, the per-actor ownership
scoping, the declared wire shape, and the server-stamped actor.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

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
from httpx import ASGITransport  # noqa: E402

from ad_seller.auth.dependencies import actor_from_api_key  # noqa: E402
from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.models.api_key import (  # noqa: E402
    API_KEY_INDEX_PREFIX,
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    ApiKeyRole,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.keys = AsyncMock(
        side_effect=lambda pattern="*": [k for k in store if k.startswith(pattern.rstrip("*"))]
    )
    storage.get_order = AsyncMock(side_effect=lambda oid: store.get(f"order:{oid}"))
    storage.set_order = AsyncMock(
        side_effect=lambda oid, data: store.__setitem__(f"order:{oid}", data)
    )
    storage.get_change_request = AsyncMock(
        side_effect=lambda cid: store.get(f"change_request:{cid}")
    )
    storage.set_change_request = AsyncMock(
        side_effect=lambda cid, data: store.__setitem__(f"change_request:{cid}", data)
    )
    storage.list_change_requests = AsyncMock(
        side_effect=lambda filters=None: [
            v
            for k, v in store.items()
            if k.startswith("change_request:")
            and (
                not filters
                or (
                    (not filters.get("order_id") or v.get("order_id") == filters["order_id"])
                    and (not filters.get("status") or v.get("status") == filters["status"])
                )
            )
        ]
    )
    storage._store = store
    return storage


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _seed_key(
    store,
    *,
    role=ApiKeyRole.BUYER,
    key_id="key-test",
    identity=None,
):
    """Seed a valid API key record; return the raw key."""
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=identity if identity is not None else BuyerIdentity(agency_id="agy-1"),
        role=role,
        label=f"{role.value} {key_id}",
    )
    store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    store[f"{API_KEY_INDEX_PREFIX}{key_id}"] = key_hash
    store["api_key_list"] = (store.get("api_key_list") or []) + [key_id]
    return raw_key, record


def _auth(raw_key: str) -> dict:
    return {"Authorization": f"Bearer {raw_key}"}


# The order the snapshot would copy: a realistic audit trail with the
# actor/reason pairs and the quote id that must never reach a stranger.
_SECRET_REASON = "renegotiated after Q3 shortfall call with CFO"
_SECRET_QUOTE_ID = "QT-VICTIM-SECRET"


def _seed_order(store, order_id="ORD-VICTIM", status="booked"):
    store[f"order:{order_id}"] = {
        "order_id": order_id,
        "status": status,
        "deal_id": "DEMO-VICTIM",
        "quote_id": _SECRET_QUOTE_ID,
        "metadata": {"campaign": "victim-spring-2026"},
        "audit_log": {
            "order_id": order_id,
            "transitions": [
                {
                    "from_status": "draft",
                    "to_status": "booked",
                    "actor": "human:victim-trader@agency.example",
                    "reason": _SECRET_REASON,
                }
            ],
        },
    }
    return order_id


def _create_body(order_id, idem, **extra):
    body = {
        "idempotency_key": idem,
        "order_id": order_id,
        "change_type": "creative",
        "reason": "swap creative",
    }
    body.update(extra)
    return body


# =============================================================================
# The auth gate
# =============================================================================


class TestChangeRequestsRequireAuth:
    async def test_anonymous_list_is_401_and_leaks_nothing(self, client, mock_storage):
        """The headline defect: one anonymous unfiltered GET used to return
        every order's change requests."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            victim_key, _ = _seed_key(mock_storage._store, key_id="key-victim")
            seeded = await client.post(
                "/api/v1/change-requests",
                json=_create_body(order_id, "idem-1"),
                headers=_auth(victim_key),
            )
            assert seeded.status_code == 200

            resp = await client.get("/api/v1/change-requests")

        assert resp.status_code == 401
        body = resp.text
        assert "rollback_snapshot" not in body
        assert _SECRET_REASON not in body
        assert _SECRET_QUOTE_ID not in body
        assert order_id not in body

    async def test_anonymous_read_by_id_is_401(self, client, mock_storage):
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            victim_key, _ = _seed_key(mock_storage._store, key_id="key-victim")
            created = await client.post(
                "/api/v1/change-requests",
                json=_create_body(order_id, "idem-1"),
                headers=_auth(victim_key),
            )
            cr_id = created.json()["change_request_id"]
            resp = await client.get(f"/api/v1/change-requests/{cr_id}")

        assert resp.status_code == 401
        assert _SECRET_REASON not in resp.text

    async def test_anonymous_create_is_401(self, client, mock_storage):
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.post(
                "/api/v1/change-requests", json=_create_body(order_id, "idem-anon")
            )
        assert resp.status_code == 401


# =============================================================================
# Ownership scoping — authentication alone is not enough
# =============================================================================


class TestChangeRequestsAreScopedToTheCaller:
    async def test_another_buyer_list_sees_none_of_the_victims_records(self, client, mock_storage):
        """An authenticated stranger must not read the victim's trail."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            victim_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-victim",
                identity=BuyerIdentity(agency_id="agy-victim"),
            )
            attacker_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-attacker",
                identity=BuyerIdentity(agency_id="agy-attacker"),
            )
            await client.post(
                "/api/v1/change-requests",
                json=_create_body(order_id, "idem-1"),
                headers=_auth(victim_key),
            )
            resp = await client.get("/api/v1/change-requests", headers=_auth(attacker_key))

        assert resp.status_code == 200
        data = resp.json()
        assert data["change_requests"] == []
        assert data["count"] == 0
        assert _SECRET_REASON not in resp.text
        assert _SECRET_QUOTE_ID not in resp.text

    async def test_another_buyer_read_by_id_is_404_not_403(self, client, mock_storage):
        """404, so the route is not an existence oracle over CR ids."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            victim_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-victim",
                identity=BuyerIdentity(agency_id="agy-victim"),
            )
            attacker_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-attacker",
                identity=BuyerIdentity(agency_id="agy-attacker"),
            )
            created = await client.post(
                "/api/v1/change-requests",
                json=_create_body(order_id, "idem-1"),
                headers=_auth(victim_key),
            )
            cr_id = created.json()["change_request_id"]
            resp = await client.get(f"/api/v1/change-requests/{cr_id}", headers=_auth(attacker_key))

        assert resp.status_code == 404
        assert _SECRET_REASON not in resp.text

    async def test_owner_still_sees_its_own_records(self, client, mock_storage):
        """The gate is scoping, not a blanket denial."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            victim_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-victim",
                identity=BuyerIdentity(agency_id="agy-victim"),
            )
            created = await client.post(
                "/api/v1/change-requests",
                json=_create_body(order_id, "idem-1"),
                headers=_auth(victim_key),
            )
            cr_id = created.json()["change_request_id"]
            listed = await client.get("/api/v1/change-requests", headers=_auth(victim_key))
            by_id = await client.get(f"/api/v1/change-requests/{cr_id}", headers=_auth(victim_key))

        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["change_requests"][0]["change_request_id"] == cr_id
        assert by_id.status_code == 200
        assert by_id.json()["change_request_id"] == cr_id

    async def test_operator_sees_the_whole_queue(self, client, mock_storage):
        """Operators review change requests, so their reads stay unscoped."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            victim_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-victim",
                identity=BuyerIdentity(agency_id="agy-victim"),
            )
            op_key, _ = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR, key_id="key-op")
            await client.post(
                "/api/v1/change-requests",
                json=_create_body(order_id, "idem-1"),
                headers=_auth(victim_key),
            )
            resp = await client.get("/api/v1/change-requests", headers=_auth(op_key))

        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_replaying_another_buyers_idempotency_key_does_not_return_its_record(
        self, client, mock_storage
    ):
        """The replay path is a read too — it must not bypass the scoping."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            victim_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-victim",
                identity=BuyerIdentity(agency_id="agy-victim"),
            )
            attacker_key, _ = _seed_key(
                mock_storage._store,
                key_id="key-attacker",
                identity=BuyerIdentity(agency_id="agy-attacker"),
            )
            body = _create_body(order_id, "shared-idem-key")
            victim = await client.post(
                "/api/v1/change-requests", json=body, headers=_auth(victim_key)
            )
            attacker = await client.post(
                "/api/v1/change-requests", json=body, headers=_auth(attacker_key)
            )

        victim_cr = victim.json()["change_request_id"]
        assert attacker.status_code == 200
        assert attacker.json()["change_request_id"] != victim_cr
        assert _SECRET_REASON not in attacker.text


# =============================================================================
# The declared wire shape
# =============================================================================


class TestRollbackSnapshotNeverReachesTheWire:
    async def test_no_handler_emits_rollback_snapshot(self, client, mock_storage):
        """Create, list, read, review and apply are all checked: the snapshot
        is still written server-side, but no response model carries it."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            buyer_key, _ = _seed_key(mock_storage._store, key_id="key-buyer")
            op_key, _ = _seed_key(mock_storage._store, role=ApiKeyRole.OPERATOR, key_id="key-op")

            created = await client.post(
                "/api/v1/change-requests",
                # 'targeting' is MATERIAL, so it lands in pending_approval
                # and the review/apply pair is reachable.
                json=_create_body(order_id, "idem-1", change_type="targeting"),
                headers=_auth(buyer_key),
            )
            cr_id = created.json()["change_request_id"]
            listed = await client.get("/api/v1/change-requests", headers=_auth(buyer_key))
            read = await client.get(f"/api/v1/change-requests/{cr_id}", headers=_auth(buyer_key))
            reviewed = await client.post(
                f"/api/v1/change-requests/{cr_id}/review",
                json={"decision": "approve"},
                headers=_auth(op_key),
            )
            applied = await client.post(
                f"/api/v1/change-requests/{cr_id}/apply", headers=_auth(op_key)
            )

        for label, resp in (
            ("create", created),
            ("list", listed),
            ("read", read),
            ("review", reviewed),
            ("apply", applied),
        ):
            assert resp.status_code == 200, f"{label}: {resp.text}"
            assert "rollback_snapshot" not in resp.text, label
            assert _SECRET_REASON not in resp.text, label
            assert _SECRET_QUOTE_ID not in resp.text, label
            assert "audit_log" not in resp.text, label

        # The snapshot is still recorded server-side — this test pins the
        # wire, not the storage shape.
        stored = mock_storage._store[f"change_request:{cr_id}"]
        assert stored["rollback_snapshot"]["order_id"] == order_id

    async def test_every_handler_declares_a_response_model(self):
        """No handler may fall back to serialising the service dict."""
        from ad_seller.interfaces.api.routers import change_requests as cr_router

        routes = [r for r in cr_router.router.routes if "change-requests" in r.path]
        assert len(routes) == 5
        for route in routes:
            assert route.response_model is not None, route.path


# =============================================================================
# The actor is stamped, not asserted
# =============================================================================


class TestRequestedByIsServerStamped:
    async def test_body_requested_by_cannot_influence_the_persisted_actor(
        self, client, mock_storage
    ):
        """The social-engineering vector: name yourself the publisher's CFO
        so the human reviewer approves."""
        order_id = _seed_order(mock_storage._store)
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            buyer_key, record = _seed_key(
                mock_storage._store,
                key_id="key-buyer",
                identity=BuyerIdentity(agency_id="agy-1"),
            )
            resp = await client.post(
                "/api/v1/change-requests",
                json=_create_body(order_id, "idem-1", requested_by="human:cfo@publisher.example"),
                headers=_auth(buyer_key),
            )

        assert resp.status_code == 200
        assert resp.json()["requested_by"] == actor_from_api_key(record)
        assert "cfo@publisher.example" not in resp.text
        cr_id = resp.json()["change_request_id"]
        stored = mock_storage._store[f"change_request:{cr_id}"]
        assert stored["requested_by"] == actor_from_api_key(record)

    def test_create_model_has_no_requested_by_field(self):
        from ad_seller.interfaces.api.schemas import CreateChangeRequestModel

        assert "requested_by" not in CreateChangeRequestModel.model_fields

    def test_actor_is_stable_across_key_rotation(self):
        """The actor is an ownership key: rotating a credential must not
        orphan the records filed under the old one."""
        identity = BuyerIdentity(agency_id="agy-1")
        old = ApiKeyRecord(key_id="key-old", key_hash="h1", key_prefix_hint="p", identity=identity)
        new = ApiKeyRecord(key_id="key-new", key_hash="h2", key_prefix_hint="p", identity=identity)
        assert actor_from_api_key(old) == actor_from_api_key(new) == "agent:agy-1"

    def test_operator_actor_cannot_collide_with_a_buyer_identifier(self):
        operator = ApiKeyRecord(
            key_id="agy-1",
            key_hash="h",
            key_prefix_hint="p",
            identity=BuyerIdentity(),
            role=ApiKeyRole.OPERATOR,
        )
        buyer = ApiKeyRecord(
            key_id="key-b",
            key_hash="h",
            key_prefix_hint="p",
            identity=BuyerIdentity(agency_id="agy-1"),
        )
        assert actor_from_api_key(operator) != actor_from_api_key(buyer)

    def test_identityless_buyer_key_falls_back_to_its_key_id(self):
        """No stable buyer identity to anchor to — must not collapse every
        such key onto one shared 'public' actor."""
        a = ApiKeyRecord(
            key_id="key-a", key_hash="h", key_prefix_hint="p", identity=BuyerIdentity()
        )
        b = ApiKeyRecord(
            key_id="key-b", key_hash="h", key_prefix_hint="p", identity=BuyerIdentity()
        )
        assert actor_from_api_key(a) != actor_from_api_key(b)


# =============================================================================
# The service-level scoping, so no future consumer can re-open it
# =============================================================================


class TestServiceLevelScoping:
    async def test_list_scope_is_not_pushed_into_the_storage_filter(self, mock_storage):
        """StorageBackend.list_change_requests ignores unknown filter keys —
        a scope passed that way would fail OPEN."""
        from ad_seller.services import order_service

        mock_storage._store["change_request:CR-1"] = {
            "change_request_id": "CR-1",
            "order_id": "ORD-A",
            "requested_by": "agent:agy-victim",
        }
        mock_storage._store["change_request:CR-2"] = {
            "change_request_id": "CR-2",
            "order_id": "ORD-A",
            "requested_by": "agent:agy-attacker",
        }
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            result = await order_service.list_change_requests(requested_by="agent:agy-attacker")

        assert result["count"] == 1
        assert result["change_requests"][0]["change_request_id"] == "CR-2"
        passed_filters = mock_storage.list_change_requests.await_args.args[0]
        assert passed_filters is None or "requested_by" not in passed_filters
