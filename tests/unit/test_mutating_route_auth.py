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

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.api_key import ApiKeyRole
from ad_seller.models.buyer_identity import BuyerIdentity

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


def _record(identity=None, role=ApiKeyRole.BUYER):
    return MagicMock(identity=identity or BuyerIdentity(), role=role)


OWNER = _record(BuyerIdentity(agency_id="agency-owner"))
OTHER = _record(BuyerIdentity(agency_id="agency-other"))
OPERATOR = _record(role=ApiKeyRole.OPERATOR)


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


class TestSessionOwnership:
    async def _open(self):
        resp = await _call("POST", "/sessions", {}, record=OWNER)
        assert resp.status_code == 200, resp.text
        return resp.json()["session_id"]

    async def test_other_buyer_cannot_read_message_or_close(self):
        sid = await self._open()
        for method, path, body in [
            ("GET", f"/sessions/{sid}", None),
            ("POST", f"/sessions/{sid}/messages", {"message": "hi"}),
            ("POST", f"/sessions/{sid}/close", None),
        ]:
            resp = await _call(method, path, body, record=OTHER)
            assert resp.status_code == 404, (path, resp.text)

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

    async def test_buyers_list_only_their_own_sessions(self):
        mine = await self._open()
        theirs = (await _call("POST", "/sessions", {}, record=OTHER)).json()["session_id"]

        listed = await _call("GET", "/sessions?buyer_key=agency:agency-other", record=OWNER)

        ids = {s["session_id"] for s in listed.json()["sessions"]}
        assert mine in ids and theirs not in ids
        assert (await _call("GET", "/sessions")).status_code == 401
