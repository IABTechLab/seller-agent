# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal lifecycle service.

Extracted from ``interfaces/api/main.py`` (EP-3.1): quote-to-deal booking,
template-based creation, curated deals, bulk operations, DSP export/push,
SSP distribution, and migration/deprecation/lineage.

Behavior-preserving — error semantics are expressed as ``HTTPException``
exactly as the endpoints raised them, and imports of storage/flows/clients
stay function-level so existing test patch points keep working.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Dedicated logger for booking-time forensic events. Per proposal §5.1 Step 2
# / §6 row 14b, the seller logs the `audience_plan_id` hash at the moment a
# deal is minted carrying an audience plan. The buyer logs the same hash on
# its side via `ad_buyer.audience.booking`. Matching log entries are the
# forensic anchor for any future dispute about what was actually frozen.
booking_logger = logging.getLogger("ad_seller.audience.booking")


# =============================================================================
# Agentic audience match scoring (proposal §5.7 + §6 row 11)
# =============================================================================


def agentic_match_quality(score: float) -> str:
    """Bucket a [0, 1] match score into the spec's quality labels."""

    if score >= 0.85:
        return "STRONG"
    if score >= 0.65:
        return "MODERATE"
    if score >= 0.4:
        return "WEAK"
    return "POOR"


def deterministic_score(identifier: str) -> float:
    """sha256-derived deterministic [0, 1] mock score.

    Mock-quality is fine here -- per proposal §7, the SHA256-seeded mock is
    explicitly the load-bearing fake under every "agentic match score" we
    display in Epic 1; the real model is Epic 2 / E2-2.
    """

    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    # First 8 hex chars -> 32-bit unsigned int -> normalized to [0, 1].
    return int(digest[:8], 16) / 0xFFFFFFFF


# Wire-format §6.5 match-bucket labels. Note these differ from the
# `agentic_match_quality` labels used by `/agentic-audience/match`
# (which uses POOR for the lowest bucket); the booking response uses the
# `BookingResponse.MatchEntry` enum from the wire-format spec, which has
# `NONE` instead of `POOR`. Keeping the two scales separate avoids breaking
# the §11 endpoint contract while satisfying §6.5 on the booking surface.
def booking_match_label(score: float) -> str:
    """Wire-format §6.5 match-bucket label for a [0, 1] score."""

    if score >= 0.85:
        return "STRONG"
    if score >= 0.65:
        return "MODERATE"
    if score >= 0.4:
        return "WEAK"
    return "NONE"


def score_for_ref(ref: dict[str, Any]) -> float:
    """Deterministic mock match score for a single ref.

    Standard / contextual refs score against their `identifier`; agentic
    refs score against their embedding URI. Score range [0, 1]. Real
    similarity scoring is Epic 2 / E2-2; for Epic 1 the deterministic mock
    matches the rest of the seller's scoring surface.
    """

    identifier = ref.get("identifier") or ""
    return deterministic_score(identifier)


def match_entry_for_ref(ref: dict[str, Any]) -> dict[str, Any]:
    """Build one wire-format §6.5 `MatchEntry` for a single ref."""

    score = score_for_ref(ref)
    return {"match": booking_match_label(score), "score": round(score, 4)}


def build_audience_match_summary(plan: dict[str, Any]) -> dict[str, Any]:
    """Assemble the wire-format §6.5 `audience_match_summary` for a plan.

    Returns the four-role shape (`primary`, `constraints`, `extensions`,
    `exclusions`) -- per the schema, empty arrays MAY be omitted but
    receivers MUST treat absence as empty, so we always emit them so the
    buyer's typed parser has stable structure.
    """

    summary: dict[str, Any] = {
        "primary": match_entry_for_ref(plan.get("primary") or {}),
        "constraints": [match_entry_for_ref(r) for r in (plan.get("constraints") or [])],
        "extensions": [match_entry_for_ref(r) for r in (plan.get("extensions") or [])],
        "exclusions": [match_entry_for_ref(r) for r in (plan.get("exclusions") or [])],
    }
    return summary


def match_agentic_audience(ref: dict[str, Any]) -> dict[str, Any]:
    """Match a buyer-supplied agentic `AudienceRef` against this seller.

    Per proposal §5.7 + §6 row 11. Returns a match score and quality bucket.
    The score is mock-quality (deterministic from sha256 of `identifier`);
    the real embedding-similarity model is Epic 2 (E2-2).
    """
    from ..models.audience_capabilities import build_capability_audience_block

    ref = ref or {}
    if ref.get("type") != "agentic":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_audience_ref",
                "message": "POST /agentic-audience/match requires audience_ref.type='agentic'",
            },
        )

    identifier = ref.get("identifier") or ""
    if not identifier:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_audience_ref",
                "message": "audience_ref.identifier is required",
            },
        )

    seller_caps = build_capability_audience_block()
    agentic_supported = bool(seller_caps.agentic.supported)

    if not agentic_supported:
        return {
            "audience_ref": ref,
            "match_confidence": 0.0,
            "match_quality": "POOR",
            "matched_capabilities": [],
            "agentic_supported_by_seller": False,
            "rationale": (
                "Seller does not advertise top-level agentic capability "
                "(audience_capabilities.agentic.supported=false); returning POOR."
            ),
        }

    score = deterministic_score(identifier)
    quality = agentic_match_quality(score)

    # `matched_capabilities` is a placeholder for the real model's
    # per-signal-type breakdown (E2-2). For now we mirror the seller's
    # advertised top-level agentic flag as a single capability label.
    matched: list[str] = []
    if quality != "POOR":
        matched.append("agentic")

    return {
        "audience_ref": ref,
        "match_confidence": round(score, 4),
        "match_quality": quality,
        "matched_capabilities": matched,
        "agentic_supported_by_seller": True,
        "rationale": (
            f"Deterministic mock score {round(score, 4)} -> {quality}. "
            "Real similarity model is tracked in Epic 2 (E2-2)."
        ),
    }


# =============================================================================
# Quote-to-deal booking (IAB Deals API v1.0)
# =============================================================================


async def book_deal(request: Any) -> dict[str, Any]:
    """Book a deal from a previously issued quote (``DealBookingRequestModel``).

    Validates the quote, generates a Deal ID, and returns confirmed terms.
    This is the commit point — the quote becomes bound. When the request
    carries an ``audience_plan``, the plan is validated against seller
    capabilities, frozen onto the deal record as ``audience_plan_snapshot``,
    and the forensic hash is logged via ``ad_seller.audience.booking``.
    """
    from ..models.quotes import DealBookingResponse, DealBookingStatus, QuoteStatus
    from ..storage.factory import get_storage

    storage = await get_storage()

    # Retrieve the quote
    quote = await storage.get_quote(request.quote_id)
    if not quote:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "quote_not_found",
                "message": f"Quote '{request.quote_id}' not found.",
            },
        )

    # Lazy expiry check
    if quote.get("expires_at"):
        expires = datetime.fromisoformat(quote["expires_at"].rstrip("Z"))
        if datetime.utcnow() > expires:
            quote["status"] = QuoteStatus.EXPIRED.value
            await storage.set_quote(request.quote_id, quote, ttl=3600)
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "quote_expired",
                    "message": "Quote has expired. Request a new quote.",
                },
            )

    # Validate status — must be "available"
    if quote.get("status") != QuoteStatus.AVAILABLE.value:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "quote_already_booked",
                "message": f"Quote status is '{quote.get('status')}', expected 'available'.",
            },
        )

    # Pre-flight: if the buyer sent an audience_plan with this booking, validate
    # it against the seller's capability block. Per proposal §5.7 layer 3, any
    # unsupported part triggers a structured `audience_plan_unsupported` 400 so
    # the buyer's degrade_plan_for_seller() can retry. (Bead ar-sn8f.)
    if request.audience_plan:
        from ..models.audience_capabilities import build_capability_audience_block
        from .audience_plan_validator import validate_audience_plan

        seller_caps = build_capability_audience_block()
        unsupported = validate_audience_plan(request.audience_plan, seller_caps)
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "audience_plan_unsupported",
                    "unsupported": unsupported,
                },
            )

    # Generate deal
    now = datetime.utcnow()
    deal_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"
    deal_expires = now + timedelta(days=30)

    from ..models.quotes import QuotePricing, QuoteProductInfo, QuoteTerms

    deal = DealBookingResponse(
        deal_id=deal_id,
        deal_type=quote["deal_type"],
        status=DealBookingStatus.PROPOSED,
        quote_id=request.quote_id,
        product=QuoteProductInfo(**quote["product"]),
        pricing=QuotePricing(**quote["pricing"]),
        terms=QuoteTerms(**quote["terms"]),
        buyer_tier=quote.get("buyer_tier", "public"),
        expires_at=deal_expires.isoformat() + "Z",
        activation_instructions={
            "ttd": f"In The Trade Desk, create a new PMP deal with Deal ID: {deal_id}",
            "dv360": f"In DV360, add deal {deal_id} under Inventory > My Inventory > Deals",
            "xandr": f"In Xandr, navigate to Deals and enter Deal ID: {deal_id}",
        },
        openrtb_params={
            "id": deal_id,
            "bidfloor": quote["pricing"]["final_cpm"],
            "bidfloorcur": "USD",
            "at": 3 if quote["deal_type"] == "PA" else 1,
            "wseat": [],
        },
        created_at=now.isoformat() + "Z",
    )

    deal_data = deal.model_dump(mode="json")

    # Freeze the audience plan onto the deal record + compute per-role match
    # summary (proposal §5.1 Step 2 + wire-format §6.5). Both fields are added
    # only when the buyer supplied an audience_plan; legacy bookings remain
    # byte-for-byte identical.
    if request.audience_plan:
        plan_snapshot = dict(request.audience_plan)
        deal_data["audience_plan_snapshot"] = plan_snapshot
        deal_data["audience_match_summary"] = build_audience_match_summary(plan_snapshot)

        # Forensic anchor hash log (proposal §5.1 Step 2 / bead 14b). Buyer
        # logs the same hash on its side via `ad_buyer.audience.booking`.
        plan_id = plan_snapshot.get("audience_plan_id") or ""
        booking_logger.info(
            "deal_booking deal_id=%s audience_plan_id=%s quote_id=%s",
            deal_id,
            plan_id,
            request.quote_id,
        )

    # Update quote status to "booked" and link deal_id
    quote["status"] = QuoteStatus.BOOKED.value
    quote["deal_id"] = deal_id
    await storage.set_quote(request.quote_id, quote, ttl=86400)

    # Store the deal in deal storage (coexists with proposal-based deals).
    # The snapshot fields land on the persisted record so
    # `honor_audience_plan_snapshot()` can read them at fulfillment time.
    await storage.set_deal(deal_id, deal_data)

    return deal_data


async def get_deal(deal_id: str) -> dict[str, Any]:
    """Get the current status of a deal (lazy expiry for 'proposed')."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    deal = await storage.get_deal(deal_id)

    if not deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    # Lazy expiry check for proposed deals
    if deal.get("status") == "proposed" and deal.get("expires_at"):
        expires = datetime.fromisoformat(deal["expires_at"].rstrip("Z"))
        if datetime.utcnow() > expires:
            deal["status"] = "expired"
            await storage.set_deal(deal_id, deal)

    return deal


async def get_deal_performance(deal_id: str) -> dict[str, Any]:
    """Return delivery stats for a deal.

    Provides performance feedback for buyer SPO (Supply Path Optimization).

    If GAM is configured and the deal has a linked ``gam_order_id`` (set when
    the deal is trafficked into GAM), returns real delivery data from the ad
    server via ``GAMSoapClient.get_delivery_report`` (main PR #12 wiring,
    re-grounded in the service layer — bead ar-j8hl). Otherwise returns
    placeholder stats.
    """
    from ..config import get_settings
    from ..storage.factory import get_storage

    storage = await get_storage()
    deal = await storage.get_deal(deal_id)

    if not deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    now = datetime.utcnow().isoformat() + "Z"
    settings = get_settings()

    # Real path: GAM configured + deal was trafficked into GAM
    gam_order_id = deal.get("gam_order_id") or (deal.get("metadata") or {}).get("gam_order_id")
    if (
        settings.gam_enabled
        and settings.gam_network_code
        and settings.gam_json_key_path
        and gam_order_id
    ):
        try:
            from ..clients.gam_soap_client import GAMSoapClient

            client = GAMSoapClient()
            client.connect()
            report = client.get_delivery_report([str(gam_order_id)], days=30)
            client.disconnect()

            summary = report.get("summary", {})
            impressions_served = summary.get("impressions", 0)
            impressions_available = 0
            for order in report.get("orders", []):
                for li in order.get("line_items", []):
                    goal = li.get("impressions_goal", 0)
                    if goal and goal > 0:
                        impressions_available += goal

            fill_rate = (
                round(impressions_served / impressions_available * 100, 1)
                if impressions_available
                else 0.0
            )
            revenue = summary.get("revenue_usd", 0.0)
            avg_cpm = round(revenue / impressions_served * 1000, 2) if impressions_served else 0.0
            pacing = (
                "not_started"
                if impressions_served == 0
                else "on_track"
                if fill_rate >= 40
                else "behind"
            )

            return {
                "deal_id": deal_id,
                "impressions_available": impressions_available,
                "impressions_served": impressions_served,
                "fill_rate": fill_rate,
                "win_rate": 0.0,
                "avg_cpm_actual": avg_cpm,
                "delivery_pacing": pacing,
                "last_updated": now,
            }
        except Exception:
            logger.exception(
                "GAM delivery report failed for deal %s (order %s) — "
                "falling back to placeholder stats",
                deal_id,
                gam_order_id,
            )

    # Fallback: placeholder stats (GAM not configured or order not yet trafficked)
    return {
        "deal_id": deal_id,
        "impressions_available": 1000000,
        "impressions_served": 0,
        "fill_rate": 0.0,
        "win_rate": 0.0,
        "avg_cpm_actual": 0.0,
        "delivery_pacing": "not_started",
        "last_updated": now,
    }


# =============================================================================
# Legacy proposal-based deal generation (POST /deals)
# =============================================================================


def generate_deal_from_proposal(proposal_id: str) -> dict[str, Any]:
    """Generate a deal from an accepted proposal (legacy demo flow)."""
    from ..flows import DealGenerationFlow

    flow = DealGenerationFlow()
    result = flow.generate_deal(
        proposal_id=proposal_id,
        proposal_data={
            "status": "accepted",
            "deal_type": "preferred_deal",
            "price": 15.0,
            "product_id": "display",
            "impressions": 1000000,
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
        },
    )

    if not result.get("deal_id"):
        raise HTTPException(status_code=400, detail="Failed to generate deal")

    return result


# =============================================================================
# Template-based deal creation (Deal Library Phase 4)
# =============================================================================


async def create_deal_from_template(
    request: Any,
    buyer_context: Any,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Create a deal directly from template parameters (quote + auto-book).

    Product resolution now reads the single cached catalog source
    (EP-3.3) instead of running ProductSetupFlow per request.
    Returns the created deal data dict (includes ``schain``).
    """
    from ..engines.pricing_rules_engine import PricingRulesEngine
    from ..models.pricing_tiers import TieredPricingConfig
    from ..models.quotes import DealBookingStatus
    from ..storage.factory import get_storage

    deal_type_map = _quote_deal_type_map()
    deal_type_str = request.deal_type.upper()
    if deal_type_str not in deal_type_map:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_deal_type",
                "message": f"Deal type must be one of: PG, PD, PA. Got: {request.deal_type}",
            },
        )

    # PG requires impressions
    if deal_type_str == "PG" and not request.impressions:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "pg_requires_impressions",
                "message": "Programmatic Guaranteed deals require an impressions count.",
            },
        )

    product = catalog["products"].get(request.product_id)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "product_not_found",
                "message": f"Product '{request.product_id}' not found in catalog.",
            },
        )

    # Calculate price. Honest pricing: base_cpm falling back to floor_cpm;
    # unpriced products are a 422, never a fabricated price (ar-92f8).
    from . import catalog_service

    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)
    deal_type_enum = deal_type_map[deal_type_str]

    decision = engine.calculate_price(
        product_id=request.product_id,
        base_price=catalog_service.priceable_cpm(product),
        buyer_context=buyer_context,
        deal_type=deal_type_enum,
        volume=request.impressions or 0,
        inventory_type=product.inventory_type,
    )

    final_cpm = decision.final_price

    # Check max_cpm against floor
    if request.max_cpm is not None and request.max_cpm < final_cpm:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "below_floor_price",
                "message": f"Buyer max CPM ${request.max_cpm:.2f} is below seller minimum ${final_cpm:.2f}.",
                "seller_minimum_cpm": final_cpm,
                "buyer_max_cpm": request.max_cpm,
                "product_id": request.product_id,
                "deal_type": deal_type_str,
            },
        )

    # Auto-book: generate deal directly (skip separate quote step)
    now = datetime.utcnow()
    deal_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"

    flight_start = request.flight_start or now.strftime("%Y-%m-%d")
    flight_end = request.flight_end or (now + timedelta(days=30)).strftime("%Y-%m-%d")

    deal_data = {
        "deal_id": deal_id,
        "deal_type": deal_type_str,
        "status": DealBookingStatus.CONFIRMED.value,
        "product_id": request.product_id,
        "actual_price_cpm": final_cpm,
        "currency": "USD",
        "impressions": request.impressions,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "buyer_tier": buyer_context.effective_tier.value,
        "notes": request.notes,
        "created_at": now.isoformat() + "Z",
        "activation_instructions": {
            "ttd": f"In The Trade Desk, create a new PMP deal with Deal ID: {deal_id}",
            "dv360": f"In DV360, add deal {deal_id} under Inventory > My Inventory > Deals",
            "amazon": f"In Amazon DSP, navigate to Supply > Deals and add Deal ID: {deal_id}",
            "xandr": f"In Xandr, navigate to Deals and enter Deal ID: {deal_id}",
        },
    }

    # Build schain for the deal response
    deal_data["schain"] = _build_seller_schain()

    storage = await get_storage()
    await storage.set_deal(deal_id, deal_data)

    return deal_data


def _quote_deal_type_map():
    from ..models.core import DealType

    return {
        "PG": DealType.PROGRAMMATIC_GUARANTEED,
        "PD": DealType.PREFERRED_DEAL,
        "PA": DealType.PRIVATE_AUCTION,
    }


def _build_seller_schain() -> dict[str, Any]:
    """Build the seller-side schain object (sellers.json-backed or default)."""
    from ..config import get_settings
    from ..models.supply_chain import build_schain_from_sellers_json, load_sellers_json

    _settings = get_settings()
    _sellers_json_path = getattr(_settings, "sellers_json_path", None)
    _sellers_json = load_sellers_json(_sellers_json_path) if _sellers_json_path else None
    if _sellers_json:
        _seller_id = getattr(_settings, "seller_organization_id", "default")
        schain_obj = build_schain_from_sellers_json(_sellers_json, _seller_id)
        return schain_obj.model_dump()

    _seller_domain = getattr(_settings, "seller_domain", "demo-publisher.example.com")
    _seller_org_name = getattr(_settings, "seller_organization_name", "Demo Publisher")
    return {
        "ver": "1.0",
        "complete": 1,
        "nodes": [
            {
                "asi": _seller_domain,
                "sid": "default",
                "hp": 1,
                "name": _seller_org_name,
                "domain": _seller_domain,
            }
        ],
    }


# =============================================================================
# Bulk deal operations (Deal Library Phase 5)
# =============================================================================


async def bulk_deal_operations(operations: list[Any]) -> list[dict[str, Any]]:
    """Process a batch of deal operations (create/update/cancel).

    Each operation is processed independently; returns per-operation
    result dicts (index/action/success/deal_id/error).
    """
    from ..models.quotes import DealBookingStatus, QuoteStatus
    from ..storage.factory import get_storage

    storage = await get_storage()
    results: list[dict[str, Any]] = []

    def _result(index: int, action: str, success: bool, deal_id=None, error=None):
        return {
            "index": index,
            "action": action,
            "success": success,
            "deal_id": deal_id,
            "error": error,
        }

    for i, op in enumerate(operations):
        try:
            if op.action == "create":
                if not op.quote_id:
                    results.append(
                        _result(i, "create", False, error="quote_id is required for create")
                    )
                    continue

                quote = await storage.get_quote(op.quote_id)
                if not quote:
                    results.append(
                        _result(i, "create", False, error=f"Quote '{op.quote_id}' not found")
                    )
                    continue

                if quote.get("status") != QuoteStatus.AVAILABLE.value:
                    results.append(
                        _result(
                            i,
                            "create",
                            False,
                            error=f"Quote status is '{quote.get('status')}', expected 'available'",
                        )
                    )
                    continue

                # Generate deal
                now = datetime.utcnow()
                deal_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"

                deal_data = {
                    "deal_id": deal_id,
                    "quote_id": op.quote_id,
                    "status": DealBookingStatus.CONFIRMED.value,
                    "created_at": now.isoformat() + "Z",
                    "notes": op.notes,
                }
                await storage.set_deal(deal_id, deal_data)

                # Mark quote as booked
                quote["status"] = QuoteStatus.BOOKED.value
                await storage.set_quote(op.quote_id, quote)

                results.append(_result(i, "create", True, deal_id=deal_id))

            elif op.action == "cancel":
                if not op.deal_id:
                    results.append(
                        _result(i, "cancel", False, error="deal_id is required for cancel")
                    )
                    continue

                deal = await storage.get_deal(op.deal_id)
                if not deal:
                    results.append(
                        _result(i, "cancel", False, error=f"Deal '{op.deal_id}' not found")
                    )
                    continue

                deal["status"] = "cancelled"
                deal["cancelled_at"] = datetime.utcnow().isoformat() + "Z"
                deal["cancel_reason"] = op.notes or "Cancelled via bulk operation"
                await storage.set_deal(op.deal_id, deal)

                results.append(_result(i, "cancel", True, deal_id=op.deal_id))

            elif op.action == "update":
                if not op.deal_id:
                    results.append(
                        _result(i, "update", False, error="deal_id is required for update")
                    )
                    continue

                deal = await storage.get_deal(op.deal_id)
                if not deal:
                    results.append(
                        _result(i, "update", False, error=f"Deal '{op.deal_id}' not found")
                    )
                    continue

                if op.notes:
                    deal["notes"] = op.notes
                deal["updated_at"] = datetime.utcnow().isoformat() + "Z"
                await storage.set_deal(op.deal_id, deal)

                results.append(_result(i, "update", True, deal_id=op.deal_id))

            else:
                results.append(
                    _result(
                        i,
                        op.action,
                        False,
                        error=f"Unknown action '{op.action}'. Must be create, update, or cancel.",
                    )
                )

        except Exception as e:
            results.append(_result(i, op.action, False, error=str(e)))

    return results


# =============================================================================
# Deal export for DSP connectors (Deal Library Phase 4)
# =============================================================================


async def export_deals(format: str = "generic", status: Optional[str] = None) -> dict[str, Any]:
    """Export deals in DSP-native format for platform connectors."""
    from ..storage.factory import get_storage

    storage = await get_storage()

    # Collect all deals (scan deal:* keys)
    all_deals = []
    # Storage doesn't have a list_deals method, so we track deal IDs
    deal_index = await storage.get("deal_index") or {"deal_ids": []}

    for deal_id in deal_index.get("deal_ids", []):
        deal = await storage.get_deal(deal_id)
        if deal:
            if status and deal.get("status") != status:
                continue
            all_deals.append(deal)

    if format == "ttd":
        # The Trade Desk format
        return {
            "format": "ttd",
            "deals": [
                {
                    "DealId": d.get("deal_id"),
                    "DealType": "ProgrammaticGuaranteed"
                    if d.get("deal_type") == "PG"
                    else "PreferredDeal"
                    if d.get("deal_type") == "PD"
                    else "PrivateAuction",
                    "BidFloor": d.get("actual_price_cpm")
                    or d.get("pricing", {}).get("final_cpm", 0),
                    "Currency": "USD",
                    "Status": "Active" if d.get("status") == "confirmed" else "Inactive",
                }
                for d in all_deals
            ],
        }
    elif format == "dv360":
        # Display & Video 360 format
        return {
            "format": "dv360",
            "deals": [
                {
                    "dealId": d.get("deal_id"),
                    "displayName": f"Deal {d.get('deal_id')}",
                    "dealType": d.get("deal_type", "PD"),
                    "fixedCpm": {
                        "currencyCode": "USD",
                        "units": str(int(d.get("actual_price_cpm", 0) or 0)),
                        "nanos": 0,
                    },
                    "status": "ACCEPTED" if d.get("status") == "confirmed" else "PENDING",
                }
                for d in all_deals
            ],
        }
    elif format == "amazon":
        # Amazon DSP format
        return {
            "format": "amazon",
            "deals": [
                {
                    "dealId": d.get("deal_id"),
                    "dealName": f"Deal {d.get('deal_id')}",
                    "auctionType": "FIXED_PRICE"
                    if d.get("deal_type") in ("PG", "PD")
                    else "SECOND_PRICE",
                    "priceAmount": d.get("actual_price_cpm")
                    or d.get("pricing", {}).get("final_cpm", 0),
                    "priceCurrency": "USD",
                }
                for d in all_deals
            ],
        }
    elif format == "xandr":
        # Xandr format
        return {
            "format": "xandr",
            "deals": [
                {
                    "id": d.get("deal_id"),
                    "name": f"Deal {d.get('deal_id')}",
                    "type": {"1": "PG", "2": "PD", "3": "PA"}.get(
                        d.get("deal_type"), d.get("deal_type")
                    ),
                    "floor_price": d.get("actual_price_cpm")
                    or d.get("pricing", {}).get("final_cpm", 0),
                    "currency": "USD",
                    "active": d.get("status") == "confirmed",
                }
                for d in all_deals
            ],
        }
    else:
        # Generic format
        return {
            "format": "generic",
            "deals": all_deals,
            "count": len(all_deals),
        }


# =============================================================================
# IAB Deals API v1.0 — Deal Push & Status
# =============================================================================


async def push_deal_to_buyers(request: Any) -> dict[str, Any]:
    """Push a deal to one or more buyer endpoints via IAB Deals API v1.0."""
    from ..config import get_settings
    from ..storage.factory import get_storage
    from .deals_api import DealsAPIService

    settings = get_settings()
    service = DealsAPIService()

    # Try to load deal from storage first
    storage = await get_storage()
    stored_deal = await storage.get_deal(request.deal_id)

    # Build IAB Deal object
    deal_type = request.deal_type or (stored_deal or {}).get("deal_type", "PD")
    price = (
        request.price
        or (stored_deal or {}).get("actual_price_cpm")
        or (stored_deal or {}).get("pricing", {}).get("final_cpm", 0)
    )

    deal_obj = service.build_deal_object(
        deal_id=request.deal_id,
        deal_type=deal_type,
        price=price,
        name=request.name or (stored_deal or {}).get("name"),
        impressions=request.impressions or (stored_deal or {}).get("impressions"),
        flight_start=request.flight_start or (stored_deal or {}).get("flight_start"),
        flight_end=request.flight_end or (stored_deal or {}).get("flight_end"),
        buyer_seat_ids=request.buyer_seat_ids or (stored_deal or {}).get("buyer_seat_ids", []),
        seller_id=getattr(settings, "seller_organization_id", None),
        seller_domain=getattr(settings, "seller_domain", None),
    )

    # Build buyer configs
    buyer_configs = []
    for i, url in enumerate(request.buyer_urls):
        config = {"url": url}
        if request.buyer_api_keys and i < len(request.buyer_api_keys):
            config["api_key"] = request.buyer_api_keys[i]
        buyer_configs.append(config)

    # Push to all buyers
    results = await service.push_deal_to_multiple_buyers(deal_obj, buyer_configs)

    return {
        "deal_id": request.deal_id,
        "pushed_to": len(results),
        "succeeded": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "results": [r.model_dump() for r in results],
    }


async def get_deal_buyer_status(deal_id: str, buyer_url: str) -> dict[str, Any]:
    """Query a buyer for their acceptance status of a deal."""
    from .deals_api import DealsAPIService

    service = DealsAPIService()
    result = await service.query_deal_status(deal_id, buyer_url)
    return result.model_dump()


# =============================================================================
# SSP deal distribution
# =============================================================================


async def distribute_deal_via_ssp(request: Any) -> dict[str, Any]:
    """Distribute a deal through configured SSP(s)."""
    from ..clients.ssp_base import SSPDealCreateRequest, SSPDealType
    from ..clients.ssp_factory import build_ssp_registry

    registry = build_ssp_registry()

    if not registry.list_ssps():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "no_ssps_configured",
                "message": "No SSP connectors configured. Set SSP_CONNECTORS in environment.",
            },
        )

    # Get the right SSP client
    try:
        if request.ssp_name:
            ssp = registry.get_client(request.ssp_name)
        else:
            ssp = registry.get_client_for(
                inventory_type=request.inventory_type,
                deal_type=request.deal_type,
            )
    except (KeyError, RuntimeError) as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "ssp_routing_failed",
                "message": str(e),
                "available_ssps": registry.list_ssps(),
            },
        )

    # Map deal type
    deal_type_map = {
        "PMP": SSPDealType.PMP,
        "PG": SSPDealType.PG,
        "PREFERRED": SSPDealType.PREFERRED,
        "pmp": SSPDealType.PMP,
        "pg": SSPDealType.PG,
        "preferred": SSPDealType.PREFERRED,
    }

    create_request = SSPDealCreateRequest(
        deal_type=deal_type_map.get(request.deal_type or "PMP", SSPDealType.PMP),
        name=request.name,
        advertiser=request.advertiser,
        cpm=request.cpm,
        buyer_seat_ids=request.buyer_seat_ids or [],
        start_date=request.start_date,
        end_date=request.end_date,
        targeting=request.targeting,
    )

    async with ssp:
        result = await ssp.create_deal(create_request)

    return {
        "deal_id": result.deal_id,
        "ssp": result.ssp_name,
        "ssp_type": result.ssp_type.value,
        "status": result.status.value,
        "deal": result.model_dump(exclude={"raw"}),
    }


async def troubleshoot_deal_via_ssp(deal_id: str, ssp_name: str) -> dict[str, Any]:
    """Troubleshoot a deal via SSP diagnostics."""
    from ..clients.ssp_factory import build_ssp_registry

    registry = build_ssp_registry()

    try:
        ssp = registry.get_client(ssp_name)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_ssp",
                "message": f"SSP '{ssp_name}' not configured.",
                "available_ssps": registry.list_ssps(),
            },
        )

    async with ssp:
        result = await ssp.troubleshoot_deal(deal_id)

    return result.model_dump(exclude={"raw"})


# =============================================================================
# Curator support
# =============================================================================


def list_curators() -> dict[str, Any]:
    """List all registered curators."""
    from .curator_registry import build_curator_registry

    registry = build_curator_registry()
    curators = registry.list_active()

    return {
        "curators": [
            {
                "curator_id": c.curator_id,
                "name": c.name,
                "domain": c.domain,
                "type": c.curator_type.value,
                "description": c.description,
                "fee": c.fee.model_dump(),
                "supported_deal_types": c.supported_deal_types,
                "is_active": c.is_active,
            }
            for c in curators
        ],
        "count": len(curators),
    }


def get_curator(curator_id: str) -> dict[str, Any]:
    """Get details for a specific curator."""
    from .curator_registry import build_curator_registry

    registry = build_curator_registry()
    try:
        curator = registry.get(curator_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "curator_not_found",
                "message": f"Curator '{curator_id}' not registered.",
            },
        )

    return {
        "curator_id": curator.curator_id,
        "name": curator.name,
        "domain": curator.domain,
        "type": curator.curator_type.value,
        "description": curator.description,
        "fee": curator.fee.model_dump(),
        "audience_segments": curator.audience_segments,
        "content_categories": curator.content_categories,
        "supported_deal_types": curator.supported_deal_types,
        "is_active": curator.is_active,
        "tags": curator.tags,
    }


async def register_curator(request: Any) -> dict[str, Any]:
    """Register a new curator."""
    from ..models.curator import Curator, CuratorFee, CuratorFeeType, CuratorType
    from ..storage.factory import get_storage

    curator = Curator(
        curator_id=request.curator_id,
        name=request.name,
        domain=request.domain,
        curator_type=CuratorType(request.curator_type),
        description=request.description,
        fee=CuratorFee(
            fee_type=CuratorFeeType(request.fee_type),
            fee_value=request.fee_value,
        ),
        contact_email=request.contact_email,
        api_key=request.api_key,
        audience_segments=request.audience_segments,
        content_categories=request.content_categories,
        supported_deal_types=request.supported_deal_types,
    )

    # Persist to storage
    storage = await get_storage()
    await storage.set(f"curator:{curator.curator_id}", curator.model_dump())

    return {
        "curator_id": curator.curator_id,
        "name": curator.name,
        "status": "registered",
    }


async def create_curated_deal(request: Any, catalog: dict[str, Any]) -> dict[str, Any]:
    """Create a deal with curator overlay.

    The curator's fee is added on top of the publisher's base price and
    the curator appears as a node in the deal's schain. Product base price
    resolution reads the single cached catalog source (EP-3.3) instead of
    running ProductSetupFlow per request.
    """
    from ..config import get_settings
    from ..storage.factory import get_storage
    from .curator_registry import build_curator_registry

    # Get curator
    registry = build_curator_registry()
    try:
        curator = registry.get(request.curator_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "curator_not_found",
                "message": f"Curator '{request.curator_id}' not registered.",
            },
        )

    if not curator.is_active:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "curator_inactive",
                "message": f"Curator '{curator.name}' is not active.",
            },
        )

    # Get base price from product catalog. A known-but-unpriced product
    # (no base/floor CPM) is a 422 — never a fabricated price (ar-92f8).
    base_cpm = 12.0  # Default
    if request.product_id:
        product = catalog["products"].get(request.product_id)
        if product:
            from . import catalog_service

            base_cpm = catalog_service.priceable_cpm(product)

    # Calculate curated pricing
    curated_deal = registry.create_curated_deal(
        curator_id=request.curator_id,
        deal_id="pending",
        base_cpm=base_cpm,
        audience_segments=request.audience_segments,
        content_categories=request.content_categories,
        impressions=request.impressions or 0,
    )

    # Check buyer's max CPM against curated price
    if request.max_cpm is not None and request.max_cpm < curated_deal.total_cpm:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "below_curated_floor",
                "message": f"Buyer max CPM ${request.max_cpm:.2f} is below curated price ${curated_deal.total_cpm:.2f} (base ${base_cpm:.2f} + curator fee ${curated_deal.curator_fee_cpm:.2f}).",
                "base_cpm": base_cpm,
                "curator_fee_cpm": curated_deal.curator_fee_cpm,
                "total_cpm": curated_deal.total_cpm,
                "curator": curator.name,
            },
        )

    # Generate deal
    now = datetime.utcnow()
    deal_id = f"CUR-{uuid.uuid4().hex[:12].upper()}"
    flight_start = request.flight_start or now.strftime("%Y-%m-%d")
    flight_end = request.flight_end or (now + timedelta(days=30)).strftime("%Y-%m-%d")

    # Build schain with publisher + curator nodes
    settings = get_settings()
    seller_domain = getattr(settings, "seller_domain", "demo-publisher.example.com")
    seller_name = getattr(settings, "seller_organization_name", "Demo Publisher")

    schain = {
        "ver": "1.0",
        "complete": 1,
        "nodes": [
            # Publisher node
            {
                "asi": seller_domain,
                "sid": "default",
                "hp": 1,
                "name": seller_name,
                "domain": seller_domain,
            },
            # Curator node
            {
                "asi": curator.domain,
                "sid": curator.curator_id,
                "hp": 1,
                "name": curator.name,
                "domain": curator.domain,
            },
        ],
    }

    deal_data = {
        "deal_id": deal_id,
        "deal_type": request.deal_type,
        "status": "confirmed",
        "product_id": request.product_id,
        "base_cpm": base_cpm,
        "curator_fee_cpm": curated_deal.curator_fee_cpm,
        "total_cpm": curated_deal.total_cpm,
        "currency": "USD",
        "impressions": request.impressions,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "buyer_seat_ids": request.buyer_seat_ids,
        "curator": {
            "curator_id": curator.curator_id,
            "name": curator.name,
            "domain": curator.domain,
            "type": curator.curator_type.value,
            "fee": curator.fee.model_dump(),
        },
        "curator_targeting": {
            "audience_segments": request.audience_segments,
            "content_categories": request.content_categories,
        },
        "schain": schain,
        "created_at": now.isoformat() + "Z",
    }

    storage = await get_storage()
    await storage.set_deal(deal_id, deal_data)

    return {
        "deal_id": deal_id,
        "status": "confirmed",
        "deal_type": request.deal_type,
        "pricing": {
            "base_cpm": base_cpm,
            "curator_fee_cpm": curated_deal.curator_fee_cpm,
            "total_cpm": curated_deal.total_cpm,
            "currency": "USD",
        },
        "curator": {
            "curator_id": curator.curator_id,
            "name": curator.name,
            "type": curator.curator_type.value,
        },
        "schain": schain,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "created_at": deal_data["created_at"],
    }


# =============================================================================
# Deal migration & deprecation (Deal Library Phase 4)
# =============================================================================


async def migrate_deal(deal_id: str, request: Any) -> dict[str, Any]:
    """Migrate (replace) an existing deal with a new one, keeping lineage."""
    from ..storage.factory import get_storage

    storage = await get_storage()

    # Load old deal
    old_deal = await storage.get_deal(deal_id)
    if not old_deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    # Build new deal from old deal + overrides
    now = datetime.utcnow()
    new_deal_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"

    new_deal = {
        "deal_id": new_deal_id,
        "deal_type": request.deal_type or old_deal.get("deal_type", "PD"),
        "status": "confirmed",
        "product_id": request.product_id or old_deal.get("product_id"),
        "actual_price_cpm": request.max_cpm
        or old_deal.get("actual_price_cpm")
        or old_deal.get("pricing", {}).get("final_cpm"),
        "currency": old_deal.get("currency", "USD"),
        "impressions": request.impressions or old_deal.get("impressions"),
        "flight_start": request.flight_start
        or old_deal.get("flight_start")
        or now.strftime("%Y-%m-%d"),
        "flight_end": request.flight_end
        or old_deal.get("flight_end")
        or (now + timedelta(days=30)).strftime("%Y-%m-%d"),
        "buyer_seat_ids": request.buyer_seat_ids or old_deal.get("buyer_seat_ids", []),
        # Lineage
        "parent_deal_id": deal_id,
        "migration_reason": request.reason,
        # Carry forward schain and activation instructions
        "schain": old_deal.get("schain"),
        "activation_instructions": old_deal.get("activation_instructions"),
        "created_at": now.isoformat() + "Z",
    }

    await storage.set_deal(new_deal_id, new_deal)

    # Deprecate old deal
    old_deal["status"] = "deprecated"
    old_deal["deprecated_at"] = now.isoformat() + "Z"
    old_deal["deprecated_reason"] = request.reason or "Replaced by migration"
    old_deal["replacement_deal_id"] = new_deal_id
    await storage.set_deal(deal_id, old_deal)

    return {
        "new_deal_id": new_deal_id,
        "old_deal_id": deal_id,
        "status": "migrated",
        "lineage": {
            "parent_deal_id": deal_id,
            "replacement_deal_id": new_deal_id,
            "reason": request.reason,
        },
        "new_deal": new_deal,
    }


async def deprecate_deal(deal_id: str, request: Any) -> dict[str, Any]:
    """Deprecate a deal with reason and optional replacement lineage link."""
    from ..storage.factory import get_storage

    storage = await get_storage()

    deal = await storage.get_deal(deal_id)
    if not deal:
        raise HTTPException(
            status_code=404,
            detail={"error": "deal_not_found", "message": f"Deal '{deal_id}' not found."},
        )

    if deal.get("status") == "deprecated":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_deprecated",
                "message": f"Deal '{deal_id}' is already deprecated.",
                "deprecated_at": deal.get("deprecated_at"),
                "reason": deal.get("deprecated_reason"),
            },
        )

    now = datetime.utcnow().isoformat() + "Z"

    deal["status"] = "deprecated"
    deal["deprecated_at"] = now
    deal["deprecated_reason"] = request.reason
    if request.replacement_deal_id:
        deal["replacement_deal_id"] = request.replacement_deal_id

    await storage.set_deal(deal_id, deal)

    return {
        "deal_id": deal_id,
        "status": "deprecated",
        "deprecated_at": now,
        "reason": request.reason,
        "replacement_deal_id": request.replacement_deal_id,
    }


async def get_deal_lineage(deal_id: str) -> dict[str, Any]:
    """Get the lineage chain for a deal (parents + replacements)."""
    from ..storage.factory import get_storage

    storage = await get_storage()

    # Walk backwards (parents)
    parents = []
    visited = set()
    walk_id = deal_id
    while walk_id and walk_id not in visited:
        visited.add(walk_id)
        deal = await storage.get_deal(walk_id)
        if not deal:
            break
        parent_id = deal.get("parent_deal_id")
        if parent_id:
            parents.insert(
                0,
                {
                    "deal_id": parent_id,
                    "status": (await storage.get_deal(parent_id) or {}).get("status", "unknown"),
                },
            )
        walk_id = parent_id

    # Current deal
    current_deal = await storage.get_deal(deal_id)

    # Walk forwards (replacements)
    replacements = []
    visited = set()
    walk_id = (current_deal or {}).get("replacement_deal_id")
    while walk_id and walk_id not in visited:
        visited.add(walk_id)
        deal = await storage.get_deal(walk_id)
        if not deal:
            break
        replacements.append(
            {
                "deal_id": walk_id,
                "status": deal.get("status", "unknown"),
                "reason": deal.get("migration_reason"),
            }
        )
        walk_id = deal.get("replacement_deal_id")

    return {
        "deal_id": deal_id,
        "status": (current_deal or {}).get("status", "unknown"),
        "parents": parents,
        "replacements": replacements,
        "chain_length": len(parents) + 1 + len(replacements),
    }
