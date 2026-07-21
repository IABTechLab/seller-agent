# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenDirect 2.1 Tier-1 wire-dialect conformance.

The OpenDirect v2.1 normative attribute tables use ALL-LOWERCASE field
names; the seller's avails schemas historically used camelCase aliases.
These tests pin the corrected Tier-1 dialect on the seller's OpenDirect
surface (POST /products/avails):

* spec-named fields parse/serialize under their spec-lowercase names
  (``productid``, ``startdate``, ``enddate``);
* the old camelCase spec-field names are NO LONGER accepted or emitted;
* non-spec extension fields (``requestedImpressions``,
  ``availableImpressions``, ...) are unchanged pending the Tier-2
  restructure (they have no spec name to converge to).

The ``BUYER_AVAILS_REQUEST_WIRE`` / ``SELLER_AVAILS_RESPONSE_WIRE``
payloads are mirrored byte-for-byte in the buyer repo
(``tests/unit/test_opendirect_wire_conformance.py`` there): the seller
must ACCEPT exactly what the buyer emits, and EMIT exactly what the
buyer parses. Keep the two files in lockstep.
"""

import json

import pytest
from pydantic import ValidationError

from ad_seller.interfaces.api.schemas import AvailsRequest, AvailsResponse

# --- Mirrored cross-repo payloads (identical constants in the buyer repo) ---

BUYER_AVAILS_REQUEST_WIRE = {
    "productid": "prod-display-001",
    "startdate": "2026-08-01T00:00:00Z",
    "enddate": "2026-08-31T23:59:59Z",
    "requestedImpressions": 500000,
    "budget": 6000.0,
    "targeting": {"geo": ["US"], "device": ["mobile"]},
}

SELLER_AVAILS_RESPONSE_WIRE = {
    "productid": "prod-display-001",
    "availableImpressions": 750000,
    "guaranteedImpressions": 500000,
    "estimatedCpm": 12.0,
    "totalCost": 6000.0,
    "deliveryConfidence": None,
    "availableTargeting": None,
}


class TestAvailsRequestDialect:
    """The seller accepts exactly the buyer's Tier-1 wire request."""

    def test_accepts_buyer_wire_request(self):
        req = AvailsRequest.model_validate(BUYER_AVAILS_REQUEST_WIRE)
        assert req.product_id == "prod-display-001"
        assert req.requested_impressions == 500000

    def test_rejects_legacy_camelcase_request(self):
        legacy = {
            "productId": "prod-display-001",
            "startDate": "2026-08-01T00:00:00Z",
            "endDate": "2026-08-31T23:59:59Z",
        }
        with pytest.raises(ValidationError):
            AvailsRequest.model_validate(legacy)

    def test_python_field_names_still_populate(self):
        """populate_by_name=True keeps snake_case construction working."""
        req = AvailsRequest(
            product_id="p1",
            start_date="2026-08-01T00:00:00Z",
            end_date="2026-08-31T00:00:00Z",
        )
        assert req.product_id == "p1"


class TestAvailsResponseDialect:
    """The seller emits exactly the wire shape the buyer parses."""

    def test_response_emits_exact_wire_shape(self):
        resp = AvailsResponse(
            product_id="prod-display-001",
            available_impressions=750000,
            guaranteed_impressions=500000,
            estimated_cpm=12.0,
            total_cost=6000.0,
            delivery_confidence=None,
            available_targeting=None,
        )
        # FastAPI serializes response_model by alias with nulls kept.
        wire = json.loads(resp.model_dump_json(by_alias=True))
        assert wire == SELLER_AVAILS_RESPONSE_WIRE

    def test_legacy_productid_case_not_emitted(self):
        resp = AvailsResponse(
            product_id="p1",
            available_impressions=1,
            estimated_cpm=1.0,
            total_cost=1.0,
        )
        wire = json.loads(resp.model_dump_json(by_alias=True))
        assert "productid" in wire
        assert "productId" not in wire
