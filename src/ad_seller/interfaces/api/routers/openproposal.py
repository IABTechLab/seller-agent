# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenProposal 3.0 twins of the product, quote and deal endpoints.

Each ``op-`` route speaks OpenProposal objects and delegates to the
existing handler, so auth, buyer verification, tier pricing and
idempotency behave exactly as on the original route. The original
routes are unchanged.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from iab_agentic_primitives.primitives import BuyerIdentity, DealType, Money
from iab_agentic_primitives.protocol import DealBookingRequest, QuoteRequest
from pydantic import ValidationError

from ....models.openproposal import (
    GAMMapAdUnitRequest,
    GAMMapLineItemRequest,
    LineItem,
    MappedLineItem,
    MappedProposal,
    OPBuyerIdentity,
    OPDeal,
    OPDealRequest,
    OPQuote,
    OPQuoteRequest,
    Proposal,
)
from ....services import openproposal_service
from .. import deps
from . import deals as deals_routes
from . import quotes as quotes_routes

router = APIRouter()

TAG = "OpenProposal"

MECHANISM_TO_DEAL_TYPE = {
    "PROGRAMMATIC_GUARANTEED": DealType.PROGRAMMATIC_GUARANTEED,
    "PREFERRED_DEAL": DealType.PREFERRED_DEAL,
    "PRIVATE_AUCTION": DealType.PRIVATE_AUCTION,
}

GAM_AD_UNIT_EXAMPLES = {
    "display_run_of_site": {
        "summary": "Display ad unit with sizes",
        "value": {
            "network_code": "12345678",
            "ad_unit": {
                "id": "54545842",
                "name": "Run-of-Site",
                "parentId": "17980522",
                "parentPath": [
                    {"id": "17980402", "name": "12345678"},
                    {"id": "17980522", "name": "publisher.example"},
                ],
                "adUnitCode": "pubROS",
                "status": "ACTIVE",
                "adUnitSizes": [
                    {"size": {"width": 320, "height": 50}, "environmentType": "BROWSER"},
                    {"size": {"width": 728, "height": 90}, "environmentType": "BROWSER"},
                    {"size": {"width": 970, "height": 250}, "environmentType": "BROWSER"},
                ],
                "isNative": False,
                "isFluid": False,
                "isInterstitial": False,
                "targetWindow": "BLANK",
                "smartSizeMode": "NONE",
            },
        },
    },
    "fluid": {
        "summary": "Fluid ad unit with no sizes",
        "value": {
            "network_code": "12345678",
            "ad_unit": {
                "id": "531723682",
                "name": "Flex1:1",
                "adUnitCode": "Flex1:1",
                "status": "ACTIVE",
                "adUnitSizes": [],
                "isFluid": True,
            },
        },
    },
}

GAM_LINE_ITEM_EXAMPLES = {
    "standard_pg_line": {
        "summary": "Standard line item booked from an agentic deal",
        "value": {
            "network_code": "12345678",
            "order": {
                "id": 3961795097,
                "name": "Launch Campaign - OpenDirect PG",
                "currencyCode": "USD",
                "isProgrammatic": False,
            },
            "line_item": {
                "id": 7202154879,
                "orderId": 3961795097,
                "name": "Streaming brand - PG Line - 2,250,000 imps",
                "externalId": "OD-BC9DAEFA",
                "lineItemType": "STANDARD",
                "status": "DRAFT",
                "reservationStatus": "UNRESERVED",
                "priority": 8,
                "costType": "CPM",
                "costPerUnit": {"currencyCode": "USD", "microAmount": 27840000},
                "primaryGoal": {
                    "goalType": "LIFETIME",
                    "unitType": "IMPRESSIONS",
                    "units": 2250000,
                },
                "startDateTime": {
                    "date": {"year": 2026, "month": 3, "day": 1},
                    "hour": 0,
                    "minute": 0,
                    "second": 0,
                    "timeZoneId": "America/New_York",
                },
                "endDateTime": {
                    "date": {"year": 2026, "month": 6, "day": 30},
                    "hour": 23,
                    "minute": 59,
                    "second": 0,
                    "timeZoneId": "America/New_York",
                },
                "deliveryRateType": "EVENLY",
                "creativeRotationType": "EVEN",
                "roadblockingType": "ONE_OR_MORE",
                "environmentType": "BROWSER",
                "creativePlaceholders": [
                    {"size": {"width": 300, "height": 250}, "creativeSizeType": "PIXEL"},
                    {"size": {"width": 728, "height": 90}, "creativeSizeType": "PIXEL"},
                ],
                "targeting": {
                    "inventoryTargeting": {
                        "targetedAdUnits": [{"adUnitId": "54545842", "includeDescendants": True}]
                    }
                },
            },
        },
    },
    "house_line": {
        "summary": "House line item (rejected: not a sale)",
        "value": {
            "line_item": {
                "id": 1001,
                "orderId": 2001,
                "name": "House banner",
                "lineItemType": "HOUSE",
                "status": "DELIVERING",
            }
        },
    },
}


PRODUCT_ID_EXAMPLES = {
    "ctv_series": {
        "summary": "CTV series (aws_workshop CSV catalog)",
        "value": "inv-ctv-apex-series",
    },
    "ctv_sports": {
        "summary": "CTV live sports (aws_workshop CSV catalog)",
        "value": "inv-ctv-apex-sports-nba",
    },
}

QUOTE_ID_EXAMPLES = {
    "quote": {
        "summary": "quote_id returned by POST /api/v1/op-quotes",
        "value": "qt-0b9fd49d24f7",
    }
}

DEAL_ID_EXAMPLES = {
    "deal": {
        "summary": "deal_id returned by POST /api/v1/op-deals",
        "value": "DEMO-0ADC560536FE",
    }
}

OP_QUOTE_EXAMPLES = {
    "preferred_deal_advertiser": {
        "summary": "Preferred deal, advertiser tier",
        "value": {
            "idempotency_key": "op-quote-001",
            "line_item_id": "inv-ctv-apex-series",
            "transaction_mechanism": "PREFERRED_DEAL",
            "commitment": {
                "basis": "units",
                "amount": 500000,
                "flight_start": "2026-11-01",
                "flight_end": "2026-11-30",
            },
            "buyer_identity": {
                "seat_id": "seat-demo",
                "agency_id": "agency-demo",
                "advertiser_id": "adv-demo",
            },
        },
    },
    "programmatic_guaranteed_with_rate": {
        "summary": "Programmatic guaranteed with a requested rate",
        "value": {
            "idempotency_key": "op-quote-002",
            "line_item_id": "inv-ctv-apex-sports-nba",
            "transaction_mechanism": "PROGRAMMATIC_GUARANTEED",
            "commitment": {
                "basis": "units",
                "amount": 2000000,
                "flight_start": "2026-12-01",
                "flight_end": "2026-12-31",
            },
            "requested_rate": 48.0,
            "buyer_identity": {"agency_id": "agency-demo"},
        },
    },
    "private_auction_public": {
        "summary": "Private auction, no buyer identity (public tier)",
        "value": {
            "idempotency_key": "op-quote-003",
            "line_item_id": "inv-ctv-apex-series",
            "transaction_mechanism": "PRIVATE_AUCTION",
            "commitment": {"basis": "units", "amount": 100000},
        },
    },
}

OP_DEAL_EXAMPLES = {
    "book_quote": {
        "summary": "Book a quote from POST /api/v1/op-quotes",
        "value": {
            "idempotency_key": "op-deal-001",
            "quote_id": "qt-0b9fd49d24f7",
            "buyer_identity": {
                "seat_id": "seat-demo",
                "agency_id": "agency-demo",
                "advertiser_id": "adv-demo",
            },
            "notes": "Q4 launch flight",
        },
    }
}


def _explain() -> bool:
    return Query(
        default=False,
        description="Add per-field provenance, gaps and unmapped source fields.",
        openapi_examples={
            "plain": {"summary": "OpenProposal object only", "value": False},
            "explain": {"summary": "With provenance, gaps and unmapped fields", "value": True},
        },
    )


def _buyer_identity(ident: Optional[OPBuyerIdentity]) -> Optional[BuyerIdentity]:
    return BuyerIdentity(**ident.model_dump()) if ident else None


def _invalid(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"error": "invalid_request", "message": str(exc.errors(include_url=False))},
    )


@router.get(
    "/op-products",
    tags=[TAG],
    response_model=list[LineItem] | list[MappedLineItem],
    response_model_exclude_none=True,
)
async def list_op_products(
    limit: int = Query(default=50, ge=1, le=500, examples=[10]),
    offset: int = Query(default=0, ge=0, examples=[0]),
    explain: bool = _explain(),
):
    """Catalog products as OpenProposal line items (twin of ``GET /products``)."""
    catalog = await deps.get_product_catalog()
    products = list(catalog["products"].values())[offset : offset + limit]
    mapped = [openproposal_service.line_item_from_product(p) for p in products]
    return mapped if explain else [m.line_item for m in mapped]


@router.get(
    "/op-products/{product_id}",
    tags=[TAG],
    response_model=LineItem | MappedLineItem,
    response_model_exclude_none=True,
)
async def get_op_product(
    product_id: str = Path(
        description="A line_item_id from GET /op-products.",
        openapi_examples=PRODUCT_ID_EXAMPLES,
    ),
    explain: bool = _explain(),
):
    """One product as an OpenProposal line item (twin of ``GET /products/{product_id}``)."""
    catalog = await deps.get_product_catalog()
    product = catalog["products"].get(product_id)
    if product is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "product_not_found", "message": f"Product '{product_id}' not found."},
        )
    mapped = openproposal_service.line_item_from_product(product)
    return mapped if explain else mapped.line_item


@router.get(
    "/op-proposals/standard",
    tags=[TAG],
    response_model=Proposal | MappedProposal,
    response_model_exclude_none=True,
)
async def get_op_standard_proposal(
    explain: bool = _explain(),
):
    """The whole catalog as one OpenProposal standard proposal."""
    catalog = await deps.get_product_catalog()
    mapped = openproposal_service.proposal_from_catalog(catalog["products"].values())
    return mapped if explain else mapped.proposal


@router.post(
    "/api/v1/op-quotes",
    tags=[TAG],
    response_model=OPQuote,
    response_model_exclude_none=True,
)
async def create_op_quote(
    request: OPQuoteRequest = Body(openapi_examples=OP_QUOTE_EXAMPLES),
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> OPQuote:
    """Compose a line item and get its price (twin of ``POST /api/v1/quotes``).

    Pick a ``transaction_mechanism`` from the line item's available set and
    size the ``commitment`` in impressions. The response is the composed
    line item with ``agreed_rate`` and ``price_valid_until``; commit it with
    ``POST /api/v1/op-deals``.
    """
    deal_type = MECHANISM_TO_DEAL_TYPE.get(request.transaction_mechanism)
    if deal_type is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_transaction_mechanism",
                "message": f"{request.transaction_mechanism.value} cannot be quoted by this seller; "
                "use PROGRAMMATIC_GUARANTEED, PREFERRED_DEAL or PRIVATE_AUCTION.",
            },
        )
    catalog = await deps.get_product_catalog()
    product = catalog["products"].get(request.line_item_id)
    currency = getattr(product, "currency", "USD") if product else "USD"
    try:
        quote_request = QuoteRequest(
            idempotency_key=request.idempotency_key,
            product_id=request.line_item_id,
            deal_type=deal_type,
            impressions=request.commitment.amount,
            flight_start=date.fromisoformat(request.commitment.flight_start)
            if request.commitment.flight_start
            else None,
            flight_end=date.fromisoformat(request.commitment.flight_end)
            if request.commitment.flight_end
            else None,
            target_cpm=Money(
                amount_micros=round(request.requested_rate * 1_000_000), currency=currency
            )
            if request.requested_rate
            else None,
            buyer_identity=_buyer_identity(request.buyer_identity),
            agent_url=request.agent_url,
        )
    except ValidationError as exc:
        raise _invalid(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_request", "message": str(exc)}
        ) from exc
    response = await quotes_routes.create_quote(
        request=quote_request, api_key_record=api_key_record
    )
    mapped = openproposal_service.line_item_from_quote(response.quote)
    return OPQuote(quote_id=response.quote.quote_id, line_item=mapped.line_item)


@router.get(
    "/api/v1/op-quotes/{quote_id}",
    tags=[TAG],
    response_model=OPQuote,
    response_model_exclude_none=True,
)
async def get_op_quote(
    quote_id: str = Path(openapi_examples=QUOTE_ID_EXAMPLES),
) -> OPQuote:
    """A quote as a composed OpenProposal line item (twin of ``GET /api/v1/quotes/{quote_id}``)."""
    response = await quotes_routes.get_quote(quote_id)
    mapped = openproposal_service.line_item_from_quote(response.quote)
    return OPQuote(quote_id=quote_id, line_item=mapped.line_item)


@router.post(
    "/api/v1/op-deals",
    tags=[TAG],
    response_model=OPDeal,
    response_model_exclude_none=True,
)
async def create_op_deal(
    request: OPDealRequest = Body(openapi_examples=OP_DEAL_EXAMPLES),
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> OPDeal:
    """Commit to a composed line item (twin of ``POST /api/v1/deals``).

    Requires a buyer API key. Returns the agreed line item with its
    ``deal_id_issuance``.
    """
    booking = DealBookingRequest(
        idempotency_key=request.idempotency_key,
        quote_id=request.quote_id,
        buyer_identity=_buyer_identity(request.buyer_identity),
        notes=request.notes,
    )
    response = await deals_routes.book_deal(request=booking, api_key_record=api_key_record)
    mapped = openproposal_service.line_item_from_deal(response.deal)
    return OPDeal(deal_id=response.deal.deal_id, line_item=mapped.line_item)


@router.get(
    "/api/v1/op-deals/{deal_id}",
    tags=[TAG],
    response_model=OPDeal | MappedLineItem,
    response_model_exclude_none=True,
)
async def get_op_deal(
    deal_id: str = Path(openapi_examples=DEAL_ID_EXAMPLES),
    explain: bool = _explain(),
    api_key_record=Depends(deps._get_optional_api_key_record),
):
    """A booked deal as an agreed OpenProposal line item (twin of ``GET /api/v1/deals/{deal_id}``).

    Requires an API key.
    """
    if api_key_record is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required", "message": "API key required."},
        )
    response = await deals_routes.get_deal_by_id(deal_id)
    mapped = openproposal_service.line_item_from_deal(response.deal)
    return mapped if explain else OPDeal(deal_id=deal_id, line_item=mapped.line_item)


@router.post(
    "/api/v1/op-gam/ad-unit",
    tags=[TAG],
    response_model=LineItem | MappedLineItem,
    response_model_exclude_none=True,
)
async def map_gam_ad_unit(
    request: GAMMapAdUnitRequest = Body(openapi_examples=GAM_AD_UNIT_EXAMPLES),
    explain: bool = _explain(),
):
    """Map a GAM ``AdUnit`` (InventoryService, API v202608) to a draft line item.

    Pricing, terms and eligibility are not in GAM; they come from the
    seller's rate card and settings (listed as gaps with ``explain=true``).
    """
    mapped = openproposal_service.line_item_from_gam_ad_unit(request.ad_unit, request.network_code)
    return mapped if explain else mapped.line_item


@router.post(
    "/api/v1/op-gam/line-item",
    tags=[TAG],
    response_model=LineItem | MappedLineItem,
    response_model_exclude_none=True,
)
async def map_gam_line_item(
    request: GAMMapLineItemRequest = Body(openapi_examples=GAM_LINE_ITEM_EXAMPLES),
    explain: bool = _explain(),
):
    """Map a sold GAM ``LineItem`` (with its ``Order``) to an OpenProposal line item.

    House, network, bulk and other non-sale line item types return 422.
    """
    try:
        mapped = openproposal_service.line_item_from_gam_line_item(
            request.line_item, request.order, request.network_code
        )
    except openproposal_service.UnmappableError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "unmappable_line_item", "message": str(exc)},
        ) from exc
    return mapped if explain else mapped.line_item
