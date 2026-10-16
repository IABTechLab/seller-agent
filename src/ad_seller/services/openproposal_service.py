# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Map seller-agent and GAM payloads to OpenProposal 3.0 objects.

Every mapping returns the line item plus per-field provenance, the
OpenProposal fields that could not be filled, and the source fields that
have no OpenProposal home.
"""

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from iab_agentic_primitives.primitives import Deal, Quote

from ..config import get_settings
from ..config.settings import seller_id_or_default
from ..models.flow_state import ProductDefinition
from ..models.openproposal import (
    LINE_ITEM_FIELDS,
    Audience,
    Availability,
    Commitment,
    CommitmentBounds,
    ExternalReference,
    FrequencyCap,
    GAMAdUnitIn,
    GAMLineItemIn,
    GAMOrderIn,
    HoldState,
    HoldStatus,
    LineItem,
    MappedLineItem,
    MappedProposal,
    PricingEntry,
    Property,
    Proposal,
    RequiredCredential,
    Selectable,
    Settable,
    Source,
)


class UnmappableError(ValueError):
    """The source object has no OpenProposal meaning (e.g. a GAM house line item)."""


INVENTORY_TYPE_TO_CHANNEL: dict[str, str] = {
    "display": "display",
    "video": "video",
    "ctv": "ctv",
    "native": "native",
    "mobile_app": "display",
    "linear_tv": "video",
}

INVENTORY_TYPE_TO_ENVIRONMENTS: dict[str, list[str]] = {
    "display": ["web_desktop", "web_mobile"],
    "video": ["web_desktop", "web_mobile"],
    "ctv": ["ctv"],
    "native": ["web_desktop", "web_mobile"],
    "mobile_app": ["app_mobile"],
    "linear_tv": ["ctv"],
}

DEAL_TYPE_TO_MECHANISM: dict[str, str] = {
    "PG": "PROGRAMMATIC_GUARANTEED",
    "PD": "PREFERRED_DEAL",
    "PA": "PRIVATE_AUCTION",
    "programmaticguaranteed": "PROGRAMMATIC_GUARANTEED",
    "preferreddeal": "PREFERRED_DEAL",
    "privateauction": "PRIVATE_AUCTION",
}

DEAL_STATUS_TO_LINE_ITEM_STATUS: dict[str, str] = {
    "proposed": "agreed",
    "accepted": "agreed",
    "booked": "agreed",
    "active": "agreed",
    "makegood_pending": "agreed",
    "partially_cancelled": "agreed",
    "negotiating": "under_review",
    "completed": "expired",
    "expired": "expired",
    "rejected": "withdrawn",
    "failed": "withdrawn",
    "cancelled": "withdrawn",
}

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

GAM_NON_SELLABLE_TYPES = frozenset(
    {"HOUSE", "NETWORK", "BULK", "ADSENSE", "ADMOB", "CLICK_TRACKING", "BUMPER", "LEGACY_DFP"}
)

GAM_LINE_ITEM_DELIVERY_FIELDS = (
    "priority",
    "deliveryRateType",
    "creativeRotationType",
    "roadblockingType",
    "companionDeliveryOption",
    "competitiveConstraintScope",
    "discountType",
    "discount",
    "allowOverbook",
    "skipInventoryCheck",
)

SELLER_CREDENTIALS = [RequiredCredential(type="custom", scheme="api_key", id_required=True)]


class _Builder:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.provenance: dict[str, Source] = {f: Source.NOT_AVAILABLE for f in LINE_ITEM_FIELDS}
        self.gaps: list[str] = []
        self.unmapped: list[str] = []

    def set(self, field: str, value: Any, source: Source) -> None:
        if value is None:
            return
        self.values[field] = value
        self.provenance[field] = source

    def gap(self, field: str, reason: str) -> None:
        self.gaps.append(f"{field}: {reason}")

    def build(self) -> MappedLineItem:
        return MappedLineItem(
            line_item=LineItem(**self.values),
            provenance=self.provenance,
            gaps=self.gaps,
            unmapped_source_fields=self.unmapped,
        )


def _selectable(values: Iterable[Any], included: Optional[list[Any]] = None) -> Selectable:
    items = list(values)
    return Selectable(available=items, included=items[:1] if included is None else included)


def _apply_seller_defaults(b: _Builder) -> None:
    b.set(
        "hold_status", HoldStatus(hold_available=False, state=HoldState.NONE), Source.SELLER_CONFIG
    )
    b.set("visibility", "public", Source.SELLER_CONFIG)
    b.set("required_credentials", SELLER_CREDENTIALS, Source.SELLER_CONFIG)
    b.set("terms_acceptance_required", False, Source.SELLER_CONFIG)
    b.set("supported_order_systems", ["Deals API"], Source.SELLER_CONFIG)


def line_item_from_product(product: ProductDefinition) -> MappedLineItem:
    """Map a catalog product to a published OpenProposal line item (an offer)."""
    b = _Builder()
    inv_type = product.inventory_type
    b.set("line_item_id", product.product_id, Source.SELLER_CATALOG)
    b.set("status", "published", Source.SELLER_CATALOG)
    b.set("channel", INVENTORY_TYPE_TO_CHANNEL.get(inv_type, "display"), Source.INFERRED)
    if inv_type == "linear_tv":
        b.gap(
            "channel",
            "OpenProposal has no linear TV channel; mapped to video, original kept in ext",
        )
    b.set(
        "properties",
        _selectable([Property(id=product.product_id, name=product.name)], [product.product_id]),
        Source.SELLER_CATALOG,
    )
    b.gap("properties.domains", "seller-agent products carry no domain")
    detail = product.name if not product.description else f"{product.name}: {product.description}"
    b.set("content_detail", detail, Source.SELLER_CATALOG)
    envs = INVENTORY_TYPE_TO_ENVIRONMENTS.get(inv_type, ["web_desktop", "web_mobile"])
    b.set("environments", Selectable(available=envs, included=envs), Source.INFERRED)

    if product.audience_capabilities:
        b.set(
            "audiences",
            _selectable([Audience(id=a) for a in product.audience_capabilities], []),
            Source.SELLER_CATALOG,
        )
    b.gap("formats", "seller-agent products have no creative sizes; needs the ad server's sizes")

    if product.base_cpm is not None:
        b.set(
            "pricing",
            [
                PricingEntry(
                    cost_method="CPM", gross_rate=product.base_cpm, currency=product.currency
                )
            ],
            Source.SELLER_CATALOG,
        )
        b.gap("pricing.floor", "withheld: seller-agent does not disclose its floor to buyers")
    else:
        b.gap("pricing", "product is unpriced")
    b.set(
        "commitment_bounds",
        CommitmentBounds(
            min_impressions=product.minimum_impressions,
            max_impressions=product.maximum_impressions,
        ),
        Source.SELLER_CATALOG,
    )
    mechanisms = [
        DEAL_TYPE_TO_MECHANISM[d.value if hasattr(d, "value") else str(d)]
        for d in product.supported_deal_types
        if (d.value if hasattr(d, "value") else str(d)) in DEAL_TYPE_TO_MECHANISM
    ]
    if mechanisms:
        b.set("transaction_mechanism", _selectable(mechanisms), Source.SELLER_CATALOG)
    b.set("buying_route", "PUBLISHER_AD_SERVER", Source.SELLER_CONFIG)
    b.set(
        "external_references",
        [ExternalReference(system="seller-agent", id=product.product_id, role="product")],
        Source.SELLER_CATALOG,
    )
    _apply_seller_defaults(b)
    b.values["ext"] = {"seller_agent": {"inventory_type": inv_type}}
    b.unmapped += ["supported_pricing_models", "pricing_type", "ucp_embedding", "floor_cpm"]
    return b.build()


def proposal_from_catalog(products: Iterable[ProductDefinition]) -> MappedProposal:
    """Map the whole catalog to one standard proposal."""
    settings = get_settings()
    mapped = [line_item_from_product(p) for p in products]
    seller_id = seller_id_or_default(settings)
    proposal = Proposal(
        proposal_id=f"{seller_id}-standard",
        version=1,
        seller_id=seller_id,
        seller_name=settings.seller_organization_name,
        valid_from=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        description=f"Standard proposal from {settings.seller_organization_name}: "
        f"{len(mapped)} line items from the product catalog.",
        line_items=[m.line_item for m in mapped],
    )
    return MappedProposal(proposal=proposal, line_items=mapped)


QUOTE_STATUS_TO_LINE_ITEM_STATUS: dict[str, str] = {
    "available": "under_review",
    "booked": "agreed",
    "expired": "expired",
    "declined": "withdrawn",
}


def _line_item_from_priced_record(
    data: dict[str, Any], *, status: str, source: Source, record_id: str, role: str
) -> _Builder:
    b = _Builder()
    product = data.get("product") or {}
    pricing = data.get("pricing") or {}
    terms = data.get("terms") or {}
    inv_type = product.get("inventory_type") or "display"

    b.set("line_item_id", product.get("product_id") or record_id, source)
    b.set("status", status, source)
    b.set("channel", INVENTORY_TYPE_TO_CHANNEL.get(inv_type, "display"), Source.INFERRED)
    b.set(
        "properties",
        _selectable(
            [Property(id=product.get("product_id", ""), name=product.get("name"))],
            [product.get("product_id", "")],
        ),
        source,
    )
    b.set("content_detail", product.get("name"), source)

    final = (pricing.get("final_cpm") or {}).get("amount_micros")
    base = (pricing.get("base_cpm") or {}).get("amount_micros")
    currency = (pricing.get("final_cpm") or pricing.get("base_cpm") or {}).get("currency", "USD")
    b.set(
        "pricing",
        [
            PricingEntry(
                cost_method=str(pricing.get("pricing_model") or "cpm").upper(),
                gross_rate=base / 1_000_000 if base is not None else None,
                agreed_rate=final / 1_000_000 if final is not None else None,
                price_valid_until=data.get("expires_at"),
                currency=currency,
            )
        ],
        source,
    )
    if terms.get("impressions"):
        b.set(
            "commitment",
            Commitment(
                basis="units",
                amount=terms["impressions"],
                flight_start=terms.get("flight_start"),
                flight_end=terms.get("flight_end"),
                derived_units=terms["impressions"],
            ),
            source,
        )
    mech = DEAL_TYPE_TO_MECHANISM.get(str(data.get("deal_type")))
    if mech:
        b.set("transaction_mechanism", Selectable(available=[mech], included=[mech]), source)
    b.set("buying_route", "PUBLISHER_AD_SERVER", Source.SELLER_CONFIG)
    b.set("correlation_id", record_id, source)
    b.set(
        "external_references",
        [ExternalReference(system="seller-agent", id=record_id, role=role)],
        source,
    )
    b.set("supported_order_systems", ["Deals API"], Source.SELLER_CONFIG)
    b.values["ext"] = {
        "seller_agent": {
            "inventory_type": inv_type,
            "buyer_tier": data.get("buyer_tier"),
            "tier_discount_pct": pricing.get("tier_discount_pct"),
            "volume_discount_pct": pricing.get("volume_discount_pct"),
            "pricing_rationale": pricing.get("rationale"),
        }
    }
    b.unmapped += ["buyer_tier", "pricing.tier_discount_pct", "pricing.volume_discount_pct"]
    return b


def line_item_from_quote(quote: Quote) -> MappedLineItem:
    """Map a seller-agent quote to a composed OpenProposal line item awaiting commitment."""
    data = quote.model_dump(mode="json")
    b = _line_item_from_priced_record(
        data,
        status=QUOTE_STATUS_TO_LINE_ITEM_STATUS.get(data.get("status", ""), "under_review"),
        source=Source.QUOTE,
        record_id=quote.quote_id,
        role="quote",
    )
    available = (data.get("availability") or {}).get("inventory_available")
    b.set(
        "availability",
        Availability(type="reservable" if available else "forecast", as_of=data.get("created_at")),
        Source.QUOTE,
    )
    b.set("hold_status", HoldStatus(hold_available=False, state=HoldState.NONE), Source.QUOTE)
    if quote.deal_id:
        b.set("deal_id_issuance", quote.deal_id, Source.QUOTE)
    return b.build()


def line_item_from_deal(deal: Deal) -> MappedLineItem:
    """Map a booked seller-agent deal to an agreed OpenProposal line item."""
    data = deal.model_dump(mode="json")
    b = _line_item_from_priced_record(
        data,
        status=DEAL_STATUS_TO_LINE_ITEM_STATUS.get(data.get("status", ""), "agreed"),
        source=Source.BOOKED_DEAL,
        record_id=deal.deal_id,
        role="deal",
    )
    if "commitment" in b.values:
        b.gap("commitment.idempotency_key", "the booking request's key is not stored on the deal")
    b.set("hold_status", HoldStatus(state=HoldState.CONVERTED), Source.BOOKED_DEAL)
    b.set("deal_id_issuance", deal.deal_id, Source.BOOKED_DEAL)
    seats = ((data.get("openrtb_params") or {}).get("wseat")) or []
    if seats:
        b.set("buyer_requirements", {"dsp_seat_id": seats}, Source.BOOKED_DEAL)
    if deal.quote_id:
        b.values["external_references"].append(
            ExternalReference(system="seller-agent", id=deal.quote_id, role="quote")
        )
    b.unmapped += ["activation_instructions", "openrtb_params.at", "media_type"]
    return b.build()


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


def _ad_unit_path(unit: GAMAdUnitIn) -> str:
    parents = [p.get("name", "") for p in unit.parentPath[1:]]
    return " › ".join([*parents, unit.name])


def line_item_from_gam_ad_unit(
    unit: GAMAdUnitIn, network_code: Optional[str] = None
) -> MappedLineItem:
    """Map a GAM ad unit to a draft OpenProposal line item (an offer to price)."""
    b = _Builder()
    sizes = [s for s in unit.adUnitSizes if s.size]
    envs = {s.environmentType for s in unit.adUnitSizes}

    b.set("line_item_id", f"gam-au-{unit.id}", Source.AD_SERVER)
    b.set("status", "draft", Source.SELLER_CONFIG)
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
        prop.apps = [{"gam_application_id": unit.applicationId}]
        b.gap("properties.apps", "platform and bundle need a MobileApplicationService lookup")
    b.set("properties", _selectable([prop], [prop.id]), Source.AD_SERVER)
    b.gap("properties.domains", "GAM ad units have no domain; set it in seller config or ads.txt")
    detail = _ad_unit_path(unit) + (f": {unit.description}" if unit.description else "")
    b.set("content_detail", detail, Source.AD_SERVER)

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
    formats = []
    for s in sizes:
        sep = ":" if s.size.isAspectRatio else "x"
        formats.append(f"{prefix}/{s.size.width}{sep}{s.size.height}")
    formats = list(dict.fromkeys(formats))
    if formats:
        b.set("formats", _selectable(formats), Source.AD_SERVER)
    elif unit.isFluid:
        b.set("formats", _selectable(["display/fluid"]), Source.INFERRED)
        b.gap("formats", "fluid ad unit has no size; the format registry has no fluid id")
    else:
        b.gap("formats", "ad unit declares no sizes")

    b.gap("availability", "needs ForecastService.getAvailabilityForecast per prospective line item")
    b.gap("audiences", "comes from AudienceSegmentService, not the ad unit")
    b.gap("pricing", "GAM holds no offer price; comes from the seller rate card")
    b.set("hold_status", HoldStatus(hold_available=False, state=HoldState.NONE), Source.AD_SERVER)
    b.set("buying_route", "PUBLISHER_AD_SERVER", Source.AD_SERVER)
    b.set("serving_platform", "google_ad_manager", Source.AD_SERVER)
    b.set("serving_mode", "client_side", Source.INFERRED)
    b.set(
        "external_references",
        [ExternalReference(system="gam", namespace=network_code, id=unit.id, role="ad_unit")],
        Source.AD_SERVER,
    )
    b.set("visibility", "public", Source.SELLER_CONFIG)
    b.set("required_credentials", SELLER_CREDENTIALS, Source.SELLER_CONFIG)
    extra = sorted(set(unit.model_extra or {}))
    b.unmapped += ["adUnitCode", "isInterstitial", "status", *extra]
    if unit.adUnitCode:
        b.values["ext"] = {"gam": {"ad_unit_code": unit.adUnitCode}}
    return b.build()


def line_item_from_gam_line_item(
    item: GAMLineItemIn, order: Optional[GAMOrderIn] = None, network_code: Optional[str] = None
) -> MappedLineItem:
    """Map a sold GAM line item to an OpenProposal line item in its current state."""
    if item.lineItemType in GAM_NON_SELLABLE_TYPES:
        raise UnmappableError(
            f"GAM {item.lineItemType} line items are not sales and have no OpenProposal meaning"
        )
    b = _Builder()
    b.set("line_item_id", f"gam-li-{item.id}", Source.AD_SERVER)
    status = GAM_STATUS_TO_LINE_ITEM_STATUS.get(item.status, "draft")
    b.set("status", status, Source.AD_SERVER)
    if item.status in ("DELIVERING", "DELIVERY_EXTENDED", "PAUSED", "COMPLETED"):
        b.gap("status", f"GAM {item.status} has no OpenProposal equivalent; mapped to {status}")
    b.set(
        "channel", "video" if item.environmentType == "VIDEO_PLAYER" else "display", Source.INFERRED
    )

    inventory = item.targeting.get("inventoryTargeting") or {}
    props = [Property(id=f"gam:{t['adUnitId']}") for t in inventory.get("targetedAdUnits") or []]
    props += [
        Property(id=f"gam-placement:{p}") for p in inventory.get("targetedPlacementIds") or []
    ]
    if props:
        b.set("properties", _selectable(props, [p.id for p in props]), Source.AD_SERVER)
    b.set("content_detail", item.name, Source.AD_SERVER)

    sizes = {
        f"display/{c['size']['width']}x{c['size']['height']}"
        for c in item.creativePlaceholders
        if c.get("size")
    }
    if sizes:
        b.set(
            "formats", Selectable(available=sorted(sizes), included=sorted(sizes)), Source.AD_SERVER
        )

    geo = item.targeting.get("geoTargeting") or {}
    locations = [str(loc.get("id")) for loc in geo.get("targetedLocations") or []]
    if locations:
        b.set("geo", Settable(allowed=[f"gam-geo:{loc}" for loc in locations]), Source.AD_SERVER)
        b.gap("geo", "GAM location ids need translating to ISO 3166 codes")
    devices = (item.targeting.get("technologyTargeting") or {}).get("deviceCategoryTargeting") or {}
    if devices.get("targetedDeviceCategories"):
        b.set(
            "device",
            Settable(allowed=[str(d.get("id")) for d in devices["targetedDeviceCategories"]]),
            Source.AD_SERVER,
        )
    if item.targeting.get("dayPartTargeting"):
        b.set("daypart", Settable(allowed=[item.targeting["dayPartTargeting"]]), Source.AD_SERVER)
    if item.frequencyCaps:
        cap = item.frequencyCaps[0]
        b.set(
            "frequency_cap",
            FrequencyCap(
                max=Settable(max=cap.get("maxImpressions")),
                period=f"{cap.get('numTimeUnits', 1)} {str(cap.get('timeUnit', '')).lower()}",
            ),
            Source.AD_SERVER,
        )
        b.gap(
            "frequency_cap.basis",
            "GAM caps per user; OpenProposal wants device, household or listener",
        )
    if item.targeting.get("customTargeting"):
        b.unmapped.append("targeting.customTargeting")

    rate, rate_currency = _gam_money(item.costPerUnit)
    currency = rate_currency or (order.currencyCode if order else None) or "USD"
    b.set(
        "pricing",
        [PricingEntry(cost_method=item.costType, agreed_rate=rate, currency=currency)],
        Source.AD_SERVER,
    )
    b.gap("pricing.gross_rate", "GAM stores only the booked cost, not a list price or floor")

    goal = item.primaryGoal or {}
    budget, _ = _gam_money(item.budget)
    units = goal.get("units")
    if units is not None and int(units) > 0:
        amount, basis = float(units), "units"
    elif budget:
        amount, basis = budget, "spend"
    else:
        amount, basis = None, None
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
            Source.AD_SERVER,
        )
        if goal.get("goalType") == "DAILY":
            b.gap("commitment", "GAM DAILY goal; OpenProposal commitments have no per-day basis")
        if not item.externalId:
            b.gap("commitment.idempotency_key", "GAM line item has no externalId")

    reserved = item.reservationStatus == "RESERVED"
    state = (
        HoldState.CONVERTED
        if reserved and status == "agreed"
        else (HoldState.HELD if reserved else HoldState.NONE)
    )
    b.set("hold_status", HoldStatus(hold_available=reserved, state=state), Source.AD_SERVER)

    mech = GAM_LINE_ITEM_TYPE_TO_MECHANISM.get(item.lineItemType)
    if order and order.isProgrammatic and mech == "DIRECT_IO":
        mech = "PROGRAMMATIC_GUARANTEED"
    if mech:
        b.set(
            "transaction_mechanism", Selectable(available=[mech], included=[mech]), Source.AD_SERVER
        )
    if item.dealInfo:
        deal_id = item.dealInfo.get("externalDealId") or item.dealInfo.get("dealId")
        b.set("deal_id_issuance", str(deal_id) if deal_id else None, Source.AD_SERVER)
    partner = (item.thirdPartyMeasurementSettings or {}).get("viewabilityPartner")
    if partner and partner != "NONE":
        b.set("measurement_source", _selectable([{"vendor": partner}], [partner]), Source.AD_SERVER)

    b.set("buying_route", "PUBLISHER_AD_SERVER", Source.AD_SERVER)
    b.set("serving_platform", "google_ad_manager", Source.AD_SERVER)
    refs = [
        ExternalReference(system="gam", namespace=network_code, id=str(item.id), role="line_item"),
        ExternalReference(system="gam", namespace=network_code, id=str(item.orderId), role="order"),
    ]
    b.set("external_references", refs, Source.AD_SERVER)
    b.set("correlation_id", item.externalId, Source.AD_SERVER)
    if order and order.poNumber:
        b.set("billing", {"po_number": order.poNumber}, Source.AD_SERVER)

    extra = item.model_extra or {}
    delivery = {k: extra[k] for k in GAM_LINE_ITEM_DELIVERY_FIELDS if k in extra}
    b.unmapped += [f for f in GAM_LINE_ITEM_DELIVERY_FIELDS if f in extra]
    b.values["ext"] = {
        "gam": {"line_item_type": item.lineItemType, "status": item.status, **delivery}
    }
    return b.build()
