# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quote lifecycle service (IAB Deals API v1.0).

Extracted from ``interfaces/api/main.py`` (EP-3.1): quote creation,
lazy expiry, and status handling. Behavior-preserving — error semantics
are expressed as ``HTTPException`` exactly as the endpoints raised them.

Imports of storage/flows/engines stay function-level so existing test
patch points (e.g. ``ad_seller.storage.factory.get_storage``) keep
working, mirroring the original endpoint bodies.
"""

import asyncio
import logging
import uuid
import weakref
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# How long a quote stays bookable.
QUOTE_TTL_SECONDS = 86400

# Deal type wire values accepted by the Deals API.
DEAL_TYPE_MAP_KEYS = ("PG", "PD", "PA")


def _deal_type_map():
    from ..models.core import DealType

    return {
        "PG": DealType.PROGRAMMATIC_GUARANTEED,
        "PD": DealType.PREFERRED_DEAL,
        "PA": DealType.PRIVATE_AUCTION,
    }


async def get_pricing(
    product_id: str,
    product: Any,
    buyer_context: Any,
    volume: int = 0,
) -> dict[str, Any]:
    """Calculate tier/volume-adjusted pricing for a product.

    Base price is the operator rate card's override when one is stored
    and matches the product's inventory type, else the catalog's
    base_cpm/floor_cpm (issue #69; see ``rate_card_service.resolve_base_cpm``).
    Unpriced products with no matching rate card entry are still a 422 —
    never a fabricated price.
    """
    from ..engines.pricing_rules_engine import PricingRulesEngine
    from ..models.pricing_tiers import TieredPricingConfig
    from . import rate_card_service

    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    decision = engine.calculate_price(
        product_id=product_id,
        base_price=await rate_card_service.resolve_base_cpm(product),
        buyer_context=buyer_context,
        volume=volume,
    )

    return {
        "product_id": product_id,
        "base_price": decision.base_price,
        "final_price": decision.final_price,
        "currency": decision.currency,
        "tier_discount": decision.tier_discount,
        "volume_discount": decision.volume_discount,
        "rationale": decision.rationale,
    }


def _inventory_hold_key(product_id: str) -> str:
    return f"inventory_hold:{product_id}"


# Every read-modify-write of a product's ledger runs under its lock so
# concurrent quotes/bookings cannot overwrite each other's entries or both
# claim the same remaining capacity. The lock is process-local: with several
# worker processes the race remains until the storage layer offers a
# compare-and-set (upstream PR #132 proposes ``storage.compare_and_set``; the
# first write of a ledger would also need create-if-absent).
_ledger_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _ledger_lock(product_id: str) -> asyncio.Lock:
    lock = _ledger_locks.get(product_id)
    if lock is None:
        lock = asyncio.Lock()
        _ledger_locks[product_id] = lock
    return lock


def _active_holds(ledger: Any, now: datetime) -> dict[str, dict[str, Any]]:
    """Unexpired entries of a product's hold ledger.

    A ``held`` entry (an outstanding PG quote) lapses with the quote's TTL;
    a ``booked`` entry (a PG quote that was booked) stays until its flight
    ends, so booked guaranteed volume keeps consuming capacity.
    """
    if not isinstance(ledger, dict):
        return {}
    active: dict[str, dict[str, Any]] = {}
    for quote_id, entry in ledger.items():
        if not isinstance(entry, dict):
            continue
        try:
            until = datetime.fromisoformat(str(entry.get("until", "")).rstrip("Z"))
        except ValueError:
            continue
        if until > now:
            active[quote_id] = entry
    return active


def _flight_day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _overlapping(
    ledger: dict[str, dict[str, Any]], flight_start: Any, flight_end: Any
) -> dict[str, dict[str, Any]]:
    """Ledger entries whose flight overlaps ``flight_start``..``flight_end``.

    ``maximum_impressions`` is read as a cap per overlapping flight: every
    hold or booking whose flight shares at least one day with the requested
    flight counts in full, whatever the overlap length. This is a
    simplification (no per-day or per-period pacing). Entries without a
    flight (written before flights were recorded), or a request without
    one, overlap everything.
    """
    start, end = _flight_day(flight_start), _flight_day(flight_end)
    if start is None or end is None:
        return dict(ledger)
    overlapping: dict[str, dict[str, Any]] = {}
    for entry_id, entry in ledger.items():
        entry_start = _flight_day(entry.get("flight_start"))
        entry_end = _flight_day(entry.get("flight_end"))
        if entry_start is None or entry_end is None or (entry_start <= end and entry_end >= start):
            overlapping[entry_id] = entry
    return overlapping


def _tracks_capacity(product: Any) -> bool:
    from ..config import get_settings

    return (
        product is not None
        and product.maximum_impressions is not None
        and get_settings().pg_quote_inventory_hold_enabled
    )


def _check_pg_capacity(
    product: Any,
    ledger: dict[str, dict[str, Any]],
    impressions: int,
    disclose_remaining: bool,
) -> None:
    """Raise 409 ``insufficient_avails`` when ``impressions`` don't fit.

    Outstanding PG holds and booked PG volume in ``ledger`` (already pruned
    to live entries overlapping the requested flight, excluding the entry
    being placed) count against the product's ``maximum_impressions``.
    The exact remaining volume reveals how much other buyers hold or
    booked, so it is only returned when ``disclose_remaining`` (an
    identified caller).
    """
    committed = sum(int(e.get("impressions") or 0) for e in ledger.values())
    remaining = max(0, product.maximum_impressions - committed)
    if impressions <= remaining:
        return
    if not disclose_remaining:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "insufficient_avails",
                "message": (
                    "Not enough guaranteed impressions are currently available for "
                    f"'{product.product_id}' to fill {impressions:,}."
                ),
            },
        )
    raise HTTPException(
        status_code=409,
        detail={
            "error": "insufficient_avails",
            "message": (
                f"Only {remaining:,} guaranteed impressions are currently available "
                f"for '{product.product_id}'; requested {impressions:,}."
            ),
            "available_impressions": remaining,
        },
    )


async def _reserve_pg_hold(
    storage: Any,
    product: Any,
    quote_id: str,
    impressions: int,
    now: datetime,
    hold_owner: Optional[str],
    flight_start: Any = None,
    flight_end: Any = None,
) -> None:
    """Check, and for a keyed buyer soft-hold, PG capacity for a quote.

    Refuses (409 ``insufficient_avails``) when outstanding PG quotes and
    booked PG volume already leave less than ``impressions`` of the
    product's ``maximum_impressions`` over an overlapping flight (see
    ``_overlapping``). Only a quote with a ``hold_owner``
    (an API-keyed buyer) places a hold: it lasts ``pg_hold_ttl_seconds``
    and one owner may hold at most ``pg_hold_max_buyer_share`` of the cap
    at once (409 ``pg_hold_limit_exceeded``). Anonymous quotes are checked
    but hold nothing. Products without a declared cap are not tracked.
    """
    from ..config import get_settings

    if not _tracks_capacity(product):
        return
    settings = get_settings()

    key = _inventory_hold_key(product.product_id)
    async with _ledger_lock(product.product_id):
        ledger = _active_holds(await storage.get(key), now)
        overlapping = _overlapping(ledger, flight_start, flight_end)
        _check_pg_capacity(
            product, overlapping, impressions, disclose_remaining=hold_owner is not None
        )
        if hold_owner is None:
            return
        held_by_owner = sum(
            int(e.get("impressions") or 0)
            for e in overlapping.values()
            if e.get("state") == "held" and e.get("owner") == hold_owner
        )
        if held_by_owner + impressions > settings.pg_hold_max_buyer_share * (
            product.maximum_impressions
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "pg_hold_limit_exceeded",
                    "message": (
                        "This buyer's outstanding PG quotes would hold more of "
                        f"'{product.product_id}' than one buyer may hold at once. Book "
                        "or let existing PG quotes lapse, or request fewer impressions."
                    ),
                },
            )
        hold_seconds = min(settings.pg_hold_ttl_seconds, QUOTE_TTL_SECONDS)
        ledger[quote_id] = {
            "impressions": impressions,
            "state": "held",
            "until": (now + timedelta(seconds=hold_seconds)).isoformat() + "Z",
            "owner": hold_owner,
            "flight_start": flight_start,
            "flight_end": flight_end,
        }
        await storage.set(key, ledger)


async def _commit_pg_entry(
    storage: Any,
    product_id: str,
    entry_id: str,
    impressions: int,
    flight_start: Any,
    flight_end: Any,
    get_product: Callable[[], Awaitable[Any]],
    disclose_remaining: bool,
) -> None:
    """Record ``entry_id`` as booked PG volume until its flight ends.

    A live hold is converted as is. Without one (anonymous quote, lapsed
    or lost hold, or a booking that never had a quote) the volume is
    re-checked against the remaining capacity first and refused with 409
    ``insufficient_avails`` if it no longer fits.
    """
    try:
        until = datetime.fromisoformat(str(flight_end)) + timedelta(days=1)
    except ValueError:
        until = datetime.utcnow() + timedelta(days=30)
    key = _inventory_hold_key(product_id)
    async with _ledger_lock(product_id):
        ledger = _active_holds(await storage.get(key), datetime.utcnow())
        entry = ledger.get(entry_id)
        if entry is None:
            product = await get_product()
            if not _tracks_capacity(product):
                return
            entry = {"impressions": impressions}
            _check_pg_capacity(
                product,
                _overlapping(ledger, flight_start, flight_end),
                impressions,
                disclose_remaining,
            )
        ledger[entry_id] = {
            **entry,
            "state": "booked",
            "until": until.isoformat() + "Z",
            "flight_start": flight_start,
            "flight_end": flight_end,
        }
        await storage.set(key, ledger)


async def commit_pg_hold(
    storage: Any,
    quote: dict[str, Any],
    catalog: Optional[dict[str, Any]] = None,
    disclose_remaining: bool = False,
) -> None:
    """Turn a booked PG quote's capacity into a booked commitment.

    Called by every quote-booking path before the booking is persisted:
    the entry no longer lapses but stays counted against capacity until
    the flight ends. A quote with no live hold is re-checked (see
    ``_commit_pg_entry``). ``catalog`` defaults to the static catalog;
    ``disclose_remaining`` only for an identified (API-keyed) caller.
    """
    from ..config import get_settings

    product_id = (quote.get("product") or {}).get("product_id")
    quote_id = quote.get("quote_id")
    if quote.get("deal_type") != "PG" or not product_id or not quote_id:
        return
    if not get_settings().pg_quote_inventory_hold_enabled:
        return
    terms = quote.get("terms") or {}

    async def _product() -> Any:
        source = catalog
        if source is None:
            from . import catalog_service

            source = await catalog_service.get_static_product_catalog()
        return (source.get("products") or {}).get(product_id)

    await _commit_pg_entry(
        storage,
        product_id,
        quote_id,
        int(terms.get("impressions") or 0),
        terms.get("flight_start"),
        terms.get("flight_end"),
        _product,
        disclose_remaining,
    )


async def commit_pg_booking(
    storage: Any,
    product: Any,
    booking_id: str,
    impressions: int,
    flight_start: Any,
    flight_end: Any,
    disclose_remaining: bool = False,
) -> None:
    """Reserve and commit PG volume booked without a quote (from-template)."""
    if not _tracks_capacity(product):
        return

    async def _product() -> Any:
        return product

    await _commit_pg_entry(
        storage,
        product.product_id,
        booking_id,
        impressions,
        flight_start,
        flight_end,
        _product,
        disclose_remaining,
    )


async def create_quote(
    request: Any,
    buyer_context: Any,
    catalog: dict[str, Any],
    hold_owner: Optional[str] = None,
) -> dict[str, Any]:
    """Create a non-binding price quote with a 24-hour TTL.

    Args:
        request: ``QuoteRequestModel`` payload.
        buyer_context: resolved ``BuyerContext`` (API key beats body params).
        catalog: static product catalog dict (``{"products": {...}, ...}``).
        hold_owner: identity of an API-keyed buyer. Only then does a PG
            quote hold capacity; anonymous PG quotes are only checked.
    """
    from ..engines.pricing_rules_engine import PricingRulesEngine
    from ..models.pricing_tiers import TieredPricingConfig
    from ..models.quotes import (
        QuoteAvailability,
        QuotePricing,
        QuoteProductInfo,
        QuoteResponse,
        QuoteStatus,
        QuoteTerms,
    )
    from ..storage.factory import get_storage

    deal_type_map = _deal_type_map()
    deal_type_str = request.deal_type.upper()
    if deal_type_str not in deal_type_map:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_deal_type",
                "message": f"Deal type must be one of: PG, PD, PA. Got: {request.deal_type}",
            },
        )

    # PG deals require impressions
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

    # Validate minimum impressions
    if request.impressions and request.impressions < product.minimum_impressions:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "below_minimum_impressions",
                "message": f"Minimum impressions for this product: {product.minimum_impressions}.",
            },
        )

    # Ground availability in declared catalog capacity — the same
    # calculation as POST /products/avails. Also enforces the
    # honest-pricing policy: products with neither base_cpm nor floor_cpm
    # are a 422 here too, never a fabricated price.
    from . import catalog_service

    avails = catalog_service.check_avails(product, requested_impressions=request.impressions)
    # Same requested-volume fallback as check_avails: explicit impressions,
    # else the product's minimum deal size.
    requested_volume = (
        request.impressions if request.impressions is not None else product.minimum_impressions
    )
    availability = QuoteAvailability(
        inventory_available=avails["available_impressions"] >= max(0, requested_volume),
    )

    # Calculate price via PricingRulesEngine. Base price is the operator
    # rate card's override when one is stored and matches this product's
    # inventory type (issue #69), else the catalog CPM avails already
    # validated exists (check_avails guarantees at least one CPM exists;
    # falls back to the floor when the product declares no base rate).
    from . import rate_card_service

    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    deal_type_enum = deal_type_map[deal_type_str]
    decision = engine.calculate_price(
        product_id=request.product_id,
        base_price=await rate_card_service.resolve_base_cpm(product),
        buyer_context=buyer_context,
        deal_type=deal_type_enum,
        volume=request.impressions or 0,
        inventory_type=product.inventory_type,
    )

    # Evaluate target_cpm if provided
    final_cpm = decision.final_price
    if request.target_cpm is not None:
        acceptable, _ = engine.is_price_acceptable(
            offered_price=request.target_cpm,
            product_floor=product.floor_cpm,
            buyer_context=buyer_context,
        )
        if acceptable:
            final_cpm = request.target_cpm

    # Build timestamps
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=QUOTE_TTL_SECONDS)

    # Default flight dates
    flight_start = request.flight_start or now.strftime("%Y-%m-%d")
    flight_end = request.flight_end or (now + timedelta(days=30)).strftime("%Y-%m-%d")

    # Generate quote
    quote_id = f"qt-{uuid.uuid4().hex[:12]}"
    is_guaranteed = deal_type_str == "PG"

    quote = QuoteResponse(
        quote_id=quote_id,
        status=QuoteStatus.AVAILABLE,
        product=QuoteProductInfo(
            product_id=product.product_id,
            name=product.name,
            inventory_type=product.inventory_type,
        ),
        pricing=QuotePricing(
            base_cpm=decision.base_price,
            tier_discount_pct=round(decision.tier_discount * 100, 1),
            volume_discount_pct=round(decision.volume_discount * 100, 1),
            final_cpm=final_cpm,
            currency=decision.currency,
            pricing_model=decision.pricing_model.value,
            rationale=decision.rationale,
        ),
        terms=QuoteTerms(
            impressions=request.impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            guaranteed=is_guaranteed,
        ),
        availability=availability,
        deal_type=deal_type_str,
        buyer_tier=buyer_context.effective_tier.value,
        expires_at=expires_at.isoformat() + "Z",
        created_at=now.isoformat() + "Z",
    )

    storage = await get_storage()

    # PG quotes promise guaranteed volume: a keyed buyer's quote holds it so
    # the same capacity cannot be quoted twice.
    if is_guaranteed:
        await _reserve_pg_hold(
            storage,
            product,
            quote_id,
            requested_volume,
            now=now,
            hold_owner=hold_owner,
            flight_start=flight_start,
            flight_end=flight_end,
        )

    # Persist with 24-hour TTL
    await storage.set_quote(quote_id, quote.model_dump(mode="json"), ttl=86400)

    # Record in quote history for pricing verification (Layer 4)
    from ..storage.quote_history import QuoteHistoryStore

    quote_history = QuoteHistoryStore(storage)
    await quote_history.record_quote(
        quote_id=quote_id,
        buyer_id=buyer_context.get_pricing_key(),
        product_id=request.product_id,
        quoted_cpm=final_cpm,
        expires_at=expires_at,
    )

    return quote.model_dump(mode="json")


async def get_quote(quote_id: str) -> dict[str, Any]:
    """Retrieve a previously issued quote. Raises 410 Gone if expired."""
    from ..models.quotes import QuoteStatus
    from ..storage.factory import get_storage

    storage = await get_storage()
    quote = await storage.get_quote(quote_id)

    if not quote:
        raise HTTPException(
            status_code=404,
            detail={"error": "quote_not_found", "message": f"Quote '{quote_id}' not found."},
        )

    # Lazy expiry check
    if quote.get("expires_at"):
        expires = datetime.fromisoformat(quote["expires_at"].rstrip("Z"))
        if datetime.utcnow() > expires:
            quote["status"] = QuoteStatus.EXPIRED.value
            await storage.set_quote(quote_id, quote, ttl=3600)  # Keep expired record briefly
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "quote_expired",
                    "message": "Quote has expired. Request a new quote.",
                },
            )

    return quote
