"""Issue #135: export, push and migration read every stored deal's price.

Curated deals store flat ``total_cpm``, template deals flat
``actual_price_cpm``, quote-booked deals nested ``pricing.final_cpm``. Runs
through the real app against a real SQLite backend; push goes over real
HTTP to a local capture server.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

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

from ad_seller.interfaces.api import deps  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.services import deal_service  # noqa: E402
from ad_seller.storage.sqlite_backend import SQLiteBackend  # noqa: E402
from tests.unit.test_curated_deal import _catalog, _make_product  # noqa: E402

_PRODUCT_ID = "ctv-premium-sports"


def _curated(deal_id="CUR-1", deal_type="PD", total=16.5):
    return {
        "deal_id": deal_id,
        "deal_type": deal_type,
        "status": "confirmed",
        "product_id": _PRODUCT_ID,
        "base_cpm": 15.0,
        "curator_fee_cpm": 1.5,
        "total_cpm": total,
        "currency": "USD",
        "flight_start": "2026-12-01",
        "flight_end": "2026-12-31",
        "buyer_seat_ids": [],
    }


def _template(deal_id="DEMO-1", price=28.26):
    return {
        "deal_id": deal_id,
        "deal_type": "PD",
        "status": "confirmed",
        "product_id": _PRODUCT_ID,
        "actual_price_cpm": price,
        "currency": "USD",
    }


def _quote_booked(deal_id="DEAL-1", price=32.5):
    return {
        "deal_id": deal_id,
        "deal_type": "PG",
        "status": "confirmed",
        "product": {"product_id": _PRODUCT_ID, "name": "CTV", "inventory_type": "ctv"},
        "pricing": {"base_cpm": 40.0, "final_cpm": price, "currency": "USD"},
        "terms": {"impressions": 1_000_000},
    }


class TestStoredDealCpm:
    @pytest.mark.parametrize(
        "deal, cpm",
        [
            (_curated(), 16.5),
            (_template(), 28.26),
            (_quote_booked(), 32.5),
            ({**_curated(), "actual_price_cpm": 20.0}, 20.0),
            ({**_template(), "pricing": {"final_cpm": 99.0}}, 28.26),
            ({**_quote_booked(), "total_cpm": 99.0}, 32.5),
            (_template(price=0), 0),
            (_quote_booked(price=0), 0),
            (_curated(total=0), 0),
            ({**_curated(), "pricing": None}, 16.5),
            ({**_curated(), "pricing": {}}, 16.5),
            ({**_curated(), "pricing": "n/a"}, 16.5),
            ({**_curated(), "actual_price_cpm": None}, 16.5),
            ({"deal_id": "X", "deal_type": "PD"}, None),
            ({"deal_id": "X", "pricing": None}, None),
        ],
    )
    def test_resolves_the_booked_price(self, deal, cpm):
        assert deal_service._stored_deal_cpm(deal) == cpm

    @pytest.mark.parametrize(
        "cpm, units, nanos",
        [
            (16.5, "16", 500_000_000),
            (28.26, "28", 260_000_000),
            (32.0, "32", 0),
            (0, "0", 0),
            (None, "0", 0),
            (0.000000001, "0", 1),
            (9.9999999999, "10", 0),
            (1234.56789, "1234", 567_890_000),
        ],
    )
    def test_dv360_money_keeps_the_fraction(self, cpm, units, nanos):
        assert deal_service._dv360_money(cpm) == {
            "currencyCode": "USD",
            "units": units,
            "nanos": nanos,
        }


class _CaptureHandler(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        type(self).received.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "accepted"}')

    def log_message(self, *args):
        pass


@pytest.fixture
def buyer():
    _CaptureHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/deals", _CaptureHandler.received
    server.shutdown()
    server.server_close()


@pytest.fixture
async def storage(tmp_path):
    backend = SQLiteBackend(f"sqlite:///{tmp_path}/price.db")
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def client(storage):
    catalog = _catalog(_make_product(product_id=_PRODUCT_ID, base_cpm=45.0, floor_cpm=35.0))
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    app.dependency_overrides[deps._require_operator_api_key_record] = lambda: MagicMock()
    with (
        patch("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage)),
        patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            AsyncMock(return_value=catalog),
        ),
    ):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    app.dependency_overrides.clear()


async def _seed(storage, *deals):
    for deal in deals:
        await storage.set_deal(deal["deal_id"], deal)


def _by_id(deals, id_key, price):
    return {d[id_key]: price(d) for d in deals}


_FORMATS = {
    "ttd": ("DealId", lambda d: d["BidFloor"]),
    "amazon": ("dealId", lambda d: d["priceAmount"]),
    "xandr": ("id", lambda d: d["floor_price"]),
    "dv360": (
        "dealId",
        lambda d: int(d["fixedCpm"]["units"]) + d["fixedCpm"]["nanos"] / 1_000_000_000,
    ),
}


class TestExport:
    @pytest.mark.parametrize("fmt", sorted(_FORMATS))
    async def test_every_booking_path_exports_its_price(self, client, storage, fmt):
        await _seed(storage, _curated(), _template(), _quote_booked(), _template("DEMO-0", 0))
        id_key, price = _FORMATS[fmt]

        resp = await client.get(f"/api/v1/deals/export?format={fmt}")

        assert resp.status_code == 200
        assert _by_id(resp.json()["deals"], id_key, price) == {
            "CUR-1": pytest.approx(16.5),
            "DEMO-1": pytest.approx(28.26),
            "DEAL-1": pytest.approx(32.5),
            "DEMO-0": 0,
        }

    @pytest.mark.parametrize("fmt", sorted(_FORMATS))
    async def test_unpriced_or_malformed_records_export_zero(self, client, storage, fmt):
        await _seed(
            storage,
            {"deal_id": "BARE", "deal_type": "PD", "status": "confirmed"},
            {"deal_id": "NULLPRICING", "deal_type": "PA", "status": "confirmed", "pricing": None},
        )
        id_key, price = _FORMATS[fmt]

        resp = await client.get(f"/api/v1/deals/export?format={fmt}")

        assert resp.status_code == 200
        assert _by_id(resp.json()["deals"], id_key, price) == {"BARE": 0, "NULLPRICING": 0}

    async def test_generic_export_returns_records_unchanged(self, client, storage):
        await _seed(storage, _curated())

        resp = await client.get("/api/v1/deals/export?format=generic")

        assert resp.json()["deals"] == [_curated()]

    async def test_status_filter_still_applies(self, client, storage):
        await _seed(storage, _curated(), {**_template(), "status": "deprecated"})

        resp = await client.get("/api/v1/deals/export?format=ttd&status=confirmed")

        assert [d["DealId"] for d in resp.json()["deals"]] == ["CUR-1"]


class TestPush:
    @pytest.mark.parametrize(
        "deal, fixed, floor",
        [
            (_curated(deal_type="PD"), 16.5, None),
            (_curated(deal_type="PA"), None, 16.5),
            (_template(), 28.26, None),
            (_quote_booked(), 32.5, None),
        ],
    )
    async def test_stored_price_reaches_the_buyer(self, client, storage, buyer, deal, fixed, floor):
        url, received = buyer
        await _seed(storage, deal)

        resp = await client.post(
            "/api/v1/deals/push", json={"deal_id": deal["deal_id"], "buyer_urls": [url]}
        )

        assert resp.status_code == 200, resp.text
        assert len(received) == 1
        assert received[0].get("fixed_price_cpm") == fixed
        assert received[0].get("floor_price_cpm") == floor
        assert received[0]["openrtb_params"]["bidfloor"] == pytest.approx(fixed or floor)

    async def test_request_price_still_overrides_the_stored_price(self, client, storage, buyer):
        url, received = buyer
        await _seed(storage, _curated())

        await client.post(
            "/api/v1/deals/push",
            json={"deal_id": "CUR-1", "buyer_urls": [url], "price": 19.0},
        )

        assert received[0]["fixed_price_cpm"] == 19.0

    async def test_unstored_deal_without_a_price_pushes_zero(self, client, buyer):
        url, received = buyer

        await client.post(
            "/api/v1/deals/push",
            json={"deal_id": "ADHOC-1", "buyer_urls": [url], "deal_type": "PD"},
        )

        assert received[0]["fixed_price_cpm"] == 0


class TestMigrate:
    async def _migrate(self, client, deal_id, **body):
        resp = await client.post(
            f"/api/v1/deals/{deal_id}/migrate",
            json={"old_deal_id": deal_id, "reason": "better path", **body},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    @pytest.mark.parametrize(
        "deal, cpm",
        [(_curated(), 16.5), (_template(), 28.26), (_quote_booked(), 32.5)],
    )
    async def test_replacement_keeps_the_booked_price(self, client, storage, deal, cpm):
        await _seed(storage, deal)

        result = await self._migrate(client, deal["deal_id"])

        new_id = result["new_deal_id"]
        assert (await storage.get_deal(new_id))["actual_price_cpm"] == cpm
        readback = await client.get(f"/api/v1/deals/{new_id}")
        assert readback.status_code == 200, readback.text
        final = readback.json()["deal"]["pricing"]["final_cpm"]
        assert final["amount_micros"] == round(cpm * 1_000_000)
        assert (await storage.get_deal(deal["deal_id"]))["status"] == "deprecated"

    async def test_max_cpm_still_overrides_the_old_price(self, client, storage):
        await _seed(storage, _curated())

        result = await self._migrate(client, "CUR-1", max_cpm=18.0)

        assert result["new_deal"]["actual_price_cpm"] == 18.0

    async def test_deal_with_null_pricing_migrates(self, client, storage):
        await _seed(
            storage,
            {"deal_id": "NULLPRICING", "deal_type": "PD", "status": "confirmed", "pricing": None},
        )

        result = await self._migrate(client, "NULLPRICING")

        assert result["new_deal"]["actual_price_cpm"] is None

    async def test_quote_booked_replacement_keeps_product_terms_and_currency(self, client, storage):
        old = _quote_booked()
        old["pricing"]["currency"] = "EUR"
        old["terms"].update(flight_start="2027-01-01", flight_end="2027-01-31")
        await _seed(storage, old)

        result = await self._migrate(client, "DEAL-1")

        new = result["new_deal"]
        assert new["product_id"] == _PRODUCT_ID
        assert new["impressions"] == 1_000_000
        assert (new["flight_start"], new["flight_end"]) == ("2027-01-01", "2027-01-31")
        assert new["currency"] == "EUR"
        deal = (await client.get(f"/api/v1/deals/{new['deal_id']}")).json()["deal"]
        assert deal["product"]["product_id"] == _PRODUCT_ID
        assert deal["terms"]["impressions"] == 1_000_000
        assert deal["pricing"]["final_cpm"] == {"amount_micros": 32_500_000, "currency": "EUR"}

    async def test_request_overrides_still_win_over_nested_fields(self, client, storage):
        await _seed(storage, _quote_booked())

        result = await self._migrate(
            client,
            "DEAL-1",
            product_id="other-product",
            impressions=5,
            flight_start="2027-02-01",
            flight_end="2027-02-02",
        )

        new = result["new_deal"]
        assert (new["product_id"], new["impressions"]) == ("other-product", 5)
        assert (new["flight_start"], new["flight_end"]) == ("2027-02-01", "2027-02-02")

    async def test_flat_fields_still_win_over_nested_ones(self, client, storage):
        await _seed(
            storage,
            {
                **_template(),
                "currency": "GBP",
                "impressions": 7,
                "product": {"product_id": "nested"},
                "terms": {"impressions": 9},
            },
        )

        new = (await self._migrate(client, "DEMO-1"))["new_deal"]

        assert (new["product_id"], new["impressions"], new["currency"]) == (
            _PRODUCT_ID,
            7,
            "GBP",
        )
