# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Map GAM payloads (API v202608) directly to OpenProposal 3.0 line items.

``GAM_LINE_ITEM_FIELD_MAP`` is the field-by-field contract: every GAM
``LineItem`` field, the OpenProposal field it lands in (if any), how it
lands, and which mapping problem it raises. The mapper builds the line
item and a per-payload field report from that table.
"""

from collections import Counter
from typing import Any, Iterable, Optional

from ..models.openproposal import (
    LINE_ITEM_FIELDS,
    Commitment,
    ExternalReference,
    FieldReportEntry,
    FieldRule,
    FieldStatus,
    FrequencyCap,
    GAMAdUnitIn,
    GAMBatchResult,
    GAMBatchSummary,
    GAMLineItemIn,
    GAMOrderIn,
    HoldState,
    HoldStatus,
    LineItem,
    ManyToOneLineItemIn,
    ManyToOneLineItemReport,
    ManyToOneProposalRequest,
    ManyToOneProposalResponse,
    MappedLineItem,
    PricingEntry,
    Problem,
    Property,
    Proposal,
    Selectable,
    Settable,
    Source,
)


class UnmappableError(ValueError):
    """The GAM object has no OpenProposal meaning (e.g. a house line item)."""


GAM_STATUS_TO_LINE_ITEM_STATUS: dict[str, str] = {
    "DRAFT": "draft",
    "INACTIVE": "draft",
    "PENDING_APPROVAL": "under_review",
    "READY": "agreed",
    "DELIVERING": "agreed",
    "DELIVERY_EXTENDED": "agreed",
    "PAUSED": "agreed",
    "PAUSED_INVENTORY_RELEASED": "agreed",
    "COMPLETED": "expired",
    "CANCELED": "withdrawn",
    "DISAPPROVED": "withdrawn",
}

GAM_LINE_ITEM_TYPE_TO_MECHANISM: dict[str, str] = {
    "STANDARD": "DIRECT_IO",
    "SPONSORSHIP": "DIRECT_IO",
    "PREFERRED_DEAL": "PREFERRED_DEAL",
    "PRICE_PRIORITY": "OPEN_AUCTION",
    "AD_EXCHANGE": "OPEN_AUCTION",
}

GAM_NON_SALE_TYPES = frozenset(
    {"HOUSE", "NETWORK", "BULK", "ADSENSE", "ADMOB", "CLICK_TRACKING", "BUMPER", "LEGACY_DFP"}
)

M, T, L, N, X = (
    FieldStatus.MAPPED,
    FieldStatus.TRANSFORMED,
    FieldStatus.LOSSY,
    FieldStatus.NO_HOME,
    FieldStatus.NOT_APPLICABLE,
)
P = Problem


def _r(gam: str, op: Optional[str], status: FieldStatus, problem: Optional[Problem], note: str):
    return FieldRule(gam_field=gam, op_field=op, status=status, problem=problem, note=note)


GAM_LINE_ITEM_FIELD_MAP: tuple[FieldRule, ...] = (
    _r("id", "line_item_id, external_references[line_item]", T, None, "Prefixed gam-li-."),
    _r("orderId", "external_references[order]", M, None, ""),
    _r("name", "content_detail", L, P.LIFECYCLE, "Trafficker label, not buyer-facing copy."),
    _r(
        "externalId",
        "commitment.idempotency_key, correlation_id",
        M,
        P.IDENTITY,
        "Often empty; without it a commit cannot be replayed safely.",
    ),
    _r("orderName", None, N, None, "Kept in ext.gam."),
    _r(
        "lineItemType",
        "transaction_mechanism",
        L,
        P.LOSSY_ENUM,
        "STANDARD and SPONSORSHIP both become DIRECT_IO; HOUSE, NETWORK, BULK, ADSENSE, "
        "ADMOB, CLICK_TRACKING, BUMPER are not sales and cannot map.",
    ),
    _r(
        "status",
        "status",
        L,
        P.LIFECYCLE,
        "DELIVERING, PAUSED and DELIVERY_EXTENDED become agreed; COMPLETED becomes expired.",
    ),
    _r("isArchived", None, N, P.LIFECYCLE, "OpenProposal has no archived state."),
    _r("reservationStatus", "hold_status.state", T, None, "RESERVED becomes held or converted."),
    _r("reserveAtCreation", "hold_status.hold_available", T, None, ""),
    _r(
        "startDateTime",
        "commitment.flight_start",
        T,
        P.DATETIME,
        "GAM date + time + time zone id; OpenProposal does not define a time zone.",
    ),
    _r("startDateTimeType", None, L, P.DATETIME, "IMMEDIATELY and ONE_HOUR_FROM_NOW are lost."),
    _r("endDateTime", "commitment.flight_end", T, P.DATETIME, ""),
    _r(
        "unlimitedEndDateTime",
        "commitment.flight_end",
        L,
        P.DATETIME,
        "OpenProposal requires flight_end; unlimited lines have none.",
    ),
    _r("autoExtensionDays", None, N, P.DELIVERY_SETTINGS, ""),
    _r(
        "costType",
        "pricing[].cost_method",
        T,
        P.LOSSY_ENUM,
        "The spec does not enumerate cost methods; CPD, CPA and VCPM have no defined meaning.",
    ),
    _r("costPerUnit", "pricing[].gross_rate", T, None, "Micros to decimal."),
    _r(
        "discountType",
        "pricing[].agreed_rate",
        T,
        P.COMMERCIAL_TERMS,
        "agreed_rate is derived from the discount; OpenProposal has no discount field.",
    ),
    _r("discount", "pricing[].agreed_rate", T, P.COMMERCIAL_TERMS, ""),
    _r("valueCostPerUnit", None, N, P.COMMERCIAL_TERMS, "Value CPM for remnant ranking."),
    _r(
        "budget",
        "commitment.amount (basis spend)",
        T,
        P.GOAL_SEMANTICS,
        "Used only when no unit goal exists.",
    ),
    _r(
        "contractedUnitsBought",
        "commitment.amount",
        T,
        P.GOAL_SEMANTICS,
        "Used for sponsorships whose goal is a percentage.",
    ),
    _r(
        "primaryGoal",
        "commitment.amount (basis units)",
        L,
        P.GOAL_SEMANTICS,
        "DAILY goals, percentage goals and CLICKS / CONVERSIONS units have no OpenProposal basis.",
    ),
    _r("secondaryGoals", None, N, P.CARDINALITY, "OpenProposal has one commitment."),
    _r(
        "frequencyCaps",
        "frequency_cap",
        L,
        P.CARDINALITY,
        "Only the first cap fits; GAM caps per user, OpenProposal per device, household or listener.",
    ),
    _r(
        "creativePlaceholders",
        "formats",
        T,
        P.FORMATS,
        "Sizes become display/WxH; companions, expected counts and template ids are lost.",
    ),
    _r(
        "environmentType",
        "channel",
        L,
        P.LOSSY_ENUM,
        "BROWSER or VIDEO_PLAYER only; CTV, DOOH and native cannot be told apart.",
    ),
    _r("allowedFormats", "channel", T, None, "AUDIO makes the channel audio."),
    _r(
        "videoMaxDuration",
        "formats",
        T,
        P.FORMATS,
        "Becomes video/instream-<n>s; the format registry has no video ids yet (OQ-4).",
    ),
    _r("skippableAdType", None, N, P.FORMATS, ""),
    _r("companionDeliveryOption", None, N, P.FORMATS, ""),
    _r(
        "dealInfo",
        "external_references[deal]",
        M,
        None,
        "deal_id_issuance only says how ids are minted.",
    ),
    _r("programmaticCreativeSource", None, N, P.DELIVERY_SETTINGS, ""),
    _r("thirdPartyMeasurementSettings", "measurement_source", T, None, "Partner enum to vendor."),
    _r(
        "viewabilityProviderCompanyIds",
        "measurement_source",
        L,
        P.ID_TRANSLATION,
        "Company ids need a lookup to a vendor name.",
    ),
    _r("priority", None, N, P.DELIVERY_SETTINGS, ""),
    _r("deliveryRateType", None, N, P.DELIVERY_SETTINGS, "Pacing."),
    _r("deliveryForecastSource", None, N, P.DELIVERY_SETTINGS, ""),
    _r("customPacingCurve", None, N, P.DELIVERY_SETTINGS, ""),
    _r("creativeRotationType", None, N, P.DELIVERY_SETTINGS, ""),
    _r("roadblockingType", None, N, P.DELIVERY_SETTINGS, ""),
    _r("repeatedCreativeServingEnabled", None, N, P.DELIVERY_SETTINGS, ""),
    _r("deliveryAllocationProfileId", None, N, P.DELIVERY_SETTINGS, ""),
    _r("competitiveConstraintScope", None, N, P.DELIVERY_SETTINGS, ""),
    _r(
        "disableSameAdvertiserCompetitiveExclusion",
        "exclusivity",
        L,
        P.COMMERCIAL_TERMS,
        "OpenProposal exclusivity is a selectable offer term, not an ad-server switch.",
    ),
    _r(
        "appliedLabels", None, N, P.DELIVERY_SETTINGS, "Competitive exclusion and frequency labels."
    ),
    _r("effectiveAppliedLabels", None, X, None, "Derived from appliedLabels."),
    _r("childContentEligibility", None, N, P.COMMERCIAL_TERMS, "Policy; no creative_policy shape."),
    _r("customVastExtension", None, N, P.FORMATS, ""),
    _r("grpSettings", None, N, P.GOAL_SEMANTICS, "GRP goals."),
    _r("activityAssociations", None, N, P.GOAL_SEMANTICS, "CPA conversion tracking."),
    _r("creativeTargetings", None, N, P.TARGETING, ""),
    _r("customFieldValues", None, N, None, "Publisher custom fields; kept in ext.gam."),
    _r("notes", None, N, None, "Kept in ext.gam."),
    _r("webPropertyCode", None, N, None, "AdSense / Ad Exchange property."),
    _r("stats", None, N, P.DELIVERY_DATA, "Delivered impressions and clicks."),
    _r("deliveryIndicator", None, N, P.DELIVERY_DATA, ""),
    _r("deliveryData", None, N, P.DELIVERY_DATA, ""),
    _r("isMissingCreatives", None, N, P.DELIVERY_DATA, ""),
    _r("allowOverbook", None, X, None, "Trafficking flag."),
    _r("skipInventoryCheck", None, X, None, "Trafficking flag."),
    _r("skipCrossSellingRuleWarningChecks", None, X, None, "Trafficking flag."),
    _r("youtubeKidsRestricted", None, X, None, ""),
    _r("lastModifiedByApp", None, X, None, "Metadata."),
    _r("lastModifiedDateTime", None, X, None, "Metadata."),
    _r("creationDateTime", None, X, None, "Metadata."),
    _r(
        "targeting.inventoryTargeting",
        "properties",
        T,
        P.ID_TRANSLATION,
        "Ad unit and placement ids only; names and domains need an ad unit lookup.",
    ),
    _r(
        "targeting.inventoryTargeting.excludedAdUnits",
        None,
        N,
        P.TARGETING,
        "OpenProposal properties cannot express exclusions.",
    ),
    _r(
        "targeting.geoTargeting",
        "geo.allowed",
        T,
        P.ID_TRANSLATION,
        "GAM location ids need translating to ISO 3166 codes.",
    ),
    _r(
        "targeting.geoTargeting.excludedLocations",
        None,
        N,
        P.TARGETING,
        "OpenProposal geo has no exclusion list.",
    ),
    _r(
        "targeting.dayPartTargeting",
        "daypart",
        T,
        P.TARGETING,
        "Copied as-is; the spec does not define the daypart shape.",
    ),
    _r(
        "targeting.technologyTargeting.deviceCategoryTargeting",
        "device",
        T,
        P.ID_TRANSLATION,
        "GAM device category ids need a lookup.",
    ),
    _r(
        "targeting.technologyTargeting",
        None,
        N,
        P.TARGETING,
        "Browser, OS, carrier, bandwidth, manufacturer targeting.",
    ),
    _r(
        "targeting.requestPlatformTargeting",
        "environments",
        T,
        None,
        "BROWSER, MOBILE_APP, VIDEO_PLAYER.",
    ),
    _r(
        "targeting.mobileApplicationTargeting",
        "properties.apps",
        T,
        P.ID_TRANSLATION,
        "Needs a MobileApplicationService lookup for platform and bundle.",
    ),
    _r("targeting.inventoryUrlTargeting", "properties.domains", T, None, ""),
    _r("targeting.customTargeting", None, N, P.TARGETING, "Key-values."),
    _r("targeting.userDomainTargeting", None, N, P.TARGETING, ""),
    _r("targeting.contentTargeting", None, N, P.TARGETING, "Content and video bundles."),
    _r("targeting.videoPositionTargeting", None, N, P.FORMATS, "Pre-roll, mid-roll, post-roll."),
    _r("targeting.buyerUserListTargeting", None, N, P.TARGETING, ""),
    _r("targeting.verticalTargeting", None, N, P.TARGETING, ""),
    _r("targeting.contentLabelTargeting", None, N, P.TARGETING, "Brand-safety labels."),
    _r("targeting.dateTimeRangeTargeting", None, N, P.TARGETING, ""),
    _r("targeting.inventorySizeTargeting", None, N, P.FORMATS, ""),
    _r("targeting.publisherProvidedSignalsTargeting", None, N, P.TARGETING, ""),
)

GAM_ORDER_FIELD_MAP: tuple[FieldRule, ...] = (
    _r("order.id", "external_references[order]", M, None, ""),
    _r("order.currencyCode", "pricing[].currency", M, None, ""),
    _r("order.isProgrammatic", "transaction_mechanism", T, None, "STANDARD becomes PG."),
    _r("order.poNumber", "billing.po_number", T, None, ""),
    _r("order.externalOrderId", "external_references[external_order]", M, None, ""),
    _r("order.advertiserId", None, N, P.ID_TRANSLATION, "Buyer identity is not on a line item."),
    _r("order.agencyId", None, N, P.ID_TRANSLATION, "Buyer identity is not on a line item."),
    _r("order.salespersonId", None, N, None, ""),
    _r("order.traffickerId", None, N, None, ""),
    _r("order.totalBudget", None, N, None, "Order total spans all line items."),
)

ALWAYS_PROBLEMS: dict[Problem, str] = {
    P.LIFECYCLE: "A GAM line item is a sold delivery; an OpenProposal line item is an offer "
    "that becomes agreed. There is no published offer behind it.",
    P.COMMERCIAL_TERMS: "Offer price, floor, price validity, min spend, cancellation, makegood, "
    "eligibility and credentials are not in GAM.",
    P.CHOICE_SPACE: "Every selectable field has exactly one option (the booked value); the "
    "buyer's original choice space is not recorded in GAM.",
}

_RULES_BY_FIELD = {r.gam_field: r for r in GAM_LINE_ITEM_FIELD_MAP + GAM_ORDER_FIELD_MAP}


GAM_DEFAULT_VALUES: dict[str, frozenset] = {
    "skippableAdType": frozenset({"DISABLED", "UNKNOWN"}),
    "companionDeliveryOption": frozenset({"UNKNOWN", "OPTIONAL"}),
    "childContentEligibility": frozenset({"DISALLOWED", "UNKNOWN"}),
    "deliveryForecastSource": frozenset({"HISTORICAL"}),
    "competitiveConstraintScope": frozenset({"POD"}),
    "roadblockingType": frozenset({"ONE_OR_MORE"}),
    "startDateTimeType": frozenset({"USE_START_DATE_TIME"}),
}

LOSSY_GAM_STATUSES = frozenset(
    {
        "DELIVERING",
        "DELIVERY_EXTENDED",
        "PAUSED",
        "PAUSED_INVENTORY_RELEASED",
        "COMPLETED",
        "INACTIVE",
    }
)


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def _zero_money(value: Any) -> bool:
    return isinstance(value, dict) and not value.get("microAmount")


def _is_default(field: str, value: Any, raw: dict[str, Any]) -> bool:
    if value in GAM_DEFAULT_VALUES.get(field, ()):
        return True
    if field in ("valueCostPerUnit", "budget") and _zero_money(value):
        return True
    if field == "discount" and not value:
        return True
    if field == "discountType" and not raw.get("discount"):
        return True
    if field == "stats" and isinstance(value, dict) and not any(value.values()):
        return True
    return False


def _commitment_basis(raw: dict[str, Any]) -> Optional[str]:
    goal = raw.get("primaryGoal") or {}
    if raw.get("contractedUnitsBought"):
        return "units"
    if (goal.get("units") or 0) > 0 and goal.get("goalType") != "NONE":
        return "units"
    if not _zero_money(raw.get("budget")) and raw.get("budget"):
        return "spend"
    return None


def _adjust(
    rule: FieldRule, value: Any, raw: dict[str, Any]
) -> tuple[FieldStatus, Optional[Problem], str]:
    """Value-dependent status: only flag a problem when this value actually loses something."""
    status, problem, note = rule.status, rule.problem, rule.note
    field = rule.gam_field
    if field == "status" and value not in LOSSY_GAM_STATUSES:
        return M, None, ""
    if field == "name":
        return T, None, note
    if field == "costType" and value == "CPM":
        return T, None, "CPM."
    if field == "environmentType" and value == "BROWSER":
        return T, None, "BROWSER becomes display."
    if field == "primaryGoal":
        if (
            value.get("goalType") == "LIFETIME"
            and value.get("unitType") in ("IMPRESSIONS", "VIEWABLE_IMPRESSIONS")
            and (value.get("units") or 0) > 0
        ):
            return T, None, "Lifetime impression goal becomes a units commitment."
        return L, P.GOAL_SEMANTICS, note
    if field == "budget":
        if _commitment_basis(raw) == "spend":
            return T, None, "Budget becomes a spend commitment."
        return N, P.CARDINALITY, "Budget and a unit goal are both set; a commitment has one basis."
    if field == "creativePlaceholders":
        plain = all(
            c.get("creativeSizeType", "PIXEL") == "PIXEL"
            and not c.get("companions")
            and (c.get("expectedCreativeCount") or 1) <= 1
            for c in value
        )
        if plain:
            return T, None, "Pixel sizes become display/WxH."
    return status, problem, note


def _lookup(obj: dict[str, Any], path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _short(value: Any, limit: int = 160) -> Any:
    text = repr(value)
    return value if len(text) <= limit else text[: limit - 1] + "…"


def field_report(item: GAMLineItemIn, order: Optional[GAMOrderIn] = None) -> list[FieldReportEntry]:
    """Every field present on the GAM payload, with where it went in OpenProposal."""
    raw = item.model_dump(exclude_none=True)
    entries: list[FieldReportEntry] = []
    targeting = raw.get("targeting") or {}
    for rule in GAM_LINE_ITEM_FIELD_MAP:
        value = _lookup(raw, rule.gam_field)
        if not _present(value) or _is_default(rule.gam_field, value, raw):
            continue
        if rule.gam_field == "targeting.technologyTargeting":
            value = {k: v for k, v in value.items() if k != "deviceCategoryTargeting" and v}
            if not value:
                continue
        status, problem, note = _adjust(rule, value, raw)
        entries.append(
            FieldReportEntry(
                gam_field=rule.gam_field,
                op_field=rule.op_field,
                status=status,
                problem=problem,
                note=note,
                value=_short(value),
            )
        )
    known_top = {r.gam_field for r in GAM_LINE_ITEM_FIELD_MAP if "." not in r.gam_field}
    known_targeting = {
        r.gam_field.split(".")[1]
        for r in GAM_LINE_ITEM_FIELD_MAP
        if r.gam_field.startswith("targeting.")
    }
    for key, value in raw.items():
        if (
            key not in known_top
            and key != "targeting"
            and _present(value)
            and not _is_default(key, value, raw)
        ):
            entries.append(
                FieldReportEntry(
                    gam_field=key,
                    op_field=None,
                    status=FieldStatus.UNKNOWN,
                    problem=None,
                    note="Not in the GAM v202608 field map.",
                    value=_short(value),
                )
            )
    for key, value in targeting.items():
        if key not in known_targeting and _present(value):
            entries.append(
                FieldReportEntry(
                    gam_field=f"targeting.{key}",
                    op_field=None,
                    status=FieldStatus.UNKNOWN,
                    problem=P.TARGETING,
                    note="Not in the GAM v202608 field map.",
                    value=_short(value),
                )
            )
    if order is not None:
        order_raw = order.model_dump(exclude_none=True)
        for rule in GAM_ORDER_FIELD_MAP:
            value = order_raw.get(rule.gam_field.split(".", 1)[1])
            if _present(value):
                entries.append(FieldReportEntry(**rule.model_dump(), value=_short(value)))
    return entries


class _Builder:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.provenance: dict[str, Source] = {f: Source.NOT_AVAILABLE for f in LINE_ITEM_FIELDS}
        self.gaps: list[str] = []

    def set(self, field: str, value: Any, source: Source = Source.AD_SERVER) -> None:
        if value is None:
            return
        self.values[field] = value
        self.provenance[field] = source

    def gap(self, field: str, reason: str) -> None:
        self.gaps.append(f"{field}: {reason}")


def _one(value: Any) -> Selectable:
    return Selectable(available=[value], included=[value])


def _many(values: list[Any]) -> Selectable:
    return Selectable(available=values, included=[v.id if hasattr(v, "id") else v for v in values])


def _namespace(network_code: Optional[str]) -> Optional[str]:
    if not network_code:
        return None
    return network_code if network_code.startswith("network:") else f"network:{network_code}"


def _gam_datetime(value: Optional[dict[str, Any]]) -> Optional[str]:
    if not value or not value.get("date"):
        return None
    d = value["date"]
    stamp = (
        f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"
        f"T{value.get('hour', 0):02d}:{value.get('minute', 0):02d}:{value.get('second', 0):02d}"
    )
    zone = value.get("timeZoneId")
    return f"{stamp}[{zone}]" if zone else stamp


def _gam_money(value: Optional[dict[str, Any]]) -> tuple[Optional[float], Optional[str]]:
    if not value or value.get("microAmount") is None:
        return None, None
    return int(value["microAmount"]) / 1_000_000, value.get("currencyCode")


def _channel(item: GAMLineItemIn) -> str:
    if "AUDIO" in (item.allowedFormats or []):
        return "audio"
    if any(c.get("creativeSizeType") == "NATIVE" for c in item.creativePlaceholders):
        return "native"
    if item.environmentType == "VIDEO_PLAYER":
        return "video"
    return "display"


def _formats(item: GAMLineItemIn, channel: str) -> list[str]:
    out: list[str] = []
    for c in item.creativePlaceholders:
        size = c.get("size") or {}
        kind = c.get("creativeSizeType")
        if kind == "NATIVE":
            out.append("native/fluid")
        elif kind == "AUDIO":
            out.append("audio/standard")
        elif size.get("width") is not None:
            sep = ":" if size.get("isAspectRatio") or kind == "ASPECT_RATIO" else "x"
            prefix = "video" if channel == "video" else "display"
            out.append(f"{prefix}/{size['width']}{sep}{size['height']}")
    if item.videoMaxDuration:
        out.append(f"video/instream-{int(item.videoMaxDuration) // 1000}s")
    return sorted(set(out))


def line_item_from_gam_line_item(
    item: GAMLineItemIn, order: Optional[GAMOrderIn] = None, network_code: Optional[str] = None
) -> MappedLineItem:
    """Map one GAM line item straight to an OpenProposal line item."""
    if item.lineItemType in GAM_NON_SALE_TYPES:
        raise UnmappableError(
            f"GAM {item.lineItemType} line items are not sales and have no OpenProposal meaning"
        )
    b = _Builder()
    t = item.targeting or {}
    namespace = _namespace(network_code)

    b.set("line_item_id", f"gam-li-{item.id}")
    status = GAM_STATUS_TO_LINE_ITEM_STATUS.get(item.status, "draft")
    b.set("status", status)
    if item.status in ("DELIVERING", "DELIVERY_EXTENDED", "PAUSED", "COMPLETED"):
        b.gap("status", f"GAM {item.status} has no OpenProposal equivalent; mapped to {status}")
    channel = _channel(item)
    b.set("channel", channel, Source.INFERRED)
    if channel == "video":
        b.gap("channel", "VIDEO_PLAYER cannot be told apart from CTV; mapped to video")

    inventory = t.get("inventoryTargeting") or {}
    props = [Property(id=f"gam:{a['adUnitId']}") for a in inventory.get("targetedAdUnits") or []]
    props += [
        Property(id=f"gam-placement:{p}") for p in inventory.get("targetedPlacementIds") or []
    ]
    urls = [u.get("url") for u in (t.get("inventoryUrlTargeting") or {}).get("targetedUrls") or []]
    apps = (t.get("mobileApplicationTargeting") or {}).get("mobileApplicationIds") or []
    if urls and props:
        props[0].domains = [u for u in urls if u]
    if apps:
        app_refs = [{"gam_mobile_application_id": a} for a in apps]
        if props:
            props[0].apps = app_refs
        else:
            props = [Property(id="gam-apps", apps=app_refs)]
    if props:
        b.set("properties", _many(props))
        b.gap("properties", "ad unit ids only; names, domains and sizes need an ad unit lookup")
    else:
        b.gap("properties", "no inventory targeting on the line item")
    if inventory.get("excludedAdUnits"):
        b.gap("properties", "excluded ad units cannot be expressed")
    b.set("content_detail", item.name)

    platforms = (t.get("requestPlatformTargeting") or {}).get("targetedRequestPlatforms") or []
    env_map = {"BROWSER": ["web_desktop", "web_mobile"], "MOBILE_APP": ["app_mobile"]}
    envs = sorted({e for p in platforms for e in env_map.get(p, [])})
    if envs:
        b.set("environments", Selectable(available=envs, included=envs))

    formats = _formats(item, channel)
    if formats:
        b.set("formats", Selectable(available=formats, included=formats))
    else:
        b.gap("formats", "no creative placeholders")

    geo = t.get("geoTargeting") or {}
    locations = [str(loc.get("id")) for loc in geo.get("targetedLocations") or []]
    if locations:
        b.set("geo", Settable(allowed=[f"gam-geo:{loc}" for loc in locations]))
        b.gap("geo", "GAM location ids need translating to ISO 3166 codes")
    if geo.get("excludedLocations"):
        b.gap("geo", "excluded locations cannot be expressed")
    devices = (t.get("technologyTargeting") or {}).get("deviceCategoryTargeting") or {}
    if devices.get("targetedDeviceCategories"):
        b.set(
            "device",
            Settable(
                allowed=[f"gam-device:{d.get('id')}" for d in devices["targetedDeviceCategories"]]
            ),
        )
    if t.get("dayPartTargeting"):
        b.set("daypart", Settable(allowed=[t["dayPartTargeting"]]))
    if item.frequencyCaps:
        cap = item.frequencyCaps[0]
        b.set(
            "frequency_cap",
            FrequencyCap(
                max=Settable(max=cap.get("maxImpressions")),
                period=f"{cap.get('numTimeUnits', 1)} {str(cap.get('timeUnit', '')).lower()}",
            ),
        )
        b.gap(
            "frequency_cap.basis",
            "GAM caps per user; OpenProposal wants device, household or listener",
        )
        if len(item.frequencyCaps) > 1:
            b.gap("frequency_cap", f"{len(item.frequencyCaps) - 1} more GAM caps dropped")

    rate, rate_currency = _gam_money(item.costPerUnit)
    currency = rate_currency or (order.currencyCode if order else None) or "USD"
    agreed = rate
    if rate is not None and item.discount:
        if item.discountType == "ABSOLUTE_VALUE":
            agreed = round(rate - item.discount, 6)
        else:
            agreed = round(rate * (1 - item.discount / 100), 6)
    b.set(
        "pricing",
        [
            PricingEntry(
                cost_method=item.costType, gross_rate=rate, agreed_rate=agreed, currency=currency
            )
        ],
    )
    b.gap("pricing", "floor, seller_rate and price_valid_until are not in GAM")

    goal = item.primaryGoal or {}
    budget, _ = _gam_money(item.budget)
    units = goal.get("units")
    unit_type = goal.get("unitType", "IMPRESSIONS")
    amount = basis = None
    if item.contractedUnitsBought:
        amount, basis = float(item.contractedUnitsBought), "units"
    elif units is not None and int(units) > 0 and goal.get("goalType") != "NONE":
        amount, basis = float(units), "units"
    elif budget:
        amount, basis = budget, "spend"
    if amount is not None:
        b.set(
            "commitment",
            Commitment(
                basis=basis,
                amount=amount,
                currency=currency if basis == "spend" else None,
                flight_start=_gam_datetime(item.startDateTime),
                flight_end=None if item.unlimitedEndDateTime else _gam_datetime(item.endDateTime),
                idempotency_key=item.externalId,
            ),
        )
        if goal.get("goalType") == "DAILY":
            b.gap("commitment", "DAILY goal; OpenProposal commitments are lifetime totals")
        if basis == "units" and unit_type not in ("IMPRESSIONS", "VIEWABLE_IMPRESSIONS"):
            b.gap("commitment", f"goal unit {unit_type} has no OpenProposal basis")
        if item.unlimitedEndDateTime:
            b.gap("commitment.flight_end", "GAM line item has no end date")
        if not item.externalId:
            b.gap("commitment.idempotency_key", "GAM line item has no externalId")
    else:
        b.gap("commitment", "no unit goal, contracted units or budget")

    reserved = item.reservationStatus == "RESERVED"
    state = (
        HoldState.CONVERTED
        if reserved and status == "agreed"
        else (HoldState.HELD if reserved else HoldState.NONE)
    )
    b.set(
        "hold_status",
        HoldStatus(hold_available=bool(item.reserveAtCreation) or reserved, state=state),
    )

    mech = GAM_LINE_ITEM_TYPE_TO_MECHANISM.get(item.lineItemType)
    if order and order.isProgrammatic and mech == "DIRECT_IO":
        mech = "PROGRAMMATIC_GUARANTEED"
    if mech:
        b.set("transaction_mechanism", _one(mech))
    else:
        b.gap("transaction_mechanism", f"no mechanism for GAM type {item.lineItemType}")
    deal_id = None
    if item.dealInfo:
        deal_id = item.dealInfo.get("externalDealId") or item.dealInfo.get("dealId")
    partner = (item.thirdPartyMeasurementSettings or {}).get("viewabilityPartner")
    if partner and partner != "NONE":
        b.set("measurement_source", _one({"vendor": partner}))

    b.set("buying_route", "PUBLISHER_AD_SERVER")
    b.set("serving_platform", "gam")
    refs = [
        ExternalReference(system="gam", namespace=namespace, id=str(item.id), role="line_item"),
        ExternalReference(system="gam", namespace=namespace, id=str(item.orderId), role="order"),
    ]
    if deal_id:
        refs.append(
            ExternalReference(system="gam", namespace=namespace, id=str(deal_id), role="deal")
        )
    if order and order.externalOrderId:
        refs.append(
            ExternalReference(
                system="gam",
                namespace=namespace,
                id=str(order.externalOrderId),
                role="external_order",
            )
        )
    b.set("external_references", refs)
    b.set("correlation_id", item.externalId)
    if order and order.poNumber:
        b.set("billing", {"po_number": order.poNumber})

    report = field_report(item, order)
    no_home = {
        e.gam_field: e.value
        for e in report
        if e.status in (FieldStatus.NO_HOME, FieldStatus.UNKNOWN) and "." not in e.gam_field
    }
    ext_gam: dict[str, Any] = {
        "line_item_type": item.lineItemType,
        "status": item.status,
        **no_home,
    }
    if order is not None:
        ext_gam["order"] = {
            k: v
            for k, v in order.model_dump(exclude_none=True).items()
            if k in ("advertiserId", "agencyId", "name")
        }
    b.values["ext"] = {"gam": ext_gam}

    raised = {e.problem for e in report if e.problem and e.status != FieldStatus.MAPPED}
    if not item.externalId:
        raised.add(P.IDENTITY)
    problems = sorted(
        raised | set(ALWAYS_PROBLEMS),
        key=lambda p: p.value,
    )
    return MappedLineItem(
        line_item=LineItem(**b.values),
        provenance=b.provenance,
        gaps=b.gaps,
        unmapped_source_fields=[
            e.gam_field for e in report if e.status in (FieldStatus.NO_HOME, FieldStatus.UNKNOWN)
        ],
        field_report=report,
        problems=problems,
    )


def map_gam_line_items(
    items: Iterable[GAMLineItemIn],
    orders: Iterable[GAMOrderIn] = (),
    network_code: Optional[str] = None,
) -> tuple[list[GAMBatchResult], GAMBatchSummary]:
    """Map many GAM line items and summarise the problems across them."""
    order_by_id = {str(o.id): o for o in orders}
    results: list[GAMBatchResult] = []
    unmappable: Counter = Counter()
    problems: Counter = Counter()
    field_status: Counter = Counter()
    gap_fields: Counter = Counter()
    total = 0
    for item in items:
        total += 1
        try:
            mapped = line_item_from_gam_line_item(
                item, order_by_id.get(str(item.orderId)), network_code
            )
        except UnmappableError as exc:
            unmappable[item.lineItemType] += 1
            results.append(GAMBatchResult(gam_line_item_id=str(item.id), error=str(exc)))
            continue
        problems.update(p.value for p in mapped.problems)
        field_status.update(e.status.value for e in mapped.field_report)
        gap_fields.update(g.split(":")[0] for g in mapped.gaps)
        results.append(GAMBatchResult(gam_line_item_id=str(item.id), mapped=mapped))
    mapped_count = total - sum(unmappable.values())
    summary = GAMBatchSummary(
        total=total,
        mapped=mapped_count,
        unmappable_by_type=dict(unmappable),
        problems={k: v for k, v in problems.most_common()},
        field_status_counts=dict(field_status),
        gap_counts={k: v for k, v in gap_fields.most_common()},
    )
    return results, summary


def _ad_unit_path(unit: GAMAdUnitIn) -> str:
    parents = [p.get("name", "") for p in unit.parentPath[1:]]
    return " › ".join([*parents, unit.name])


def line_item_from_gam_ad_unit(
    unit: GAMAdUnitIn, network_code: Optional[str] = None
) -> MappedLineItem:
    """Map a GAM ad unit to a draft OpenProposal line item (inventory with no price yet)."""
    b = _Builder()
    namespace = _namespace(network_code)
    sizes = [s for s in unit.adUnitSizes if s.size]
    envs = {s.environmentType for s in unit.adUnitSizes}

    b.set("line_item_id", f"gam-au-{unit.id}")
    b.set("status", "draft", Source.INFERRED)
    if any(s.isAudio for s in unit.adUnitSizes):
        channel = "audio"
    elif unit.isNative:
        channel = "native"
    elif "VIDEO_PLAYER" in envs:
        channel = "video"
    else:
        channel = "display"
    b.set("channel", channel, Source.INFERRED)

    prop = Property(id=f"gam:{unit.id}", name=_ad_unit_path(unit))
    if unit.applicationId:
        prop.apps = [{"gam_mobile_application_id": unit.applicationId}]
        b.gap("properties.apps", "platform and bundle need a MobileApplicationService lookup")
    b.set("properties", Selectable(available=[prop], included=[prop.id]))
    b.gap("properties.domains", "GAM ad units have no domain")
    detail = _ad_unit_path(unit) + (f": {unit.description}" if unit.description else "")
    b.set("content_detail", detail)

    if unit.applicationId:
        environments = ["app_mobile"]
    elif channel == "audio":
        environments = ["audio_streaming"]
    else:
        environments = ["web_desktop", "web_mobile"]
    b.set(
        "environments", Selectable(available=environments, included=environments), Source.INFERRED
    )

    prefix = "audio" if channel == "audio" else "display"
    formats = list(
        dict.fromkeys(
            f"{prefix}/{s.size.width}{':' if s.size.isAspectRatio else 'x'}{s.size.height}"
            for s in sizes
        )
    )
    if formats:
        b.set("formats", Selectable(available=formats, included=formats[:1]))
    elif unit.isFluid:
        b.set(
            "formats",
            Selectable(available=["display/fluid"], included=["display/fluid"]),
            Source.INFERRED,
        )
        b.gap("formats", "fluid ad unit has no size; the format registry has no fluid id")
    else:
        b.gap("formats", "ad unit declares no sizes")

    b.gap("availability", "needs ForecastService.getAvailabilityForecast")
    b.gap("audiences", "comes from AudienceSegmentService, not the ad unit")
    b.gap("pricing", "GAM holds no offer price")
    b.set("hold_status", HoldStatus(hold_available=False, state=HoldState.NONE))
    b.set("buying_route", "PUBLISHER_AD_SERVER")
    b.set("serving_platform", "gam")
    b.set(
        "external_references",
        [ExternalReference(system="gam", namespace=namespace, id=unit.id, role="ad_unit")],
    )
    if unit.adUnitCode:
        b.values["ext"] = {"gam": {"ad_unit_code": unit.adUnitCode}}
    extra = sorted(k for k, v in (unit.model_extra or {}).items() if _present(v))
    return MappedLineItem(
        line_item=LineItem(**b.values),
        provenance=b.provenance,
        gaps=b.gaps,
        unmapped_source_fields=["adUnitCode", "isInterstitial", "status", *extra],
    )


class ManyToOneError(ValueError):
    """The many-to-one request breaks a rule; ``errors`` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


CHANNEL_ENVIRONMENTS: dict[str, list[str]] = {
    "display": ["web_desktop", "web_mobile"],
    "native": ["web_desktop", "web_mobile"],
    "video": ["web_desktop", "web_mobile"],
    "ctv": ["ctv"],
    "audio": ["audio_streaming"],
}


def _resolve_objects(spec: ManyToOneLineItemIn, errors: list[str]) -> list[dict[str, Any]]:
    resolved = []
    for i, obj in enumerate(spec.objects):
        where = f"{spec.line_item_id}.objects[{i}]"
        if obj.gam_ad_unit is not None:
            if spec.serving_platform != "gam":
                errors.append(f"{where}: a GAM ad unit cannot serve on {spec.serving_platform}")
                continue
            mapped = line_item_from_gam_ad_unit(obj.gam_ad_unit).line_item
            resolved.append(
                {
                    "id": obj.id or obj.gam_ad_unit.id,
                    "role": obj.role or "ad_unit",
                    "name": obj.name or mapped.properties.available[0].name,
                    "domains": obj.domains,
                    "apps": obj.apps or (mapped.properties.available[0].apps or []),
                    "formats": obj.formats
                    or ([f for f in mapped.formats.available] if mapped.formats else []),
                    "channel": obj.channel.value if obj.channel else mapped.channel,
                    "environments": mapped.environments.available if mapped.environments else [],
                }
            )
        elif obj.id:
            resolved.append(
                {
                    "id": obj.id,
                    "role": obj.role or "object",
                    "name": obj.name,
                    "domains": obj.domains,
                    "apps": obj.apps,
                    "formats": obj.formats,
                    "channel": obj.channel.value if obj.channel else None,
                    "environments": [],
                }
            )
        else:
            errors.append(f"{where}: needs an id or a gam_ad_unit")
    return resolved


def _many_to_one_line_item(
    spec: ManyToOneLineItemIn, pools: dict[tuple[str, str], list[str]], errors: list[str]
) -> Optional[tuple[LineItem, ManyToOneLineItemReport]]:
    b = _Builder()
    before = len(errors)
    objects = _resolve_objects(spec, errors)
    if len(errors) > before:
        return None

    channels = {o["channel"] for o in objects if o["channel"]}
    if spec.channel:
        channels.add(spec.channel.value)
    if len(channels) > 1:
        errors.append(
            f"{spec.line_item_id}: objects span channels {sorted(channels)}; "
            "a line item is in exactly one channel (spec 2). Split it."
        )
        return None
    channel = channels.pop() if channels else None
    if channel is None:
        errors.append(f"{spec.line_item_id}: channel cannot be inferred; set channel")
        return None

    platform = spec.serving_platform
    namespace = _namespace(spec.namespace)
    ids = [o["id"] for o in objects]
    if len(set(ids)) != len(ids):
        errors.append(f"{spec.line_item_id}: duplicate object ids")
        return None
    pid = {o["id"]: f"{platform}:{o['id']}" for o in objects}
    included = spec.included or ids[:1]
    unknown = [i for i in [*included, *spec.option_pricing] if i not in pid]
    if unknown:
        errors.append(f"{spec.line_item_id}: unknown object ids {sorted(set(unknown))}")
        return None

    props = [
        Property(
            id=pid[o["id"]],
            name=o["name"],
            domains=o["domains"] or None,
            apps=o["apps"] or None,
            supported_formats=o["formats"] or None,
        )
        for o in objects
    ]
    b.set("line_item_id", spec.line_item_id, Source.SELLER_CONFIG)
    b.set("status", "draft", Source.SELLER_CONFIG)
    b.set("channel", channel, Source.AD_SERVER if not spec.channel else Source.SELLER_CONFIG)
    b.set(
        "properties",
        Selectable(
            available=props,
            included=[pid[i] for i in included],
            pricing={pid[k]: v for k, v in spec.option_pricing.items()} or None,
        ),
    )
    if not any(o["domains"] or o["apps"] for o in objects):
        b.gap(
            "properties", "no domains or apps; ad servers rarely hold them, set from seller config"
        )
    names = [o["name"] for o in objects if o["id"] in included and o["name"]]
    if names:
        b.set("content_detail", ", ".join(names))

    envs = sorted({e for o in objects for e in o["environments"]}) or CHANNEL_ENVIRONMENTS.get(
        channel, []
    )
    if envs:
        b.set("environments", Selectable(available=envs, included=envs), Source.INFERRED)
    formats = list(dict.fromkeys(f for o in objects for f in o["formats"]))
    included_formats = list(
        dict.fromkeys(f for o in objects if o["id"] in included for f in o["formats"])
    )
    if formats:
        b.set(
            "formats", Selectable(available=formats, included=included_formats[:1] or formats[:1])
        )
    else:
        b.gap("formats", "no sizes or format ids on the source objects")

    if spec.pricing:
        b.set("pricing", [spec.pricing], Source.SELLER_CONFIG)
    else:
        b.gap("pricing", "no list price; it comes from the seller's rate card, not the ad server")

    if spec.availability:
        b.set("availability", spec.availability)
        if not spec.availability.as_of:
            b.gap("availability", "as_of missing; a buyer cannot tell how fresh the forecast is")
    else:
        b.gap("availability", "ask the ad server for one forecast on the combined selection")

    b.set("serving_platform", platform)
    if spec.serving_mode:
        b.set("serving_mode", spec.serving_mode, Source.SELLER_CONFIG)
    b.set("deal_id_issuance", "at_commitment", Source.SELLER_CONFIG)
    b.set(
        "external_references",
        [
            ExternalReference(system=platform, namespace=namespace, id=o["id"], role=o["role"])
            for o in objects
        ],
    )
    if not namespace:
        b.gap(
            "external_references", "no namespace; set the ad server account, e.g. network:21700000"
        )

    for o in objects:
        key = (platform, o["id"])
        others = pools.get(key, [])
        if others:
            b.gap(
                "availability",
                f"{platform}:{o['id']} is also sold in {', '.join(others)}; they draw from one pool",
            )
        pools.setdefault(key, []).append(spec.line_item_id)

    report = ManyToOneLineItemReport(
        line_item_id=spec.line_item_id,
        serving_platform=platform,
        source_objects=len(objects),
        provenance=b.provenance,
        gaps=b.gaps,
    )
    return LineItem(**b.values), report


def proposal_from_many_to_one(req: ManyToOneProposalRequest) -> ManyToOneProposalResponse:
    """Build one proposal from objects in several ad servers: one ad server per line item."""
    errors: list[str] = []
    seen = Counter(li.line_item_id for li in req.line_items)
    errors += [f"line_item_id {k} is used {v} times" for k, v in seen.items() if v > 1]
    pools: dict[tuple[str, str], list[str]] = {}
    built = [_many_to_one_line_item(spec, pools, errors) for spec in req.line_items]
    if errors:
        raise ManyToOneError(errors)
    proposal = Proposal(
        proposal_id=req.proposal_id,
        seller_id=req.seller_id,
        seller_name=req.seller_name,
        type=req.type,
        status="draft",
        valid_from=req.valid_from,
        valid_until=req.valid_until,
        description=req.description,
        line_items=[li for li, _ in built],
    )
    return ManyToOneProposalResponse(proposal=proposal, report=[r for _, r in built])
