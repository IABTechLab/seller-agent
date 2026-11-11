"""Issue #122: SSP distribution validates deal_type and cpm, and maps Index failures.

Runs the real app and the real ``IndexExchangeSSPClient`` over real HTTP
against a local stand-in for the Index Exchange Deals API, with a real
SQLite backend for deals, approvals and API keys.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

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

from ad_seller.clients.ssp_base import SSPRegistry  # noqa: E402
from ad_seller.clients.ssp_index_exchange import IndexExchangeSSPClient  # noqa: E402
from ad_seller.events.bus import InMemoryEventBus  # noqa: E402
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

_ERRORS = {
    400: "floor: must be greater than or equal to 0",
    401: "token expired",
    403: "account 12345 not permitted",
    404: "account not found",
    409: "externalDealID already exists",
    422: "startDate is after endDate",
    429: "rate limited",
    500: "internal error",
    503: "maintenance",
}


class _FakeIndex(BaseHTTPRequestHandler):
    """POST/GET /api/deals/v3/deals and GET /api/deals/v1/dsps/-/seats."""

    state: dict = {}

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/deals/v1/dsps/-/seats":
            seats = [
                {"dspID": dsp, "seatID": seat, "status": "A", "name": f"DSP {dsp}"}
                for seat in query.get("seatIDs", [])
                for dsp in self.state["seats"].get(seat, [])
            ]
            return self._send(200, {"seats": seats})
        if url.path == "/api/deals/v3/deals":
            ext = query.get("search", [""])[0]
            return self._send(
                200, {"deals": [d for d in self.state["deals"] if d["externalDealID"] == ext]}
            )
        self._send(404, {"message": "no route"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.state["posts"].append(body)
        status = self.state["fail_with"]
        if status is None:
            deal = {**body, "internalDealID": f"IX-{len(self.state['posts'])}", "status": "active"}
            self.state["deals"].append(deal)
            return self._send(201, deal)
        self._send(status, {"message": _ERRORS[status]})

    def log_message(self, *args):
        pass


@pytest.fixture
def index():
    _FakeIndex.state = {
        "posts": [],
        "deals": [],
        "fail_with": None,
        "seats": {"seat-1": [5551], "shared-seat": [52, 85]},
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeIndex)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    yield f"http://127.0.0.1:{server.server_port}", _FakeIndex.state
    server.shutdown()
    server.server_close()


@pytest.fixture
async def storage(tmp_path):
    backend = SQLiteBackend(f"sqlite:///{tmp_path}/ssp.db")
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def operator_key(storage):
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id="key-operator",
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(agency_id="seller-ops", agency_name="Seller Ops"),
        role=ApiKeyRole.OPERATOR,
    )
    await storage.set(f"{API_KEY_STORAGE_PREFIX}{key_hash}", record.model_dump(mode="json"))
    return raw_key


@pytest.fixture
async def client(index, storage, operator_key):
    base_url, _ = index

    def _registry(*_args, **_kwargs):
        registry = SSPRegistry()
        registry.register(
            "index_exchange", IndexExchangeSSPClient(base_url=base_url, api_key="ix-token")
        )
        return registry

    with (
        patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
        patch("ad_seller.events.bus.get_event_bus", AsyncMock(return_value=InMemoryEventBus())),
        patch("ad_seller.clients.ssp_factory.build_ssp_registry", _registry),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Api-Key": operator_key},
        ) as c:
            yield c


def _body(**overrides):
    return {
        "deal_id": "DEAL-1",
        "name": "Sports PD",
        "account_id": 12345,
        "buyer_seat_ids": ["seat-1"],
        "cpm": 12.5,
        "ssp_name": "index_exchange",
        **overrides,
    }


async def _distribute(client, **overrides):
    return await client.post("/api/v1/deals/distribute", json=_body(**overrides))


class TestDealType:
    @pytest.mark.parametrize(
        "sent, class_id, auction, guaranteed, reads_back",
        [
            ("PG", 1, "fixed", True, "pg"),
            ("pg", 1, "fixed", True, "pg"),
            ("PROGRAMMATIC_GUARANTEED", 1, "fixed", True, "pg"),
            ("PD", 1, "fixed", False, "preferred"),
            ("pd", 1, "fixed", False, "preferred"),
            ("PREFERRED", 1, "fixed", False, "preferred"),
            ("Preferred_Deal", 1, "fixed", False, "preferred"),
            ("PA", 1, "first", False, "pmp"),
            ("PMP", 1, "first", False, "pmp"),
            ("private_auction", 1, "first", False, "pmp"),
            ("AUCTION_PACKAGE", 4, "first", None, "auction_package"),
            ("auction_package", 4, "first", None, "auction_package"),
            (None, 1, "first", False, "pmp"),
        ],
    )
    async def test_accepted_spellings_create_the_matching_index_deal(
        self, client, index, sent, class_id, auction, guaranteed, reads_back
    ):
        _, state = index
        body = _body(deal_type=sent) if sent is not None else _body()

        resp = await client.post("/api/v1/deals/distribute", json=body)

        assert resp.status_code == 200, resp.text
        assert len(state["posts"]) == 1
        posted = state["posts"][0]
        assert posted["classID"] == class_id
        assert posted["auctionType"] == auction
        assert posted.get("directConfigurations", {}).get("programmaticGuaranteed") == guaranteed
        assert resp.json()["deal"]["deal_type"] == reads_back

    @pytest.mark.parametrize(
        "sent", ["BOGUS", "", " PD ", "PG,PD", "PREFERRED DEAL", "PMPX", "X" * 10_000]
    )
    async def test_unknown_deal_type_is_400_and_nothing_reaches_index(
        self, client, index, storage, sent
    ):
        _, state = index

        resp = await _distribute(client, deal_type=sent)

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"] == "invalid_deal_type"
        assert state["posts"] == []
        assert await storage.keys("approval:*") == []

    @pytest.mark.parametrize("sent", [5, ["PD"], {"code": "PD"}])
    async def test_non_string_deal_type_is_rejected_by_the_schema(self, client, index, sent):
        _, state = index

        resp = await _distribute(client, deal_type=sent)

        assert resp.status_code == 422, resp.text
        assert state["posts"] == []

    async def test_deal_type_is_checked_before_any_connector_is_needed(self, client):
        with patch(
            "ad_seller.clients.ssp_factory.build_ssp_registry", lambda *a, **k: SSPRegistry()
        ):
            resp = await _distribute(client, deal_type="BOGUS")

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_deal_type"


class TestCpm:
    @pytest.mark.parametrize("cpm", [12.5, 0, 0.0001, 9_999.99])
    async def test_valid_cpm_is_sent_as_the_floor(self, client, index, cpm):
        _, state = index

        resp = await _distribute(client, deal_type="PD", cpm=cpm)

        assert resp.status_code == 200, resp.text
        assert state["posts"][0]["floor"] == cpm

    async def test_omitted_cpm_is_unchanged(self, client, index):
        _, state = index
        body = _body(deal_type="PD")
        del body["cpm"]

        resp = await client.post("/api/v1/deals/distribute", json=body)

        assert resp.status_code == 200, resp.text
        assert state["posts"][0]["floor"] is None

    @pytest.mark.parametrize("raw", ["-5", "-0.01", "NaN", "Infinity", "-Infinity"])
    async def test_negative_or_non_finite_cpm_is_400(self, client, index, raw):
        _, state = index
        payload = json.dumps(_body(deal_type="PD", cpm=0)).replace('"cpm": 0', f'"cpm": {raw}')

        resp = await client.post(
            "/api/v1/deals/distribute",
            content=payload,
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"] == "invalid_cpm"
        assert state["posts"] == []


class TestIndexFailures:
    @pytest.mark.parametrize(
        "status, code, error",
        [
            (400, 400, "ssp_rejected"),
            (422, 400, "ssp_rejected"),
            (409, 409, "ssp_conflict"),
            (401, 502, "ssp_error"),
            (403, 502, "ssp_error"),
            (404, 502, "ssp_error"),
            (429, 502, "ssp_error"),
            (500, 502, "ssp_error"),
            (503, 502, "ssp_error"),
        ],
    )
    async def test_index_failure_maps_to_a_clear_error(self, client, index, status, code, error):
        _, state = index
        state["fail_with"] = status

        resp = await _distribute(client, deal_type="PG")

        assert resp.status_code == code, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == error
        assert detail["ssp_status"] == status
        assert _ERRORS[status] in detail["message"]
        assert len(state["posts"]) == 1

    async def test_unreachable_index_is_502(self, client, storage, operator_key):
        def _dead_registry(*_args, **_kwargs):
            registry = SSPRegistry()
            registry.register(
                "index_exchange", IndexExchangeSSPClient(base_url="http://127.0.0.1:1", api_key="t")
            )
            return registry

        with patch("ad_seller.clients.ssp_factory.build_ssp_registry", _dead_registry):
            resp = await _distribute(client, deal_type="PG")

        assert resp.status_code == 502, resp.text
        assert resp.json()["detail"]["error"] == "ssp_error"
        assert resp.json()["detail"]["ssp_status"] is None

    @pytest.mark.parametrize("status, code", [(401, 502), (400, 400)])
    async def test_seat_lookup_failure_is_mapped_and_nothing_is_created(
        self, client, index, status, code
    ):
        _, state = index

        def _failing_lookup(*_args, **_kwargs):
            request = httpx.Request("GET", "http://index/api/deals/v1/dsps/-/seats")
            return httpx.Response(status, json={"message": _ERRORS[status]}, request=request)

        with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_failing_lookup)):
            resp = await _distribute(client, deal_type="PG")

        assert resp.status_code == code, resp.text
        assert resp.json()["detail"]["ssp_status"] == status
        assert state["posts"] == []

    async def test_5xx_with_the_deal_present_still_returns_it(self, client, index):
        _, state = index
        state["fail_with"] = 503
        state["deals"].append(
            {
                "externalDealID": "DEAL-1",
                "internalDealID": "IX-EXISTING",
                "classID": 1,
                "auctionType": "fixed",
                "directConfigurations": {"programmaticGuaranteed": True},
                "status": "active",
            }
        )

        resp = await _distribute(client, deal_type="PG")

        assert resp.status_code == 200, resp.text
        assert resp.json()["deal_id"] == "IX-EXISTING"

    async def test_missing_account_is_still_invalid_request(self, client, index):
        _, state = index

        resp = await _distribute(client, deal_type="PG", account_id=None)

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_request"
        assert state["posts"] == []


class TestApprovalResume:
    async def _pending(self, client):
        resp = await _distribute(client, deal_type="PD", buyer_seat_ids=["shared-seat"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending_approval"
        approval_id = resp.json()["approval_id"]
        decided = await client.post(
            f"/approvals/{approval_id}/decide",
            json={"decision": "approve", "modifications": {"selected_dsp_id": 52}},
        )
        assert decided.status_code == 200, decided.text
        return approval_id

    async def test_rejected_create_maps_and_restores_the_approval(self, client, index):
        _, state = index
        approval_id = await self._pending(client)
        state["fail_with"] = 400

        resp = await client.post(f"/approvals/{approval_id}/resume")

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"] == "ssp_rejected"
        assert (await client.get(f"/approvals/{approval_id}")).json()["request"][
            "status"
        ] == "approved"

        state["fail_with"] = None
        retried = await client.post(f"/approvals/{approval_id}/resume")

        assert retried.status_code == 200, retried.text
        assert retried.json()["deal"]["deal_type"] == "preferred"
        assert [p["directConfigurations"]["dspID"] for p in state["posts"]] == [52, 52]
        assert (await client.get(f"/approvals/{approval_id}")).json()["request"][
            "status"
        ] == "consumed"

    async def test_mcp_resume_returns_the_mapped_error(self, client, index):
        from ad_seller.interfaces import mcp_server

        _, state = index
        approval_id = await self._pending(client)
        state["fail_with"] = 401

        with patch.object(mcp_server.mcp, "get_context", side_effect=LookupError):
            result = json.loads(await mcp_server.resume_approval(approval_id))

        assert result["detail"]["error"] == "ssp_error"
        assert result["detail"]["ssp_status"] == 401


class TestMcpDistribute:
    async def _call(self, **kwargs):
        from ad_seller.interfaces import mcp_server

        with patch.object(mcp_server.mcp, "get_context", side_effect=LookupError):
            return json.loads(
                await mcp_server.distribute_deal_via_ssp(
                    deal_id="DEAL-1", ssp_name="index_exchange", account_id=12345, **kwargs
                )
            )

    async def test_pd_creates_a_preferred_deal(self, client, index, storage):
        _, state = index
        await storage.set_deal("DEAL-1", {"deal_id": "DEAL-1", "buyer_seat_ids": ["seat-1"]})

        result = await self._call(deal_type="PD", cpm=12.5)

        assert result["deal"]["deal_type"] == "preferred"
        assert state["posts"][0]["auctionType"] == "fixed"

    @pytest.mark.parametrize(
        "kwargs, error",
        [({"deal_type": "BOGUS"}, "invalid_deal_type"), ({"cpm": -1}, "invalid_cpm")],
    )
    async def test_invalid_input_returns_the_error(self, client, index, kwargs, error):
        _, state = index

        result = await self._call(**kwargs)

        assert result["detail"]["error"] == error
        assert state["posts"] == []

    async def test_index_rejection_returns_the_mapped_error(self, client, index, storage):
        _, state = index
        await storage.set_deal("DEAL-1", {"deal_id": "DEAL-1", "buyer_seat_ids": ["seat-1"]})
        state["fail_with"] = 409

        result = await self._call(deal_type="PG", cpm=10)

        assert result["detail"]["error"] == "ssp_conflict"


async def test_service_raises_an_http_exception_for_a_rejected_create(index, storage):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from ad_seller.services import deal_service

    base_url, state = index
    state["fail_with"] = 422
    registry = SSPRegistry()
    registry.register("index_exchange", IndexExchangeSSPClient(base_url=base_url, api_key="t"))
    request = SimpleNamespace(
        deal_id="DEAL-1",
        deal_type="PG",
        name="n",
        advertiser=None,
        cpm=10.0,
        buyer_seat_ids=["seat-1"],
        account_id=12345,
        start_date=None,
        end_date=None,
        targeting=None,
        ssp_name="index_exchange",
        inventory_type=None,
    )

    with (
        patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
        patch("ad_seller.clients.ssp_factory.build_ssp_registry", return_value=registry),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await deal_service.distribute_deal_via_ssp(request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "ssp_rejected"
    assert exc_info.value.detail["ssp_status"] == 422
