"""Quote booking honours the approval gate (upstream issue #129).

``POST /proposals`` holds a high-value decision for a human, but booking a
quote through ``POST /api/v1/deals`` minted the deal directly, so the same
deal could be reached without approval. Booking now applies the same gate
at the price actually booked: a held booking answers 202
``pending_approval`` with an ``approval_id`` and mints nothing; the buyer
re-submits once an operator approves.
"""

import json
import sys
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace
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


async def _decide(storage, approval_id, body):
    from ad_seller.auth.dependencies import require_operator_key

    app.dependency_overrides[require_operator_key] = lambda: SimpleNamespace(
        key_id="op-1", identity=None
    )
    try:
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                return await c.post(f"/approvals/{approval_id}/decide", json=body)
    finally:
        app.dependency_overrides.pop(require_operator_key, None)


class TestBookingApprovalModifications:
    async def test_approving_with_modifications_is_rejected(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = (await _book(client, storage, settings)).json()

        resp = await _decide(
            storage,
            held["approval_id"],
            {"decision": "approve", "modifications": {"final_cpm": 20.0, "impressions": 10}},
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"] == "modifications_not_supported"
        assert storage._store[f"approval:{held['approval_id']}"]["status"] == "pending"

    async def test_plain_approval_still_books(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = (await _book(client, storage, settings)).json()

        resp = await _decide(storage, held["approval_id"], {"decision": "approve"})
        booked = await _book(_fresh(client), storage, settings)

        assert resp.status_code == 200, resp.text
        assert booked.status_code == 200, booked.text


def _proposal_led_negotiation(storage):
    storage._store[f"quote:{QUOTE_ID}"] = _quote(impressions=100_000)
    storage._store[f"negotiation_by_quote:{QUOTE_ID}"] = "prop-1"
    storage._store["negotiation:prop-1"] = {
        "negotiation_id": "neg-1",
        "proposal_id": "prop-1",
        "status": "accepted",
        "rounds": [{"seller_price": 40.25}],
    }


async def _proposal_approval(storage, decision=None):
    gate = ApprovalGate(storage)
    approval = await gate.request_approval(
        flow_id="flow-prop-1",
        flow_type="proposal_handling",
        gate_name="proposal_decision",
        context={"proposal_id": "prop-1"},
        flow_state_snapshot={},
        proposal_id="prop-1",
    )
    storage._store["approval_index:proposal:prop-1"] = approval.approval_id
    if decision:
        await gate.submit_decision(approval.approval_id, decision, "operator")
    return approval.approval_id


_PROPOSAL_GATE = dict(
    approval_required_above_value=0.0,
    approval_gate_enabled=True,
    approval_required_flows="proposal_decision",
)


class TestRejectedProposal:
    async def test_submit_proposal_records_its_approval(self, storage):
        from ad_seller.services import negotiation_service

        flow = MagicMock()
        flow.handle_proposal_async = AsyncMock(
            return_value={
                "recommendation": "accept",
                "status": "pending_approval",
                "pending_approval": True,
                "flow_id": "flow-1",
                "_flow_state_snapshot": {},
            }
        )
        verification = MagicMock(pricing_verified=True, reason="ok")
        history = MagicMock(verify_pricing=AsyncMock(return_value=verification))
        request = MagicMock(product_id="ctv-1", price=40.0, impressions=100_000)
        context = MagicMock(get_pricing_key=MagicMock(return_value="agency-1"))
        with (
            patch("ad_seller.flows.ProposalHandlingFlow", return_value=flow),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.storage.quote_history.QuoteHistoryStore", return_value=history),
        ):
            result = await negotiation_service.submit_proposal(request, context, {"products": {}})

        assert result["status"] == "pending_approval"
        key = f"approval_index:proposal:{result['proposal_id']}"
        assert storage._store[key] == result["approval_id"]

    @pytest.mark.parametrize(
        "gate",
        [_PROPOSAL_GATE, dict(approval_required_above_value=0.0)],
        ids=["proposal_gate_on", "booking_not_gated"],
    )
    async def test_booking_a_rejected_proposal_is_refused(self, client, storage, gate):
        _proposal_led_negotiation(storage)
        await _proposal_approval(storage, "reject")

        resp = await _book(client, storage, _settings(**gate))

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "proposal_rejected"
        assert f"approval_index:quote_booking:{QUOTE_ID}" not in storage._store
        assert _deals(storage) == []

    async def test_bulk_create_of_a_rejected_proposal_is_refused(self, client, storage):
        _proposal_led_negotiation(storage)
        await _proposal_approval(storage, "reject")

        result = await _bulk_rest(client, storage, _settings(**_PROPOSAL_GATE))

        assert result["success"] is False
        assert "proposal_rejected" in result["error"]
        assert _deals(storage) == []

    @pytest.mark.parametrize("decision", [None, "approve"], ids=["pending", "approved"])
    async def test_open_or_approved_proposal_still_gates_the_booking(
        self, client, storage, decision
    ):
        _proposal_led_negotiation(storage)
        proposal_approval_id = await _proposal_approval(storage, decision)

        resp = await _book(client, storage, _settings(**_PROPOSAL_GATE))

        assert resp.status_code == 202, resp.text
        assert resp.json()["approval_id"] != proposal_approval_id


def _deals(storage):
    return [k for k in storage._store if k.startswith("deal:")]


async def _bulk_rest(client, storage, settings):
    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.config.get_settings", return_value=settings),
        patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
    ):
        async with client as c:
            resp = await c.post(
                "/api/v1/deals/bulk",
                json={"operations": [{"action": "create", "quote_id": QUOTE_ID}]},
            )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _bulk_mcp(storage, settings):
    from ad_seller.interfaces import mcp_server

    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.config.get_settings", return_value=settings),
        patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
    ):
        out = await mcp_server.bulk_deal_operations(
            json.dumps([{"action": "create", "quote_id": QUOTE_ID}])
        )
    return json.loads(out)["results"][0]


class TestBulkCreateGate:
    async def test_bulk_create_cannot_book_a_held_quote(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = (await _book(client, storage, settings)).json()

        result = await _bulk_rest(_fresh(client), storage, settings)

        assert result["success"] is False
        assert result["status"] == "pending_approval"
        assert result["approval_id"] == held["approval_id"]
        assert _deals(storage) == []
        assert storage._store[f"quote:{QUOTE_ID}"]["status"] == "available"

    async def test_bulk_create_books_after_the_booking_approval(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = (await _book(client, storage, settings)).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")

        result = await _bulk_rest(_fresh(client), storage, settings)

        assert result["success"] is True, result
        assert _deals(storage) == [f"deal:{result['deal_id']}"]
        assert storage._store[f"quote:{QUOTE_ID}"]["status"] == "booked"

    async def test_bulk_create_rejected_booking_is_not_booked(self, client, storage):
        settings = _settings(approval_required_above_value=250_000)
        held = await _bulk_rest(client, storage, settings)
        await ApprovalGate(storage).submit_decision(held["approval_id"], "reject", "operator")

        result = await _bulk_rest(_fresh(client), storage, settings)

        assert result["success"] is False
        assert "booking_rejected" in result["error"]
        assert _deals(storage) == []

    async def test_bulk_create_books_at_the_negotiated_price_it_gated(self, client, storage):
        storage._store[f"negotiation:{QUOTE_ID}"] = {
            "negotiation_id": "neg-1",
            "status": "accepted",
            "rounds": [{"seller_price": 38.0}],
        }
        settings = _settings(approval_required_above_value=250_000)
        held = await _bulk_rest(client, storage, settings)
        approval = storage._store[f"approval:{held['approval_id']}"]
        assert approval["context"]["terms"]["final_cpm"] == 38.0
        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")

        result = await _bulk_rest(_fresh(client), storage, settings)

        assert result["success"] is True, result
        assert storage._store[f"deal:{result['deal_id']}"]["pricing"]["final_cpm"] == 38.0

    async def test_mcp_bulk_create_is_gated_then_books(self, storage):
        settings = _settings(approval_required_above_value=250_000)

        held = await _bulk_mcp(storage, settings)
        assert held["status"] == "pending_approval"
        assert _deals(storage) == []

        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")
        booked = await _bulk_mcp(storage, settings)

        assert booked["success"] is True, booked
        assert _deals(storage) == [f"deal:{booked['deal_id']}"]


TEMPLATE_BODY = {"deal_type": "PG", "product_id": "ctv-premium-sports", "impressions": 1_000_000}


@pytest.fixture
def template_client():
    from ad_seller.models.api_key import ApiKeyRole

    app.dependency_overrides[_get_optional_api_key_record] = lambda: SimpleNamespace(
        key_id="k-test",
        role=ApiKeyRole.BUYER,
        identity=BuyerIdentity(agency_id="agency-1", agency_name="Agency One"),
    )
    yield lambda: httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


def _catalog():
    from tests.unit.test_trust_tier_verification import _mock_catalog

    return _mock_catalog()


async def _template_rest(template_client, storage, settings, body=TEMPLATE_BODY):
    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.config.get_settings", return_value=settings),
        patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_catalog(),
        ),
    ):
        async with template_client() as c:
            return await c.post("/api/v1/deals/from-template", json=body)


class TestFromTemplateGate:
    async def test_high_value_template_booking_is_held(self, template_client, storage):
        settings = _settings(approval_required_above_value=1000)

        resp = await _template_rest(template_client, storage, settings)

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending_approval"
        assert body["approval_id"]
        assert _deals(storage) == []
        approval = storage._store[f"approval:{body['approval_id']}"]
        assert approval["flow_type"] == "deal_booking"

    async def test_resubmit_while_pending_reuses_the_request(self, template_client, storage):
        settings = _settings(approval_required_above_value=1000)
        first = (await _template_rest(template_client, storage, settings)).json()
        second = (await _template_rest(template_client, storage, settings)).json()

        assert second["approval_id"] == first["approval_id"]
        assert storage._store["approval_index:pending"] == [first["approval_id"]]

    async def test_approved_template_books_once(self, template_client, storage):
        settings = _settings(approval_required_above_value=1000)
        held = (await _template_rest(template_client, storage, settings)).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")

        booked = await _template_rest(template_client, storage, settings)
        again = await _template_rest(template_client, storage, settings)

        assert booked.status_code == 201, booked.text
        assert _deals(storage) == [f"deal:{booked.json()['deal_id']}"]
        assert storage._store[f"approval:{held['approval_id']}"]["status"] == "consumed"
        assert again.status_code == 202
        assert again.json()["approval_id"] != held["approval_id"]

    async def test_rejected_template_booking_is_refused(self, template_client, storage):
        settings = _settings(approval_required_above_value=1000)
        held = (await _template_rest(template_client, storage, settings)).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "reject", "operator")

        resp = await _template_rest(template_client, storage, settings)

        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "booking_rejected"
        assert _deals(storage) == []

    async def test_approval_for_other_terms_does_not_carry_over(self, template_client, storage):
        settings = _settings(approval_required_above_value=1000)
        held = (await _template_rest(template_client, storage, settings)).json()
        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")

        resp = await _template_rest(
            template_client, storage, settings, {**TEMPLATE_BODY, "impressions": 2_000_000}
        )

        assert resp.status_code == 202
        assert resp.json()["approval_id"] != held["approval_id"]
        assert _deals(storage) == []

    async def test_below_threshold_books_immediately(self, template_client, storage):
        resp = await _template_rest(
            template_client, storage, _settings(approval_required_above_value=1_000_000)
        )

        assert resp.status_code == 201, resp.text

    async def test_mcp_template_tool_is_gated_then_books(self, storage):
        from ad_seller.interfaces import mcp_server

        settings = _settings(approval_required_above_value=1000)

        async def _call():
            with (
                patch("ad_seller.storage.factory.get_storage", return_value=storage),
                patch("ad_seller.config.get_settings", return_value=settings),
                patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
                patch(
                    "ad_seller.services.catalog_service.get_static_product_catalog",
                    AsyncMock(return_value=_catalog()),
                ),
                patch.object(mcp_server.mcp, "get_context", side_effect=LookupError),
            ):
                return json.loads(
                    await mcp_server.create_deal_from_template(
                        deal_type="PG", product_id="ctv-premium-sports", impressions=1_000_000
                    )
                )

        held = await _call()
        assert held["status"] == "pending_approval"
        assert _deals(storage) == []

        await ApprovalGate(storage).submit_decision(held["approval_id"], "approve", "operator")
        booked = await _call()

        assert booked["status"] == "confirmed", booked
        assert _deals(storage) == [f"deal:{booked['deal_id']}"]
