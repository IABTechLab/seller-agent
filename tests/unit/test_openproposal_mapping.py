"""Direct GAM → OpenProposal 3.0 line item mapping and its problem analysis."""

import copy

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.interfaces.api.main import app
from ad_seller.interfaces.api.routers.openproposal import (
    GAM_AD_UNIT_EXAMPLES,
    GAM_LINE_ITEM_EXAMPLES,
    GAM_LINE_ITEMS_BATCH_EXAMPLES,
    RICH_LINE_ITEM,
)
from ad_seller.models.openproposal import (
    LINE_ITEM_FIELDS,
    FieldStatus,
    GAMAdUnitIn,
    GAMLineItemIn,
    GAMOrderIn,
    Problem,
    Source,
)
from ad_seller.services import openproposal_service as op

STANDARD = GAM_LINE_ITEM_EXAMPLES["standard_pg_line"]["value"]


def _item(base: dict = None, **overrides) -> GAMLineItemIn:
    data = copy.deepcopy(base or STANDARD["line_item"])
    data.update(overrides)
    return GAMLineItemIn(**data)


def _entry(mapped, field):
    return next(e for e in mapped.field_report if e.gam_field == field)


class TestFieldMap:
    def test_every_rule_names_a_status(self):
        rules = op.GAM_LINE_ITEM_FIELD_MAP + op.GAM_ORDER_FIELD_MAP
        assert len({r.gam_field for r in rules}) == len(rules)
        for rule in rules:
            if rule.status in (FieldStatus.MAPPED, FieldStatus.TRANSFORMED):
                assert rule.op_field, rule.gam_field
            if rule.status == FieldStatus.NO_HOME:
                assert rule.op_field is None, rule.gam_field


class TestStandardLineItem:
    def test_core_fields(self):
        mapped = op.line_item_from_gam_line_item(
            _item(), GAMOrderIn(**STANDARD["order"]), "12345678"
        )
        li = mapped.line_item
        assert li.line_item_id == "gam-li-7202154879"
        assert li.status == "draft"
        assert li.channel == "display"
        assert li.pricing[0].gross_rate == 27.84
        assert li.pricing[0].agreed_rate == 27.84
        assert li.commitment.basis == "units"
        assert li.commitment.amount == 2250000
        assert li.commitment.flight_start == "2026-03-01T00:00:00[America/New_York]"
        assert li.commitment.idempotency_key == "OD-BC9DAEFA"
        assert li.transaction_mechanism.included == ["DIRECT_IO"]
        assert li.formats.available == ["display/300x250", "display/728x90"]
        assert [r.role for r in li.external_references] == ["line_item", "order"]
        assert li.ext["gam"]["priority"] == 8

    def test_provenance_covers_every_field(self):
        mapped = op.line_item_from_gam_line_item(_item())
        assert set(mapped.provenance) == set(LINE_ITEM_FIELDS)
        assert mapped.provenance["pricing"] == Source.AD_SERVER
        assert mapped.provenance["channel"] == Source.INFERRED

    def test_field_report_statuses(self):
        mapped = op.line_item_from_gam_line_item(_item())
        assert _entry(mapped, "costPerUnit").op_field == "pricing[].gross_rate"
        assert _entry(mapped, "priority").status == FieldStatus.NO_HOME
        assert _entry(mapped, "lineItemType").status == FieldStatus.LOSSY
        assert _entry(mapped, "status").status == FieldStatus.MAPPED
        assert "priority" in mapped.unmapped_source_fields

    def test_always_present_problems(self):
        problems = op.line_item_from_gam_line_item(_item()).problems
        assert {Problem.LIFECYCLE, Problem.COMMERCIAL_TERMS, Problem.CHOICE_SPACE} <= set(problems)
        assert Problem.IDENTITY not in problems

    def test_gam_defaults_are_not_reported(self):
        item = _item(
            skippableAdType="DISABLED",
            startDateTimeType="USE_START_DATE_TIME",
            discount=0.0,
            discountType="PERCENTAGE",
            valueCostPerUnit={"currencyCode": "USD", "microAmount": 0},
        )
        fields = {e.gam_field for e in op.line_item_from_gam_line_item(item).field_report}
        assert not fields & {"skippableAdType", "startDateTimeType", "discount", "valueCostPerUnit"}

    def test_unknown_fields_are_reported(self):
        item = _item(someNewField="x")
        entry = _entry(op.line_item_from_gam_line_item(item), "someNewField")
        assert entry.status == FieldStatus.UNKNOWN


class TestProblemCases:
    def test_sponsorship_example_raises_expected_problems(self):
        mapped = op.line_item_from_gam_line_item(
            GAMLineItemIn(**RICH_LINE_ITEM), GAMOrderIn(id=3961795098, currencyCode="USD")
        )
        li = mapped.line_item
        assert li.status == "agreed"
        assert li.hold_status.state == "converted"
        assert li.pricing[0].cost_method == "CPD"
        assert li.pricing[0].gross_rate == 5000.0
        assert li.pricing[0].agreed_rate == 4500.0
        assert li.commitment.amount == 30
        assert li.commitment.flight_end is None
        problems = set(mapped.problems)
        assert {
            Problem.GOAL_SEMANTICS,
            Problem.CARDINALITY,
            Problem.TARGETING,
            Problem.DATETIME,
            Problem.IDENTITY,
            Problem.DELIVERY_SETTINGS,
            Problem.LOSSY_ENUM,
        } <= problems
        gaps = " ".join(mapped.gaps)
        assert "DAILY goal" in gaps
        assert "1 more GAM caps dropped" in gaps
        assert "excluded locations" in gaps
        assert "no end date" in gaps
        assert "targeting.customTargeting" in mapped.unmapped_source_fields

    def test_absolute_discount(self):
        item = _item(discountType="ABSOLUTE_VALUE", discount=2.84)
        assert op.line_item_from_gam_line_item(item).line_item.pricing[0].agreed_rate == 25.0

    def test_budget_alongside_units_is_many_to_one(self):
        item = _item(budget={"currencyCode": "USD", "microAmount": 62640000000})
        entry = _entry(op.line_item_from_gam_line_item(item), "budget")
        assert entry.status == FieldStatus.NO_HOME
        assert entry.problem == Problem.CARDINALITY

    def test_budget_only_becomes_spend(self):
        item = _item(
            primaryGoal={"goalType": "NONE", "unitType": "IMPRESSIONS", "units": -1},
            budget={"currencyCode": "USD", "microAmount": 10000000000},
        )
        commitment = op.line_item_from_gam_line_item(item).line_item.commitment
        assert commitment.basis == "spend"
        assert commitment.amount == 10000.0
        assert commitment.currency == "USD"

    def test_programmatic_order_means_pg(self):
        order = GAMOrderIn(id=1, isProgrammatic=True)
        li = op.line_item_from_gam_line_item(_item(), order).line_item
        assert li.transaction_mechanism.included == ["PROGRAMMATIC_GUARANTEED"]

    def test_video_audio_and_native_channels(self):
        video = op.line_item_from_gam_line_item(_item(environmentType="VIDEO_PLAYER"))
        audio = op.line_item_from_gam_line_item(_item(allowedFormats=["AUDIO"]))
        native = op.line_item_from_gam_line_item(
            _item(
                creativePlaceholders=[
                    {"size": {"width": 1, "height": 1}, "creativeSizeType": "NATIVE"}
                ]
            )
        )
        assert video.line_item.channel == "video"
        assert audio.line_item.channel == "audio"
        assert native.line_item.channel == "native"

    def test_video_duration_becomes_format(self):
        item = _item(
            environmentType="VIDEO_PLAYER", videoMaxDuration=30000, creativePlaceholders=[]
        )
        assert op.line_item_from_gam_line_item(item).line_item.formats.available == [
            "video/instream-30s"
        ]

    @pytest.mark.parametrize("line_type", sorted(op.GAM_NON_SALE_TYPES))
    def test_non_sales_are_unmappable(self, line_type):
        with pytest.raises(op.UnmappableError):
            op.line_item_from_gam_line_item(_item(lineItemType=line_type))


class TestBatch:
    def test_summary_counts(self):
        body = GAM_LINE_ITEMS_BATCH_EXAMPLES["three_line_items"]["value"]
        results, summary = op.map_gam_line_items(
            [GAMLineItemIn(**li) for li in body["line_items"]],
            [GAMOrderIn(**o) for o in body["orders"]],
        )
        assert summary.total == 3
        assert summary.mapped == 2
        assert summary.unmappable_by_type == {"HOUSE": 1}
        assert summary.problems["commercial_terms_missing"] == 2
        assert summary.problems["goal_semantics"] == 1
        assert results[2].error


class TestAdUnit:
    def test_sized_display_unit(self):
        payload = GAM_AD_UNIT_EXAMPLES["display_run_of_site"]["value"]
        li = op.line_item_from_gam_ad_unit(GAMAdUnitIn(**payload["ad_unit"]), "12345678").line_item
        assert li.formats.available == ["display/320x50", "display/728x90", "display/970x250"]
        assert li.properties.available[0].name == "publisher.example › Run-of-Site"

    def test_fluid_unit(self):
        payload = GAM_AD_UNIT_EXAMPLES["fluid"]["value"]
        mapped = op.line_item_from_gam_ad_unit(GAMAdUnitIn(**payload["ad_unit"]))
        assert mapped.line_item.formats.available == ["display/fluid"]
        assert mapped.provenance["formats"] == Source.INFERRED


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")


class TestRoutes:
    async def test_field_map(self, client):
        resp = await client.get("/api/v1/op-gam/line-item/field-map")
        assert resp.status_code == 200
        fields = {r["gam_field"] for r in resp.json()}
        assert {
            "priority",
            "costPerUnit",
            "targeting.customTargeting",
            "order.currencyCode",
        } <= fields

    async def test_line_item_plain_and_explain(self, client):
        plain = await client.post("/api/v1/op-gam/line-item", json=STANDARD)
        assert plain.status_code == 200
        assert plain.json()["line_item_id"] == "gam-li-7202154879"
        assert "field_report" not in plain.json()
        explained = await client.post("/api/v1/op-gam/line-item?explain=true", json=STANDARD)
        body = explained.json()
        assert body["provenance"]["pricing"] == "ad_server"
        assert "priority" in body["unmapped_source_fields"]
        assert "lifecycle_mismatch" in body["problems"]

    async def test_house_line_is_422(self, client):
        resp = await client.post(
            "/api/v1/op-gam/line-item", json=GAM_LINE_ITEM_EXAMPLES["house_line"]["value"]
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "unmappable_line_item"

    async def test_batch(self, client):
        body = GAM_LINE_ITEMS_BATCH_EXAMPLES["three_line_items"]["value"]
        plain = await client.post("/api/v1/op-gam/line-items", json=body)
        assert plain.status_code == 200
        assert plain.json()["summary"]["mapped"] == 2
        assert "line_item" in plain.json()["results"][0]
        assert "mapped" not in plain.json()["results"][0]
        explained = await client.post("/api/v1/op-gam/line-items?explain=true", json=body)
        assert "field_report" in explained.json()["results"][0]["mapped"]

    async def test_ad_unit_examples(self, client):
        for example in GAM_AD_UNIT_EXAMPLES.values():
            resp = await client.post("/api/v1/op-gam/ad-unit", json=example["value"])
            assert resp.status_code == 200

    async def test_seller_agent_op_routes_are_gone(self, client):
        for path in (
            "/op-products",
            "/op-proposals/standard",
            "/api/v1/op-quotes/x",
            "/api/v1/op-deals/x",
        ):
            assert (await client.get(path)).status_code == 404


class TestConventions:
    def test_namespace_and_serving_platform(self):
        li = op.line_item_from_gam_line_item(_item(), None, "12345678").line_item
        assert li.serving_platform == "gam"
        assert {r.namespace for r in li.external_references} == {"network:12345678"}

    def test_deal_id_goes_to_external_references(self):
        li = op.line_item_from_gam_line_item(
            _item(dealInfo={"externalDealId": "131941923"})
        ).line_item
        assert li.deal_id_issuance is None
        assert ("deal", "131941923") in {(r.role, r.id) for r in li.external_references}


def _m2o(**overrides):
    from ad_seller.interfaces.api.routers.openproposal import MANY_TO_ONE_EXAMPLES

    body = copy.deepcopy(MANY_TO_ONE_EXAMPLES["three_ad_servers"]["value"])
    body.update(overrides)
    return body


class TestManyToOne:
    def _build(self, body):
        from ad_seller.models.openproposal import ManyToOneProposalRequest

        return op.proposal_from_many_to_one(ManyToOneProposalRequest(**body))

    def test_sample_builds_one_proposal(self):
        result = self._build(_m2o())
        items = {li.line_item_id: li for li in result.proposal.line_items}
        assert set(items) == {"business-web-display", "business-newsletter-display", "weather-ctv"}
        web = items["business-web-display"]
        assert web.serving_platform == "gam"
        assert [p.id for p in web.properties.available] == ["gam:21780011", "gam:21780019"]
        assert web.properties.included == ["gam:21780011"]
        assert web.properties.pricing == {"gam:21780019": {"delta_cpm": 2.0}}
        assert web.formats.available == ["display/300x250", "display/728x90"]
        assert web.availability.quantity == 6200000
        assert web.deal_id_issuance == "at_commitment"
        assert [r.id for r in web.external_references] == ["21780011", "21780019"]
        assert items["weather-ctv"].channel == "ctv"
        assert items["weather-ctv"].serving_mode == "ssai"

    def test_missing_price_and_forecast_are_gaps(self):
        report = {r.line_item_id: r for r in self._build(_m2o()).report}
        gaps = " ".join(report["weather-ctv"].gaps)
        assert "rate card" in gaps
        assert "one forecast" in gaps

    def test_shared_object_is_one_pool(self):
        body = _m2o()
        dup = copy.deepcopy(body["line_items"][1])
        dup["line_item_id"] = "newsletter-copy"
        body["line_items"].append(dup)
        report = {r.line_item_id: r for r in self._build(body).report}
        assert any("one pool" in g for g in report["newsletter-copy"].gaps)

    @pytest.mark.parametrize(
        "mutate,message",
        [
            (
                lambda b: b["line_items"][0].update(serving_platform="kevel"),
                "cannot serve on kevel",
            ),
            (lambda b: b["line_items"][0].update(included=["nope"]), "unknown object ids"),
            (
                lambda b: (
                    b["line_items"][1]["objects"][0].update(channel="ctv", formats=[])
                    or b["line_items"][1].update(channel="display")
                ),
                "exactly one channel",
            ),
            (lambda b: b["line_items"].append(copy.deepcopy(b["line_items"][2])), "used 2 times"),
            (
                lambda b: b["line_items"][2]["objects"][0].pop("channel"),
                "channel cannot be inferred",
            ),
        ],
    )
    def test_rule_violations(self, mutate, message):
        body = _m2o()
        mutate(body)
        with pytest.raises(op.ManyToOneError) as err:
            self._build(body)
        assert message in str(err.value)


class TestManyToOneRoute:
    async def test_plain_and_explain(self, client):
        plain = await client.post("/api/v1/op-many-to-one/proposal", json=_m2o())
        assert plain.status_code == 200
        assert "report" not in plain.json()
        assert len(plain.json()["proposal"]["line_items"]) == 3
        explained = await client.post("/api/v1/op-many-to-one/proposal?explain=true", json=_m2o())
        assert len(explained.json()["report"]) == 3

    async def test_invalid_example_is_422(self, client):
        from ad_seller.interfaces.api.routers.openproposal import MANY_TO_ONE_EXAMPLES

        resp = await client.post(
            "/api/v1/op-many-to-one/proposal",
            json=MANY_TO_ONE_EXAMPLES["invalid_two_channels"]["value"],
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_many_to_one"
