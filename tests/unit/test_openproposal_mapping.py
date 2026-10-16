"""OpenProposal 3.0 mapping of catalog products, booked deals and GAM payloads."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.interfaces.api import deps
from ad_seller.interfaces.api.main import app
from ad_seller.interfaces.api.routers.openproposal import (
    GAM_AD_UNIT_EXAMPLES,
    GAM_LINE_ITEM_EXAMPLES,
)
from ad_seller.models.core import DealType
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.openproposal import (
    LINE_ITEM_FIELDS,
    GAMAdUnitIn,
    GAMLineItemIn,
    GAMOrderIn,
    Source,
)
from ad_seller.services import openproposal_service as op


def _product(**overrides) -> ProductDefinition:
    data = {
        "product_id": "ctv-premium",
        "name": "CTV Premium",
        "description": "Premium streaming",
        "inventory_type": "ctv",
        "supported_deal_types": [DealType.PROGRAMMATIC_GUARANTEED, DealType.PREFERRED_DEAL],
        "base_cpm": 30.0,
        "floor_cpm": 25.5,
        "minimum_impressions": 10000,
        "maximum_impressions": 5000000,
    }
    data.update(overrides)
    return ProductDefinition(**data)


def _gam_line_item(**overrides) -> dict:
    data = dict(GAM_LINE_ITEM_EXAMPLES["standard_pg_line"]["value"]["line_item"])
    data.update(overrides)
    return data


def _stored_deal(status: str = "proposed") -> dict:
    return {
        "deal_id": "DEMO-0ADC560536FE",
        "deal_type": "PD",
        "status": status,
        "quote_id": "qt-0b9fd49d24f7",
        "product": {"product_id": "ctv-premium", "name": "CTV Premium", "inventory_type": "ctv"},
        "pricing": {"base_cpm": 45.0, "final_cpm": 38.25, "currency": "USD"},
        "terms": {"impressions": 500000, "flight_start": "2026-11-01", "flight_end": "2026-11-30"},
        "buyer_tier": "advertiser",
        "openrtb_params": {
            "id": "DEMO-0ADC560536FE",
            "bidfloor": 38.25,
            "at": 1,
            "wseat": ["seat-1"],
        },
    }


class TestProductMapping:
    def test_maps_identity_pricing_and_mechanisms(self):
        result = op.line_item_from_product(_product())
        li = result.line_item
        assert li.line_item_id == "ctv-premium"
        assert li.status == "published"
        assert li.channel == "ctv"
        assert li.pricing[0].gross_rate == 30.0
        assert li.transaction_mechanism.available == ["PROGRAMMATIC_GUARANTEED", "PREFERRED_DEAL"]
        assert li.commitment_bounds.max_impressions == 5000000

    def test_floor_is_never_disclosed(self):
        li = op.line_item_from_product(_product()).line_item
        assert li.pricing[0].floor is None
        assert "25.5" not in li.model_dump_json()

    def test_provenance_covers_every_field(self):
        result = op.line_item_from_product(_product())
        assert set(result.provenance) == set(LINE_ITEM_FIELDS)
        assert result.provenance["pricing"] == Source.SELLER_CATALOG
        assert result.provenance["formats"] == Source.NOT_AVAILABLE

    def test_linear_tv_is_flagged(self):
        result = op.line_item_from_product(_product(inventory_type="linear_tv"))
        assert result.line_item.channel == "video"
        assert result.line_item.ext == {"seller_agent": {"inventory_type": "linear_tv"}}
        assert any(g.startswith("channel:") for g in result.gaps)

    def test_unpriced_product_has_no_pricing(self):
        result = op.line_item_from_product(_product(base_cpm=None, floor_cpm=None))
        assert result.line_item.pricing == []
        assert "pricing: product is unpriced" in result.gaps


class TestGAMAdUnitMapping:
    def test_sized_display_unit(self):
        payload = GAM_AD_UNIT_EXAMPLES["display_run_of_site"]["value"]
        result = op.line_item_from_gam_ad_unit(GAMAdUnitIn(**payload["ad_unit"]), "12345678")
        li = result.line_item
        assert li.line_item_id == "gam-au-54545842"
        assert li.channel == "display"
        assert li.formats.available == ["display/320x50", "display/728x90", "display/970x250"]
        assert li.properties.available[0].name == "publisher.example › Run-of-Site"
        assert li.external_references[0].namespace == "12345678"
        assert result.provenance["formats"] == Source.AD_SERVER
        assert result.provenance["pricing"] == Source.NOT_AVAILABLE
        assert any(g.startswith("properties.domains") for g in result.gaps)

    def test_fluid_unit_gets_inferred_format(self):
        payload = GAM_AD_UNIT_EXAMPLES["fluid"]["value"]
        result = op.line_item_from_gam_ad_unit(GAMAdUnitIn(**payload["ad_unit"]))
        assert result.line_item.formats.available == ["display/fluid"]
        assert result.provenance["formats"] == Source.INFERRED

    def test_audio_and_video_and_app_channels(self):
        audio = GAMAdUnitIn(
            id="1",
            name="Podcast",
            adUnitSizes=[{"size": {"width": 1, "height": 1}, "isAudio": True}],
        )
        video = GAMAdUnitIn(
            id="2",
            name="Player",
            adUnitSizes=[
                {"size": {"width": 640, "height": 480}, "environmentType": "VIDEO_PLAYER"}
            ],
        )
        app_unit = GAMAdUnitIn(id="3", name="App banner", applicationId=99)
        assert op.line_item_from_gam_ad_unit(audio).line_item.channel == "audio"
        assert op.line_item_from_gam_ad_unit(video).line_item.channel == "video"
        app_li = op.line_item_from_gam_ad_unit(app_unit).line_item
        assert app_li.environments.available == ["app_mobile"]
        assert app_li.properties.available[0].apps == [{"gam_application_id": 99}]


class TestGAMLineItemMapping:
    def test_standard_line_item(self):
        payload = GAM_LINE_ITEM_EXAMPLES["standard_pg_line"]["value"]
        result = op.line_item_from_gam_line_item(
            GAMLineItemIn(**payload["line_item"]), GAMOrderIn(**payload["order"]), "12345678"
        )
        li = result.line_item
        assert li.pricing[0].agreed_rate == 27.84
        assert li.commitment.amount == 2250000
        assert li.commitment.basis == "units"
        assert li.commitment.flight_start == "2026-03-01T00:00:00[America/New_York]"
        assert li.commitment.idempotency_key == "OD-BC9DAEFA"
        assert li.transaction_mechanism.included == ["DIRECT_IO"]
        assert li.formats.available == ["display/300x250", "display/728x90"]
        assert [r.role for r in li.external_references] == ["line_item", "order"]
        assert li.ext["gam"]["priority"] == 8
        assert "priority" in result.unmapped_source_fields

    @pytest.mark.parametrize("line_type", ["HOUSE", "NETWORK", "BULK", "ADSENSE"])
    def test_non_sales_are_unmappable(self, line_type):
        with pytest.raises(op.UnmappableError):
            op.line_item_from_gam_line_item(GAMLineItemIn(**_gam_line_item(lineItemType=line_type)))

    def test_delivering_collapses_to_agreed_with_gap(self):
        item = GAMLineItemIn(**_gam_line_item(status="DELIVERING", reservationStatus="RESERVED"))
        result = op.line_item_from_gam_line_item(item)
        assert result.line_item.status == "agreed"
        assert result.line_item.hold_status.state == "converted"
        assert any(g.startswith("status:") for g in result.gaps)

    def test_programmatic_order_means_pg(self):
        item = GAMLineItemIn(**_gam_line_item())
        order = GAMOrderIn(id=1, isProgrammatic=True)
        li = op.line_item_from_gam_line_item(item, order).line_item
        assert li.transaction_mechanism.included == ["PROGRAMMATIC_GUARANTEED"]

    def test_daily_goal_and_missing_external_id_are_gaps(self):
        item = GAMLineItemIn(
            **_gam_line_item(
                externalId=None,
                primaryGoal={"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 1000},
            )
        )
        gaps = op.line_item_from_gam_line_item(item).gaps
        assert any("DAILY" in g for g in gaps)
        assert any(g.startswith("commitment.idempotency_key") for g in gaps)


class TestDealMapping:
    def test_booked_deal_is_agreed(self):
        from ad_seller.interfaces.api import contract_mappers as cm

        result = op.line_item_from_deal(cm.internal_deal_to_shared_deal(_stored_deal()))
        li = result.line_item
        assert li.status == "agreed"
        assert li.pricing[0].agreed_rate == 38.25
        assert li.pricing[0].gross_rate == 45.0
        assert li.commitment.amount == 500000
        assert li.deal_id_issuance == "DEMO-0ADC560536FE"
        assert li.transaction_mechanism.included == ["PREFERRED_DEAL"]
        assert li.buyer_requirements == {"dsp_seat_id": ["seat-1"]}
        assert "buyer_tier" in result.unmapped_source_fields

    def test_cancelled_deal_is_withdrawn(self):
        from ad_seller.interfaces.api import contract_mappers as cm

        deal = cm.internal_deal_to_shared_deal(_stored_deal(status="cancelled"))
        assert op.line_item_from_deal(deal).line_item.status == "withdrawn"


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _catalog():
    return {"products": {"ctv-premium": _product()}, "inventory_types": ["ctv"]}


def _quote_response():
    from iab_agentic_primitives.protocol import QuoteResponse

    return QuoteResponse.model_validate(
        {
            "quote": {
                "quote_id": "qt-1",
                "status": "available",
                "deal_type": "PD",
                "product": {
                    "product_id": "ctv-premium",
                    "name": "CTV Premium",
                    "inventory_type": "ctv",
                },
                "pricing": {
                    "base_cpm": {"amount_micros": 45000000, "currency": "USD"},
                    "final_cpm": {"amount_micros": 38250000, "currency": "USD"},
                    "tier_discount_pct": 15.0,
                },
                "terms": {
                    "impressions": 500000,
                    "flight_start": "2026-11-01",
                    "flight_end": "2026-11-30",
                },
                "availability": {"inventory_available": True},
                "buyer_tier": "advertiser",
                "expires_at": "2026-10-17T00:00:00Z",
            }
        }
    )


def _quote_body(**overrides):
    body = {
        "idempotency_key": "op-q1",
        "line_item_id": "ctv-premium",
        "transaction_mechanism": "PREFERRED_DEAL",
        "commitment": {"amount": 500000, "flight_start": "2026-11-01", "flight_end": "2026-11-30"},
        "requested_rate": 40.0,
        "buyer_identity": {"advertiser_id": "adv-1"},
    }
    body.update(overrides)
    return body


class TestRoutes:
    async def test_op_products_list_and_explain(self, client):
        with patch.object(deps, "get_product_catalog", AsyncMock(return_value=_catalog())):
            plain = await client.get("/op-products")
            explained = await client.get("/op-products?explain=true")
        assert plain.status_code == 200
        assert plain.json()[0]["line_item_id"] == "ctv-premium"
        assert "provenance" not in plain.json()[0]
        assert explained.json()[0]["provenance"]["pricing"] == "seller_catalog"

    async def test_op_product_404(self, client):
        with patch.object(deps, "get_product_catalog", AsyncMock(return_value=_catalog())):
            resp = await client.get("/op-products/nope")
        assert resp.status_code == 404

    async def test_standard_proposal(self, client):
        with patch.object(deps, "get_product_catalog", AsyncMock(return_value=_catalog())):
            resp = await client.get("/op-proposals/standard")
        assert resp.status_code == 200
        assert resp.json()["type"] == "standard"
        assert resp.json()["line_items"][0]["line_item_id"] == "ctv-premium"

    async def test_op_quote_delegates_to_quote_handler(self, client):
        from ad_seller.interfaces.api.routers import openproposal as routes

        create = AsyncMock(return_value=_quote_response())
        with (
            patch.object(deps, "get_product_catalog", AsyncMock(return_value=_catalog())),
            patch.object(routes.quotes_routes, "create_quote", create),
        ):
            resp = await client.post("/api/v1/op-quotes", json=_quote_body())
        assert resp.status_code == 200
        sent = create.call_args.kwargs["request"]
        assert sent.product_id == "ctv-premium"
        assert sent.deal_type.value == "PD"
        assert sent.impressions == 500000
        assert sent.target_cpm.amount_micros == 40000000
        assert sent.buyer_identity.advertiser_id == "adv-1"
        body = resp.json()
        assert body["quote_id"] == "qt-1"
        assert body["line_item"]["status"] == "under_review"
        assert body["line_item"]["pricing"][0]["agreed_rate"] == 38.25
        assert body["line_item"]["transaction_mechanism"]["included"] == ["PREFERRED_DEAL"]

    @pytest.mark.parametrize("mechanism", ["DIRECT_IO", "OPEN_AUCTION"])
    async def test_op_quote_rejects_unquotable_mechanism(self, client, mechanism):
        resp = await client.post(
            "/api/v1/op-quotes", json=_quote_body(transaction_mechanism=mechanism)
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "unsupported_transaction_mechanism"

    async def test_op_quote_rejects_spend_basis(self, client):
        body = _quote_body(commitment={"basis": "spend", "amount": 1000})
        resp = await client.post("/api/v1/op-quotes", json=body)
        assert resp.status_code == 422

    async def test_op_deal_delegates_to_booking_handler(self, client):
        from iab_agentic_primitives.protocol import DealBookingResponse

        from ad_seller.interfaces.api import contract_mappers as cm
        from ad_seller.interfaces.api.routers import openproposal as routes

        booked = DealBookingResponse(deal=cm.internal_deal_to_shared_deal(_stored_deal()))
        book = AsyncMock(return_value=booked)
        with patch.object(routes.deals_routes, "book_deal", book):
            resp = await client.post(
                "/api/v1/op-deals", json={"idempotency_key": "op-d1", "quote_id": "qt-1"}
            )
        assert resp.status_code == 200
        assert book.call_args.kwargs["request"].quote_id == "qt-1"
        assert resp.json()["deal_id"] == "DEMO-0ADC560536FE"
        assert resp.json()["line_item"]["status"] == "agreed"
        assert resp.json()["line_item"]["deal_id_issuance"] == "DEMO-0ADC560536FE"

    async def test_op_deal_booking_requires_key(self, client):
        resp = await client.post(
            "/api/v1/op-deals", json={"idempotency_key": "op-d1", "quote_id": "qt-1"}
        )
        assert resp.status_code == 401

    async def test_op_deal_read_requires_key(self, client):
        resp = await client.get("/api/v1/op-deals/DEMO-1")
        assert resp.status_code == 401

    async def test_op_deal_read_maps_stored_deal(self, client):
        app.dependency_overrides[deps._get_optional_api_key_record] = lambda: MagicMock()
        storage = AsyncMock()
        storage.get_deal = AsyncMock(return_value=_stored_deal())
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            resp = await client.get("/api/v1/op-deals/DEMO-0ADC560536FE")
        assert resp.status_code == 200
        assert resp.json()["line_item"]["status"] == "agreed"

    async def test_gam_examples_round_trip(self, client):
        for example in GAM_AD_UNIT_EXAMPLES.values():
            resp = await client.post("/api/v1/op-gam/ad-unit", json=example["value"])
            assert resp.status_code == 200
        ok = await client.post(
            "/api/v1/op-gam/line-item",
            json=GAM_LINE_ITEM_EXAMPLES["standard_pg_line"]["value"],
        )
        assert ok.status_code == 200
        assert ok.json()["line_item_id"] == "gam-li-7202154879"
        assert "provenance" not in ok.json()
        explained = await client.post(
            "/api/v1/op-gam/line-item?explain=true",
            json=GAM_LINE_ITEM_EXAMPLES["standard_pg_line"]["value"],
        )
        assert explained.json()["provenance"]["pricing"] == "ad_server"
        assert "priority" in explained.json()["unmapped_source_fields"]
        house = await client.post(
            "/api/v1/op-gam/line-item",
            json=GAM_LINE_ITEM_EXAMPLES["house_line"]["value"],
        )
        assert house.status_code == 422
        assert house.json()["detail"]["error"] == "unmappable_line_item"

    async def test_existing_routes_are_unchanged(self, client):
        with patch.object(deps, "get_product_catalog", AsyncMock(return_value=_catalog())):
            resp = await client.get("/products")
        assert resp.status_code == 200
        assert set(resp.json()) >= {"products", "total_count"}


class TestQuoteMapping:
    def test_quote_is_composed_line_item(self):
        result = op.line_item_from_quote(_quote_response().quote)
        li = result.line_item
        assert li.status == "under_review"
        assert li.pricing[0].price_valid_until.startswith("2026-10-17")
        assert li.availability.type == "reservable"
        assert li.commitment.amount == 500000
        assert li.ext["seller_agent"]["tier_discount_pct"] == 15.0
        assert result.provenance["pricing"] == Source.QUOTE
