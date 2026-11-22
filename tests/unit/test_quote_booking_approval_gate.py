"""Quote booking honours the approval gate (upstream issue #129).

``POST /proposals`` holds a high-value decision for a human, but booking a
quote through ``POST /api/v1/deals`` minted the deal directly, so the same
deal could be reached without approval. Booking now applies the same gate
at the price actually booked: a held booking answers 202
``pending_approval`` with an ``approval_id`` and mints nothing; the buyer
re-submits once an operator approves.
"""

import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

_broken_flows = ["ad_seller.flows.execution_activation_flow"]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.config import get_settings  # noqa: E402
from ad_seller.events.approval import ApprovalGate  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402

QUOTE_ID = "qt-gate000001"


def _quote(final_cpm=40.0, impressions=10_000_000):
    return {
        "quote_id": QUOTE_ID,
        "status": "available",
        "deal_type": "PG",
        "product": {"product_id": "ctv-1", "name": "CTV", "inventory_type": "ctv"},
        "pricing": {
            "base_cpm": 45.0,
            "tier_discount_pct": 10.0,
            "volume_discount_pct": 0.0,
            "final_cpm": final_cpm,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base price: $45.00 CPM",
        },
        "terms": {
            "impressions": impressions,
            "flight_start": "2027-01-01",
            "flight_end": "2027-01-31",
            "guaranteed": True,
        },
        "buyer_tier": "agency",
        "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


@pytest.fixture
def storage():
    store = {}
    s = AsyncMock()
    s.get = AsyncMock(side_effect=lambda k: store.get(k))
    s.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    s.get_quote = AsyncMock(side_effect=lambda q: store.get(f"quote:{q}"))
    s.set_quote = AsyncMock(side_effect=lambda q, d, ttl=86400: store.__setitem__(f"quote:{q}", d))
    s.get_deal = AsyncMock(side_effect=lambda d: store.get(f"deal:{d}"))
    s.set_deal = AsyncMock(side_effect=lambda d, data: store.__setitem__(f"deal:{d}", data))
    s.get_negotiation = AsyncMock(side_effect=lambda k: store.get(f"negotiation:{k}"))
    s._store = store
    store[f"quote:{QUOTE_ID}"] = _quote()
    return s


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(
        identity=BuyerIdentity(agency_id="agency-1")
    )
    yield httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


def _settings(**overrides):
    return get_settings().model_copy(update=overrides)


async def _book(client, storage, settings, key="idem-book-1"):
    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.config.get_settings", return_value=settings),
        patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
    ):
        async with client as c:
            return await c.post(
                "/api/v1/deals", json={"idempotency_key": key, "quote_id": QUOTE_ID}
            )


def _fresh(client):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestThresholdGate:
    async def test_high_value_booking_is_held_for_approval(self, client, storage):
        resp = await _book(client, storage, _settings(approval_required_above_value=250_000))

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending_approval"
        assert body["approval_id"]
        assert not any(k.startswith("deal:") for k in storage._store)
        assert storage._store[f"quote:{QUOTE_ID}"]["status"] == "available"
        approval = storage._store[f"approval:{body['approval_id']}"]
        assert approval["flow_type"] == "deal_booking"
        assert approval["context"]["gross_value"] == 400_000.0

    async def test_resubmit_while_pending_reuses_the_request(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        first = (await _book(client, storage, settings)).json()
        second = (await _book(_fresh(client), storage, settings)).json()

        assert second["approval_id"] == first["approval_id"]
        assert storage._store["approval_index:pending"] == [first["approval_id"]]

    async def test_approved_booking_books_on_resubmit(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = (await _book(client, storage, settings)).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")

        resp = await _book(_fresh(client), storage, settings)

        assert resp.status_code == 200, resp.text
        assert resp.json()["deal"]["deal_id"].startswith("DEMO-")
        assert storage._store[f"quote:{QUOTE_ID}"]["status"] == "booked"

    async def test_rejected_booking_is_refused(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = (await _book(client, storage, settings)).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "reject", "operator")

        resp = await _book(_fresh(client), storage, settings)

        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "booking_rejected"

    async def test_approval_for_other_terms_does_not_carry_over(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = (await _book(client, storage, settings)).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")
        storage._store[f"quote:{QUOTE_ID}"] = _quote(final_cpm=38.0)

        resp = await _book(_fresh(client), storage, settings)

        assert resp.status_code == 202
        assert resp.json()["approval_id"] != held["approval_id"]

    async def test_below_threshold_books_immediately(self, client, storage):
        storage._store[f"quote:{QUOTE_ID}"] = _quote(impressions=1_000_000)

        resp = await _book(client, storage, _settings(approval_required_above_value=250_000))

        assert resp.status_code == 200, resp.text

    async def test_threshold_disabled_books_immediately(self, client, storage):
        resp = await _book(client, storage, _settings(approval_required_above_value=0.0))

        assert resp.status_code == 200, resp.text


class TestOptInGate:
    async def test_proposal_led_negotiated_booking_is_gated(self, client, storage):
        storage._store[f"quote:{QUOTE_ID}"] = _quote(impressions=100_000)
        storage._store[f"negotiation_by_quote:{QUOTE_ID}"] = "prop-1"
        storage._store["negotiation:prop-1"] = {
            "negotiation_id": "neg-1",
            "proposal_id": "prop-1",
            "status": "accepted",
            "rounds": [{"seller_price": 40.25}],
        }
        settings = _settings(
            approval_required_above_value=0.0,
            approval_gate_enabled=True,
            approval_required_flows="proposal_decision",
        )

        resp = await _book(client, storage, settings)

        assert resp.status_code == 202, resp.text
        approval = storage._store[f"approval:{resp.json()['approval_id']}"]
        assert approval["context"]["proposal_id"] == "prop-1"
        assert approval["context"]["terms"]["final_cpm"] == 40.25

    async def test_operator_resume_reports_the_decision(self, client, storage):
        from ad_seller.services import approval_service

        held = (
            await _book(client, storage, _settings(approval_required_above_value=250_000))
        ).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            result = await approval_service.resume_flow(held["approval_id"])

        assert result == {
            "quote_id": QUOTE_ID,
            "status": "approved",
            "resumed_from_approval": held["approval_id"],
        }
