# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Operator rate card resolution (issue #69).

``PUT /api/v1/rate-card`` and the MCP ``update_rate_card`` tool wrote a
rate card that nothing in pricing ever read: quotes, from-template
bookings, and negotiation anchors all priced off the catalog product's
``base_cpm``/``floor_cpm`` directly, and the stored card sat inert in
storage. This module is the ONE place that consults the stored rate
card before falling back to catalog pricing, so quoting
(``quote_service``), from-template booking
(``deal_service.create_deal_from_template``), and negotiation anchoring
(``negotiation_service.counter_proposal`` and
``ProposalHandlingFlow.evaluate_pricing``) all inherit a rate card
change identically instead of duplicating the lookup per call site.

Match key: ``RateCardEntry.inventory_type`` — the only match key the
rate-card model declares (``interfaces/api/schemas.RateCardEntry`` has
no ``product_id`` or tier field, only ``inventory_type``/``base_cpm``/
``currency``/``effective_date``/``notes``). An entry whose
``inventory_type`` matches the product's overrides the catalog base CPM
as the starting price. The product's ``floor_cpm`` still applies — an
override below floor is clamped up to the floor, never priced under
it. When the product has no ``floor_cpm`` of its own, the clamp falls
back to the global floor (``TieredPricingConfig.global_floor_cpm``) —
an override is never applied unclamped just because the product didn't
configure a floor. No matching entry, or no stored rate card at all,
falls back to catalog pricing — byte-identical to pre-issue-#69
behavior.

Deliberately unpriced products (no ``base_cpm``/``floor_cpm`` at all —
the honest-availability policy's 422-unpriceable path, see
``catalog_service.priceable_cpm``) are NOT put on sale by a rate card
entry: the rate card overrides an existing catalog price, it does not
manufacture one for a product the catalog declares has no price.
``resolve_base_cpm`` still 422s for those exactly as
``catalog_service.priceable_cpm`` always has.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

RATE_CARD_STORAGE_KEY = "rate_card:current"


async def get_rate_card_entries() -> Optional[list[dict[str, Any]]]:
    """Stored rate card entries, or ``None`` if no rate card has ever been set."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    rate_card = await storage.get(RATE_CARD_STORAGE_KEY)
    if not rate_card:
        return None
    return rate_card.get("entries") or []


def _find_entry(
    entries: Optional[list[dict[str, Any]]], inventory_type: Optional[str]
) -> Optional[dict[str, Any]]:
    """The rate card entry matching ``inventory_type``, if any."""
    if not entries or not inventory_type:
        return None
    for entry in entries:
        if entry.get("inventory_type") == inventory_type and entry.get("base_cpm") is not None:
            return entry
    return None


def _effective_floor_cpm(floor_cpm: Optional[float]) -> float:
    """The floor to clamp a rate card override against.

    ``ProductDefinition.floor_cpm`` is optional — plenty of products carry
    a ``base_cpm`` with no floor configured at all — so an override on one
    of those must not skip the clamp entirely just because there is no
    product-specific floor to clamp to. Falls back to
    ``TieredPricingConfig.global_floor_cpm``, the same backstop
    ``PricingRulesEngine`` already enforces on quotes and bookings, rather
    than inventing a separate constant here.
    """
    if floor_cpm is not None:
        return floor_cpm

    from ..models.pricing_tiers import TieredPricingConfig

    return TieredPricingConfig.model_fields["global_floor_cpm"].default


def _apply_override(
    *,
    catalog_cpm: float,
    floor_cpm: Optional[float],
    entry: dict[str, Any],
    product_id: Optional[str],
    inventory_type: Optional[str],
) -> float:
    """Clamp a matched rate card entry to the floor and log the provenance."""
    override_cpm = entry["base_cpm"]
    effective_floor = _effective_floor_cpm(floor_cpm)
    if override_cpm < effective_floor:
        logger.info(
            "Rate card override for product %s (%s): $%.2f is below floor $%.2f; clamped to floor.",
            product_id,
            inventory_type,
            override_cpm,
            effective_floor,
        )
        return effective_floor

    logger.info(
        "Rate card override applied for product %s (%s): catalog base $%.2f -> $%.2f.",
        product_id,
        inventory_type,
        catalog_cpm,
        override_cpm,
    )
    return override_cpm


async def resolve_base_cpm(product: Any) -> float:
    """Starting CPM for a ``ProductDefinition``: rate card override, else catalog.

    Consulted by quoting (``quote_service.get_pricing`` /
    ``quote_service.create_quote``) and from-template booking
    (``deal_service.create_deal_from_template``). Raises the same
    ``HTTPException(422)`` as ``catalog_service.priceable_cpm`` for a
    product with neither ``base_cpm`` nor ``floor_cpm`` — a rate card
    entry never manufactures a price the catalog says doesn't exist.
    """
    from . import catalog_service

    catalog_cpm = catalog_service.priceable_cpm(product)
    entries = await get_rate_card_entries()
    entry = _find_entry(entries, getattr(product, "inventory_type", None))
    if entry is None:
        return catalog_cpm

    return _apply_override(
        catalog_cpm=catalog_cpm,
        floor_cpm=product.floor_cpm,
        entry=entry,
        product_id=product.product_id,
        inventory_type=product.inventory_type,
    )


async def resolve_negotiation_anchor(product_data: dict[str, Any]) -> tuple[float, float]:
    """``(base_price, floor_price)`` anchor for negotiation, from a
    serialized product dict (``catalog_service.serialize_product`` shape,
    or the ``storage.get_product`` record negotiation already reads).

    Mirrors ``resolve_base_cpm``'s override lookup, but keeps
    ``counter_proposal``'s existing default-to-0 semantics for missing
    catalog pricing rather than 422ing — negotiation never 422'd on an
    unpriced product before this change either.
    """
    floor_cpm = product_data.get("floor_cpm") or 0
    catalog_cpm = product_data.get("base_cpm")
    if catalog_cpm is None:
        catalog_cpm = floor_cpm

    entries = await get_rate_card_entries()
    inventory_type = product_data.get("inventory_type")
    entry = _find_entry(entries, inventory_type)
    if entry is None:
        return catalog_cpm, floor_cpm

    base_price = _apply_override(
        catalog_cpm=catalog_cpm,
        floor_cpm=product_data.get("floor_cpm"),
        entry=entry,
        product_id=product_data.get("product_id"),
        inventory_type=inventory_type,
    )
    return base_price, floor_cpm
