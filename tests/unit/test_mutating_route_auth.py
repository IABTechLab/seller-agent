"""Routes that change state require a credential (upstream issue #86).

Curated-deal creation, deal deprecation and migration, bulk deal operations
and the buyer-status / SSP-troubleshoot lookups (which call out to
external systems) are operator actions. Buyer writes (quotes, proposals,
negotiation, sessions, orders, change requests) require an API key unless
``ALLOW_ANONYMOUS_BUYER_WRITES`` is set. A session belongs to the key
that opened it: only that key or an operator may read, message or close
it. Read-only discovery, ``/health`` and ``/.well-known/agent.json`` stay
public.
"""

from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.config import get_settings
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.api_key import ApiKeyRole
from ad_seller.models.buyer_identity import BuyerIdentity
from ad_seller.storage.factory import get_storage

OPERATOR_ROUTES = [
    ("POST", "/api/v1/deals/curated", {"curator_id": "c", "deal_type": "PMP"}),
    ("POST", "/api/v1/deals/DEMO-X/deprecate", {"reason": "probe"}),
    ("POST", "/api/v1/deals/DEMO-X/migrate", {"reason": "probe"}),
    ("POST", "/api/v1/deals/bulk", {"operations": []}),
    ("GET", "/api/v1/deals/DEMO-X/buyer-status?buyer_url=http://127.0.0.1/", None),
    ("GET", "/api/v1/deals/DEMO-X/ssp-troubleshoot?ssp_name=x", None),
]

BUYER_WRITE_ROUTES = [
    ("POST", "/api/v1/quotes", {}),
    ("POST", "/proposals", {}),
    ("POST", "/proposals/prop-x/counter", {}),
    ("POST", "/api/v1/negotiations/messages", {}),
    ("POST", "/sessions", {}),
    ("POST", "/sessions/s-1/messages", {"message": "hi"}),
    ("POST", "/sessions/s-1/close", None),
    ("POST", "/api/v1/orders", {}),
    ("POST", "/api/v1/change-requests", {}),
]

PUBLIC_ROUTES = [
    ("GET", "/health", None),
    ("GET", "/.well-known/agent.json", None),
    ("POST", "/discovery", {"query": "ctv"}),
    ("POST", "/pricing", {"product_id": "nope"}),
    ("POST", "/media-kit/search", {"query": "ctv"}),
]


def _record(identity=None, role=ApiKeyRole.BUYER, key_id="key-x"):
    return MagicMock(identity=identity or BuyerIdentity(), role=role, key_id=key_id)


OWNER = _record(BuyerIdentity(agency_id="agency-owner"), key_id="key-owner")
OTHER = _record(BuyerIdentity(agency_id="agency-other"), key_id="key-other")
OPERATOR = _record(role=ApiKeyRole.OPERATOR, key_id="key-operator")


async def _drop_owner_key_id(session_id):
    """Rewrite a stored session the way sessions were stored before key ids."""
    storage = await get_storage()
    data = await storage.get_session(session_id)
    data.pop("owner_key_id", None)
    await storage.set_session(session_id, data)


async def _call(method, path, body=None, record=None):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: record
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.request(method, path, json=body)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("method, path, body", OPERATOR_ROUTES)
async def test_operator_routes_reject_anonymous_callers(method, path, body):
    resp = await _call(method, path, body)
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("method, path, body", BUYER_WRITE_ROUTES)
async def test_buyer_writes_require_a_key(method, path, body):
    resp = await _call(method, path, body)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"] == "authentication_required"


@pytest.mark.parametrize("method, path, body", BUYER_WRITE_ROUTES[:5])
async def test_anonymous_buyer_writes_can_be_enabled(anonymous_buyer_writes, method, path, body):
    resp = await _call(method, path, body)
    assert resp.status_code != 401, resp.text


@pytest.mark.parametrize("method, path, body", PUBLIC_ROUTES)
async def test_read_only_routes_stay_public(method, path, body):
    resp = await _call(method, path, body)
    assert resp.status_code not in (401, 403), resp.text


async def test_audience_match_stays_public():
    resp = await _call("POST", "/agentic-audience/match", {"audience_ref": {}})
    assert resp.status_code not in (401, 403)


def _session_calls(sid):
    return [
        ("GET", f"/sessions/{sid}", None),
        ("POST", f"/sessions/{sid}/messages", {"message": "hi"}),
        ("POST", f"/sessions/{sid}/close", None),
    ]


async def _listed(record):
    resp = await _call("GET", "/sessions", record=record)
    assert resp.status_code == 200, resp.text
    return {s["session_id"] for s in resp.json()["sessions"]}


class TestSessionOwnership:
    async def _open(self, record=OWNER):
        resp = await _call("POST", "/sessions", {}, record=record)
        assert resp.status_code == 200, resp.text
        return resp.json()["session_id"]

    async def test_other_buyer_cannot_read_message_or_close(self):
        sid = await self._open()
        for method, path, body in _session_calls(sid):
            resp = await _call(method, path, body, record=OTHER)
            assert resp.status_code == 404, (path, resp.text)

    async def test_session_belongs_to_the_key_not_the_shared_advertiser(self):
        key_a = _record(BuyerIdentity(agency_id="ag-1", advertiser_id="adv-1"), key_id="key-a")
        key_b = _record(BuyerIdentity(agency_id="ag-2", advertiser_id="adv-1"), key_id="key-b")
        sid = await self._open(key_a)

        assert sid not in await _listed(key_b)
        for method, path, body in _session_calls(sid):
            resp = await _call(method, path, body, record=key_b)
            assert resp.status_code == 404, (path, resp.text)
        assert sid in await _listed(key_a)
        assert (await _call("GET", f"/sessions/{sid}", record=key_a)).status_code == 200

    async def test_identity_less_keys_do_not_share_sessions(self):
        key_c = _record(key_id="key-c")
        key_d = _record(key_id="key-d")
        sid = await self._open(key_c)

        assert sid not in await _listed(key_d)
        for method, path, body in _session_calls(sid):
            resp = await _call(method, path, body, record=key_d)
            assert resp.status_code == 404, (path, resp.text)
        assert sid in await _listed(key_c)
        assert (await _call("GET", f"/sessions/{sid}", record=key_c)).status_code == 200

    async def test_legacy_keyed_session_falls_back_to_the_pricing_key(self):
        sid = await self._open()
        await _drop_owner_key_id(sid)

        assert (await _call("GET", f"/sessions/{sid}", record=OWNER)).status_code == 200
        assert sid in await _listed(OWNER)
        assert (await _call("GET", f"/sessions/{sid}", record=OTHER)).status_code == 404
        assert sid not in await _listed(OTHER)

    async def test_legacy_identity_less_keyed_session_is_operator_only(self):
        sid = await self._open(_record(key_id="key-c"))
        await _drop_owner_key_id(sid)

        for method, path, body in _session_calls(sid):
            resp = await _call(method, path, body, record=_record(key_id="key-d"))
            assert resp.status_code == 404, (path, resp.text)
        assert (await _call("GET", f"/sessions/{sid}", record=OPERATOR)).status_code == 200

    async def _open_keyless(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "allow_anonymous_buyer_writes", True)
        sid = await self._open(record=None)
        monkeypatch.setattr(settings, "allow_anonymous_buyer_writes", False)
        return sid

    async def test_keyless_session_is_operator_only_by_default(self, monkeypatch):
        sid = await self._open_keyless(monkeypatch)

        for method, path, body in _session_calls(sid):
            assert (await _call(method, path, body)).status_code == 401, path
            resp = await _call(method, path, body, record=OWNER)
            assert resp.status_code == 404, (path, resp.text)
        assert sid not in await _listed(OWNER)

        assert (await _call("GET", f"/sessions/{sid}", record=OPERATOR)).status_code == 200
        assert sid in await _listed(OPERATOR)
        resp = await _call("POST", f"/sessions/{sid}/close", record=OPERATOR)
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    async def test_keyless_session_stays_shared_when_anonymous_writes_allowed(
        self, anonymous_buyer_writes
    ):
        sid = await self._open(record=None)

        assert (await _call("GET", f"/sessions/{sid}")).status_code == 200
        assert (await _call("GET", f"/sessions/{sid}", record=OWNER)).status_code == 200
        assert (await _call("POST", f"/sessions/{sid}/close")).status_code == 200

    async def test_owner_and_operator_can_read_and_close(self):
        sid = await self._open()
        assert (await _call("GET", f"/sessions/{sid}", record=OWNER)).status_code == 200
        assert (await _call("GET", f"/sessions/{sid}", record=OPERATOR)).status_code == 200
        resp = await _call("POST", f"/sessions/{sid}/close", record=OWNER)
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    async def test_unknown_session_is_404_not_200(self):
        resp = await _call("POST", "/sessions/does-not-exist/close", record=OWNER)
        assert resp.status_code == 404

    async def test_operator_closing_unknown_session_is_404(self):
        resp = await _call("POST", "/sessions/does-not-exist/close", record=OPERATOR)
        assert resp.status_code == 404, resp.text

    async def test_buyers_list_only_their_own_sessions(self):
        mine = await self._open()
        theirs = (await _call("POST", "/sessions", {}, record=OTHER)).json()["session_id"]

        listed = await _call("GET", "/sessions?buyer_key=agency:agency-other", record=OWNER)

        ids = {s["session_id"] for s in listed.json()["sessions"]}
        assert mine in ids and theirs not in ids
        assert (await _call("GET", "/sessions")).status_code == 401
