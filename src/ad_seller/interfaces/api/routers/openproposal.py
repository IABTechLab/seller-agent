# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Direct GAM → OpenProposal 3.0 line item mapping.

Read-only: these routes take GAM payloads (API v202608, as returned by
``LineItemService`` / ``InventoryService``) and return OpenProposal line
items, with an optional field-by-field analysis of what did not map.
"""

from fastapi import APIRouter, Body, HTTPException, Query

from ....models.openproposal import (
    FieldRule,
    GAMBatchResult,
    GAMMapAdUnitRequest,
    GAMMapLineItemRequest,
    GAMMapLineItemsRequest,
    GAMMapLineItemsResponse,
    LineItem,
    ManyToOneProposalRequest,
    ManyToOneProposalResponse,
    MappedLineItem,
)
from ....services import openproposal_service

router = APIRouter()

TAG = "OpenProposal (GAM mapping)"

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


RICH_LINE_ITEM = {
    "id": 7202154880,
    "orderId": 3961795098,
    "name": "Sports section takeover - Q4",
    "lineItemType": "SPONSORSHIP",
    "status": "DELIVERING",
    "reservationStatus": "RESERVED",
    "priority": 4,
    "costType": "CPD",
    "costPerUnit": {"currencyCode": "USD", "microAmount": 5000000000},
    "discountType": "PERCENTAGE",
    "discount": 10.0,
    "contractedUnitsBought": 30,
    "primaryGoal": {"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100},
    "startDateTime": {
        "date": {"year": 2026, "month": 10, "day": 1},
        "hour": 0,
        "minute": 0,
        "second": 0,
        "timeZoneId": "America/New_York",
    },
    "unlimitedEndDateTime": True,
    "deliveryRateType": "FRONTLOADED",
    "roadblockingType": "AS_MANY_AS_POSSIBLE",
    "environmentType": "BROWSER",
    "frequencyCaps": [
        {"maxImpressions": 3, "numTimeUnits": 1, "timeUnit": "DAY"},
        {"maxImpressions": 10, "numTimeUnits": 1, "timeUnit": "WEEK"},
    ],
    "creativePlaceholders": [
        {"size": {"width": 970, "height": 250}, "creativeSizeType": "PIXEL"},
        {"size": {"width": 300, "height": 600}, "creativeSizeType": "PIXEL"},
    ],
    "targeting": {
        "inventoryTargeting": {
            "targetedAdUnits": [{"adUnitId": "17980522", "includeDescendants": True}],
            "excludedAdUnits": [{"adUnitId": "513300682", "includeDescendants": True}],
        },
        "geoTargeting": {
            "targetedLocations": [{"id": 2840}],
            "excludedLocations": [{"id": 21137}],
        },
        "customTargeting": {
            "logicalOperator": "OR",
            "children": [{"keyId": 1, "valueIds": [11], "operator": "IS"}],
        },
    },
    "appliedLabels": [{"labelId": 99, "isNegated": False}],
    "notes": "Homepage and sports takeover",
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
    "sponsorship_with_problems": {
        "summary": "Sponsorship showing most mapping problems",
        "value": {
            "network_code": "12345678",
            "order": {"id": 3961795098, "currencyCode": "USD", "advertiserId": 555},
            "line_item": RICH_LINE_ITEM,
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


GAM_LINE_ITEMS_BATCH_EXAMPLES = {
    "three_line_items": {
        "summary": "Standard, sponsorship and house line items",
        "value": {
            "network_code": "12345678",
            "orders": [
                {"id": 3961795097, "currencyCode": "USD"},
                {"id": 3961795098, "currencyCode": "USD", "advertiserId": 555},
            ],
            "line_items": [
                GAM_LINE_ITEM_EXAMPLES["standard_pg_line"]["value"]["line_item"],
                RICH_LINE_ITEM,
                GAM_LINE_ITEM_EXAMPLES["house_line"]["value"]["line_item"],
            ],
        },
    }
}


MANY_TO_ONE_EXAMPLES = {
    "three_ad_servers": {
        "summary": "One seller, GAM + Kevel + FreeWheel (ManyToOneRevised sample)",
        "value": {
            "proposal_id": "examplepub-cross-channel-open",
            "seller_id": "examplepub",
            "seller_name": "Example Publisher US",
            "type": "standard",
            "description": "Business audiences across web, newsletter and CTV.",
            "valid_from": "2026-10-20T00:00:00Z",
            "valid_until": "2026-12-31T23:59:59Z",
            "line_items": [
                {
                    "line_item_id": "business-web-display",
                    "serving_platform": "gam",
                    "namespace": "network:21700000",
                    "objects": [
                        {
                            "domains": ["business-daily.example"],
                            "gam_ad_unit": {
                                "id": "21780011",
                                "name": "Business Daily",
                                "adUnitSizes": [
                                    {"size": {"width": 300, "height": 250}},
                                    {"size": {"width": 728, "height": 90}},
                                ],
                            },
                        },
                        {
                            "domains": ["markets-weekly.example"],
                            "gam_ad_unit": {
                                "id": "21780019",
                                "name": "Markets Weekly",
                                "adUnitSizes": [{"size": {"width": 300, "height": 250}}],
                            },
                        },
                    ],
                    "included": ["21780011"],
                    "option_pricing": {"21780019": {"delta_cpm": 2.0}},
                    "pricing": {"cost_method": "CPM", "gross_rate": 18.0, "currency": "USD"},
                    "availability": {
                        "unit": "impressions",
                        "basis": "flight",
                        "as_of": "2026-10-19",
                        "granularity": "line_item",
                        "type": "forecast",
                        "quantity": 6200000,
                    },
                },
                {
                    "line_item_id": "business-newsletter-display",
                    "serving_platform": "kevel",
                    "namespace": "network:9120",
                    "objects": [
                        {
                            "id": "4401",
                            "role": "zone",
                            "name": "Daily newsletter",
                            "domains": ["newsletter.example"],
                            "formats": ["display/600x200"],
                            "channel": "display",
                        }
                    ],
                    "pricing": {"cost_method": "CPM", "gross_rate": 21.0, "currency": "USD"},
                    "availability": {"type": "forecast", "quantity": 300000, "as_of": "2026-10-19"},
                },
                {
                    "line_item_id": "weather-ctv",
                    "serving_platform": "freewheel",
                    "namespace": "network:169843",
                    "serving_mode": "ssai",
                    "objects": [
                        {
                            "id": "88120",
                            "role": "site_section",
                            "name": "Weather app",
                            "apps": [{"platform": "roku", "bundle": "weatherapp.roku"}],
                            "formats": ["ctv/instream-15s", "ctv/instream-30s"],
                            "channel": "ctv",
                        }
                    ],
                },
            ],
        },
    },
    "invalid_two_channels": {
        "summary": "Rejected: one line item mixing display and CTV",
        "value": {
            "proposal_id": "bad-mix",
            "seller_id": "examplepub",
            "seller_name": "Example Publisher US",
            "description": "Display and CTV in one line item",
            "line_items": [
                {
                    "line_item_id": "mixed",
                    "serving_platform": "freewheel",
                    "objects": [
                        {"id": "1", "channel": "display", "formats": ["display/300x250"]},
                        {"id": "2", "channel": "ctv", "formats": ["ctv/instream-15s"]},
                    ],
                }
            ],
        },
    },
}


def _explain() -> bool:
    return Query(
        default=False,
        description="Add provenance, gaps, unmapped GAM fields, the field report and problems.",
        openapi_examples={
            "plain": {"summary": "OpenProposal line item only", "value": False},
            "explain": {"summary": "With the full mapping analysis", "value": True},
        },
    )


@router.get(
    "/api/v1/op-gam/line-item/field-map",
    tags=[TAG],
    response_model=list[FieldRule],
    response_model_exclude_none=True,
)
async def get_gam_line_item_field_map() -> list[FieldRule]:
    """Every GAM ``LineItem`` and ``Order`` field and where it lands in OpenProposal.

    ``status`` is one of mapped, transformed, lossy, no_home or
    not_applicable; ``problem`` names the mapping problem it raises.
    """
    return list(
        openproposal_service.GAM_LINE_ITEM_FIELD_MAP + openproposal_service.GAM_ORDER_FIELD_MAP
    )


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
    """Map one GAM ``LineItem`` (with its ``Order``) to an OpenProposal line item.

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


@router.post(
    "/api/v1/op-gam/line-items",
    tags=[TAG],
    response_model=GAMMapLineItemsResponse,
    response_model_exclude_none=True,
)
async def map_gam_line_items(
    request: GAMMapLineItemsRequest = Body(openapi_examples=GAM_LINE_ITEMS_BATCH_EXAMPLES),
    explain: bool = _explain(),
) -> GAMMapLineItemsResponse:
    """Map many GAM line items and summarise the mapping problems across them.

    Paste a ``LineItemService`` export. Non-sale types are counted in
    ``summary.unmappable_by_type`` instead of failing the request.
    """
    results, summary = openproposal_service.map_gam_line_items(
        request.line_items, request.orders, request.network_code
    )
    if not explain:
        results = [
            GAMBatchResult(
                gam_line_item_id=r.gam_line_item_id,
                line_item=r.mapped.line_item if r.mapped else None,
                error=r.error,
            )
            for r in results
        ]
    return GAMMapLineItemsResponse(summary=summary, results=results)


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
    """Map a GAM ``AdUnit`` to a draft OpenProposal line item (inventory, no price yet)."""
    mapped = openproposal_service.line_item_from_gam_ad_unit(request.ad_unit, request.network_code)
    return mapped if explain else mapped.line_item


@router.post(
    "/api/v1/op-many-to-one/proposal",
    tags=[TAG],
    response_model=ManyToOneProposalResponse,
    response_model_exclude_none=True,
)
async def build_many_to_one_proposal(
    request: ManyToOneProposalRequest = Body(openapi_examples=MANY_TO_ONE_EXAMPLES),
    explain: bool = _explain(),
) -> ManyToOneProposalResponse:
    """Build one proposal from objects in several ad servers (ManyToOneRevised.md).

    Rules: one ad server and one channel per line item; several objects from the
    same ad server combine into one line item (each a property option);
    ``availability`` is one ad-server forecast for the default selection, never a
    sum; every source object is listed in ``external_references`` with its ad
    server account. Breaking a rule returns 422 with every error.
    """
    try:
        result = openproposal_service.proposal_from_many_to_one(request)
    except openproposal_service.ManyToOneError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_many_to_one", "errors": exc.errors},
        ) from exc
    return result if explain else ManyToOneProposalResponse(proposal=result.proposal)
