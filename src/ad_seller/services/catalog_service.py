# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Product catalog service — the ONE source for the seller's default catalog.

Historically the default product list lived in two places:

1. ``ProductSetupFlow.create_default_products()`` — ran per request from
   several endpoints, which is expensive (initialize_setup →
   ensure_seller_organization spins up an OpenDirect MCP session that hangs
   in ``session.initialize()``).
2. A hardcoded duplicate in ``interfaces/api/main.py``
   (``_get_static_product_catalog``) used by read endpoints.

This module reconciles both into a single cached catalog source (EP-3.3):

- ``DEFAULT_PRODUCT_CONFIGS`` is the canonical default product data.
  ``ProductSetupFlow.create_default_products()`` now consumes it too.
- ``get_static_product_catalog()`` is the cached accessor used by API
  endpoints (via the ``interfaces.api.main._get_static_product_catalog``
  compat delegator, which tests patch/reset).

CSV mode (``AD_SERVER_TYPE=csv``): the catalog is built from the CSV
inventory in ``CSV_DATA_DIR`` instead of the static defaults, so
``GET /products`` (and everything else reading the catalog — avails,
pricing, package resolution) reflects what the seller actually has.
Product IDs come verbatim from the CSV ``id`` column: stable across
calls within a process (single-cache design, issue #34) and
deterministic across restarts, and identical to the ``product_id``s the
sync flow stamps on SYNCED package placements.
"""

import asyncio
import logging
import uuid
from typing import Any, Optional

from ..models.core import DealType, PricingModel

logger = logging.getLogger(__name__)

# Canonical default product list. Keeping the data here (instead of in the
# flow) avoids importing CrewAI plus the OpenDirect client chain just to
# read the catalog.
#
# the defaults declare realistic capacity caps
# (``maximum_impressions``), audience/content targeting dicts, and ONE
# deliberately unpriced product (no ``base_cpm``/``floor_cpm``) so the
# avails capping, availableTargeting, and 422-unpriceable paths exercise
# on the wire — not only against synthetic test products.
DEFAULT_PRODUCT_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "Premium Display - Homepage",
        "description": "High-impact display on homepage",
        "inventory_type": "display",
        "base_cpm": 15.0,
        "floor_cpm": 10.0,
        "supported_deal_types": [
            DealType.PROGRAMMATIC_GUARANTEED,
            DealType.PREFERRED_DEAL,
        ],
        "supported_pricing_models": [PricingModel.CPM],
        # Homepage is a finite placement: ~5M monthly impressions.
        "maximum_impressions": 5_000_000,
        "content_targeting": {"section": ["homepage"]},
    },
    {
        "name": "Standard Display - ROS",
        "description": "Run of site display inventory",
        "inventory_type": "display",
        "base_cpm": 8.0,
        "floor_cpm": 5.0,
        "supported_deal_types": [DealType.PREFERRED_DEAL, DealType.PRIVATE_AUCTION],
        "supported_pricing_models": [PricingModel.CPM],
    },
    {
        "name": "Pre-Roll Video",
        "description": "In-stream pre-roll video ads",
        "inventory_type": "video",
        "base_cpm": 25.0,
        "floor_cpm": 18.0,
        "supported_deal_types": [
            DealType.PROGRAMMATIC_GUARANTEED,
            DealType.PREFERRED_DEAL,
        ],
        "supported_pricing_models": [PricingModel.CPM, PricingModel.CPCV],
    },
    {
        "name": "CTV Premium Streaming",
        "description": "Connected TV inventory on premium streaming apps",
        "inventory_type": "ctv",
        "base_cpm": 35.0,
        "floor_cpm": 28.0,
        "supported_deal_types": [DealType.PROGRAMMATIC_GUARANTEED],
        "supported_pricing_models": [PricingModel.CPM],
        # Premium streaming supply is capacity-constrained: ~20M monthly.
        "maximum_impressions": 20_000_000,
        "audience_targeting": {
            "demo": ["A18-49", "A25-54"],
            "geo": ["US"],
        },
        "content_targeting": {
            "genre": ["drama", "comedy", "sports", "news"],
            "content_rating": ["TV-PG", "TV-14"],
        },
    },
    {
        "name": "Mobile App Rewarded Video",
        "description": "User-initiated rewarded video in mobile apps",
        "inventory_type": "mobile_app",
        "base_cpm": 20.0,
        "floor_cpm": 15.0,
        "supported_deal_types": [DealType.PREFERRED_DEAL, DealType.PRIVATE_AUCTION],
        "supported_pricing_models": [PricingModel.CPM, PricingModel.CPCV],
    },
    {
        "name": "Native In-Feed",
        "description": "Native ads in content feeds",
        "inventory_type": "native",
        "base_cpm": 12.0,
        "floor_cpm": 8.0,
        "supported_deal_types": [DealType.PREFERRED_DEAL],
        "supported_pricing_models": [PricingModel.CPM, PricingModel.CPC],
    },
    # Linear TV — Direct seller (NBCU)
    {
        "name": "NBC Primetime :30",
        "description": "NBC broadcast primetime 30-second national spot",
        "inventory_type": "linear_tv",
        "base_cpm": 55.0,
        "floor_cpm": 40.0,
        "supported_deal_types": [DealType.PROGRAMMATIC_GUARANTEED],
        "supported_pricing_models": [PricingModel.CPM],
        # Primetime spot load is finite: ~12M impressions per flight.
        "maximum_impressions": 12_000_000,
    },
    {
        "name": "NBCU Cable Network :30 (Bravo/USA)",
        "description": "NBCU cable network 30-second spot across Bravo, USA, CNBC",
        "inventory_type": "linear_tv",
        "base_cpm": 22.0,
        "floor_cpm": 15.0,
        "supported_deal_types": [
            DealType.PROGRAMMATIC_GUARANTEED,
            DealType.PREFERRED_DEAL,
        ],
        "supported_pricing_models": [PricingModel.CPM],
    },
    {
        "name": "Telemundo Primetime :30",
        "description": "Telemundo Spanish-language primetime 30-second spot",
        "inventory_type": "linear_tv",
        "base_cpm": 18.0,
        "floor_cpm": 12.0,
        "supported_deal_types": [
            DealType.PROGRAMMATIC_GUARANTEED,
            DealType.PREFERRED_DEAL,
            DealType.PRIVATE_AUCTION,
        ],
        "supported_pricing_models": [PricingModel.CPM],
    },
    # Linear TV — MVPD operator (Comcast/Spectrum)
    {
        "name": "Comcast Local Avails — Top 10 DMAs",
        "description": "Comcast Xfinity local cable insertion avails in top 10 markets",
        "inventory_type": "linear_tv",
        "base_cpm": 15.0,
        "floor_cpm": 8.0,
        "supported_deal_types": [DealType.PREFERRED_DEAL, DealType.PRIVATE_AUCTION],
        "supported_pricing_models": [PricingModel.CPM],
    },
    {
        "name": "Comcast Addressable Linear — National",
        "description": "Comcast addressable linear TV with household-level targeting",
        "inventory_type": "linear_tv",
        "base_cpm": 55.0,
        "floor_cpm": 40.0,
        "supported_deal_types": [
            DealType.PROGRAMMATIC_GUARANTEED,
            DealType.PRIVATE_AUCTION,
        ],
        "supported_pricing_models": [PricingModel.CPM],
        "audience_targeting": {
            "geo": ["US"],
            "household_income": ["75k-100k", "100k+"],
            "presence_of_children": ["true", "false"],
        },
    },
    # Linear TV — Reseller/SSP (PubMatic/Magnite)
    {
        "name": "Programmatic Linear Reach — A25-54 Primetime",
        "description": "Aggregated primetime linear reach across multiple networks via SSP",
        "inventory_type": "linear_tv",
        "base_cpm": 30.0,
        "floor_cpm": 20.0,
        "supported_deal_types": [
            DealType.PROGRAMMATIC_GUARANTEED,
            DealType.PRIVATE_AUCTION,
        ],
        "supported_pricing_models": [PricingModel.CPM],
        "audience_targeting": {"demo": ["A25-54"], "daypart": ["primetime"]},
    },
    # Deliberately UNPRICED (no base_cpm/floor_cpm): pricing is on request,
    # so avails/quotes for it exercise the honest 422-unpriceable path on
    # the wire (never a fabricated price).
    {
        "name": "Digital Out-of-Home — Times Square Spectacular",
        "description": (
            "Iconic Times Square digital billboard takeover; "
            "pricing on request only"
        ),
        "inventory_type": "dooh",
        "supported_deal_types": [DealType.PREFERRED_DEAL],
        "supported_pricing_models": [PricingModel.CPM],
        "content_targeting": {"venue": ["times_square"], "format": ["billboard"]},
    },
]

# Module-level cache. Product IDs are generated once and cached so that
# repeated reads return stable product_ids.
_CATALOG_CACHE: Optional[dict[str, Any]] = None


def product_from_config(cfg: dict[str, Any], product_id: str) -> Any:
    """Build a ``ProductDefinition`` from one default-config entry.

    The ONE config→product mapping, shared by
    :func:`build_static_product_catalog` and
    ``ProductSetupFlow.create_default_products`` so enrichment fields
    (caps, targeting, deliberate unpricing) cannot silently
    diverge between the two consumers.
    """
    from ..models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id=product_id,
        name=cfg["name"],
        description=cfg.get("description"),
        inventory_type=cfg["inventory_type"],
        supported_deal_types=cfg["supported_deal_types"],
        supported_pricing_models=cfg["supported_pricing_models"],
        base_cpm=cfg.get("base_cpm"),
        floor_cpm=cfg.get("floor_cpm"),
        audience_targeting=cfg.get("audience_targeting"),
        content_targeting=cfg.get("content_targeting"),
        ad_product_targeting=cfg.get("ad_product_targeting"),
        minimum_impressions=cfg.get("minimum_impressions", 10000),
        maximum_impressions=cfg.get("maximum_impressions"),
    )


def classify_inventory_type(item: Any) -> str:
    """Classify an ad server inventory item into an inventory type string.

    Canonical name-based classification, shared by the catalog builder and
    ``ProductSetupFlow`` (which delegates here) so CSV-mode catalog
    products and sync-seeded products can never diverge.
    """
    name_lower = item.name.lower() if hasattr(item, "name") else ""
    if "ctv" in name_lower or "ott" in name_lower or "connected" in name_lower:
        return "ctv"
    if "video" in name_lower or "preroll" in name_lower or "midroll" in name_lower:
        return "video"
    if "native" in name_lower or "feed" in name_lower:
        return "native"
    if "app" in name_lower or "mobile" in name_lower:
        return "mobile_app"
    if (
        "linear" in name_lower
        or "broadcast" in name_lower
        or "tv " in name_lower
        or "cable" in name_lower
    ):
        return "linear_tv"
    return "display"


def infer_deal_types(inv_type: str) -> list[DealType]:
    """Infer supported deal types from inventory type (canonical mapping)."""
    return {
        "display": [DealType.PREFERRED_DEAL, DealType.PRIVATE_AUCTION],
        "video": [DealType.PROGRAMMATIC_GUARANTEED, DealType.PREFERRED_DEAL],
        "ctv": [DealType.PROGRAMMATIC_GUARANTEED],
        "mobile_app": [DealType.PREFERRED_DEAL, DealType.PRIVATE_AUCTION],
        "native": [DealType.PREFERRED_DEAL],
        "linear_tv": [DealType.PROGRAMMATIC_GUARANTEED, DealType.PREFERRED_DEAL],
    }.get(inv_type, [DealType.PREFERRED_DEAL])


def product_from_inventory_item(item: Any) -> Any:
    """Build a ``ProductDefinition`` from an ad server inventory item.

    The ONE item→product mapping, shared by :func:`build_csv_product_catalog`
    and ``ProductSetupFlow.sync_from_ad_server`` so the API catalog and the
    flow's seeded products carry identical ids, pricing, and taxonomy.

    ``product_id`` is the ad server item id verbatim (for CSV, the ``id``
    column of ``inventory.csv``) — deterministic across process restarts.
    """
    from ..models.flow_state import ProductDefinition

    raw = getattr(item, "raw", {}) or {}
    floor = raw.get("floor_price_cpm", 10.0)
    inv_type = classify_inventory_type(item)
    return ProductDefinition(
        product_id=item.id,
        name=item.name,
        description=raw.get("description", ""),
        inventory_type=inv_type,
        supported_deal_types=infer_deal_types(inv_type),
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=floor,
        floor_cpm=round(floor * 0.85, 2),
    )


def _run_blocking(coro: Any) -> Any:
    """Run a coroutine to completion from sync code.

    Works both outside any event loop (``asyncio.run``) and inside a
    running loop (endpoint threads) by delegating to a fresh loop in a
    worker thread. Only used for the one-time CSV catalog build.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _csv_mode_active() -> bool:
    """True when the configured ad server is the CSV adapter."""
    try:
        from ..config import get_settings

        return get_settings().ad_server_type == "csv"
    except Exception:  # pragma: no cover — settings unavailable in exotic envs
        logger.warning("Could not resolve settings; serving default catalog")
        return False


def build_csv_product_catalog() -> dict[str, Any]:
    """Build the product catalog from the CSV inventory (uncached).

    Reads ``CSV_DATA_DIR`` through the same ``CSVAdServerClient`` used by
    the sync flow (including connect-time schema validation), then maps
    each inventory item through :func:`product_from_inventory_item`.

    On any read/validation failure the default catalog is served instead
    (mirroring the sync flow's fallback-with-warning resilience) — the
    failure is logged loudly rather than taking down every catalog
    endpoint.
    """
    from ..clients.csv_adapter import CSVAdServerClient
    from ..config import get_settings

    data_dir = get_settings().csv_data_dir

    async def _load() -> list[Any]:
        client = CSVAdServerClient(data_dir=data_dir)
        async with client:
            return await client.list_inventory()

    try:
        items = _run_blocking(_load())
    except Exception as exc:
        logger.warning(
            "CSV catalog build failed for %s (%s); falling back to the "
            "default catalog — /products will NOT reflect CSV inventory",
            data_dir,
            exc,
        )
        return build_static_product_catalog()

    products: dict[str, Any] = {}
    for item in items:
        product_def = product_from_inventory_item(item)
        products[product_def.product_id] = product_def

    logger.info("Built CSV product catalog: %d products from %s", len(products), data_dir)
    return {
        "products": products,
        "inventory_types": sorted({p.inventory_type for p in products.values()}),
    }


def build_static_product_catalog() -> dict[str, Any]:
    """Build a fresh catalog dict from ``DEFAULT_PRODUCT_CONFIGS`` (uncached).

    Returns ``{"products": {product_id: ProductDefinition}, "inventory_types": [...]}``
    with newly generated product IDs.
    """
    products: dict[str, Any] = {}
    for cfg in DEFAULT_PRODUCT_CONFIGS:
        product_def = product_from_config(cfg, f"prod-{uuid.uuid4().hex[:8]}")
        products[product_def.product_id] = product_def

    inventory_types = sorted({p.inventory_type for p in products.values()})

    return {
        "products": products,
        "inventory_types": inventory_types,
    }


def get_static_product_catalog() -> dict[str, Any]:
    """Return the seller's product catalog without running the flow.

    In CSV mode (``AD_SERVER_TYPE=csv``) the catalog is built from the CSV
    inventory; in every other mode it is the static default catalog
    (byte-identical to the pre-CSV-wiring behavior).

    Cached — repeated reads return stable product_ids (issue #34).
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        if _csv_mode_active():
            _CATALOG_CACHE = build_csv_product_catalog()
        else:
            _CATALOG_CACHE = build_static_product_catalog()
    return _CATALOG_CACHE


def reset_catalog_cache() -> None:
    """Reset the cached catalog (rebuilt on next read).

    Default mode regenerates fresh uuid product IDs; CSV mode re-reads the
    CSV inventory, whose IDs are deterministic (the ``id`` column).
    """
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def priceable_cpm(product: Any) -> float:
    """The product's honest CPM: ``base_cpm`` falling back to ``floor_cpm``.

    Products declaring neither are unpriceable — HTTP 422 rather than a
    fabricated price (honest-availability policy; used by avails, quotes,
    pricing, and deal templating).
    """
    from fastapi import HTTPException

    cpm = product.base_cpm if product.base_cpm is not None else product.floor_cpm
    if cpm is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Product '{product.product_id}' has no base_cpm or floor_cpm; "
                "avails cannot be priced."
            ),
        )
    return cpm


def check_avails(
    product: Any,
    requested_impressions: Optional[int] = None,
    budget: Optional[float] = None,
) -> dict[str, Any]:
    """Compute OpenDirect avails for a product from catalog data only.

    Honest-availability policy (reference implementation — no fabricated
    numbers):

    - Requested impressions: ``requested_impressions`` if given; else derived
      from ``budget`` at the product's CPM (``int(budget / cpm * 1000)``);
      else the product's ``minimum_impressions``.
    - ``available_impressions``: if ``maximum_impressions`` is None the
      product declares no capacity cap, so the requested impressions are
      reported as available (floored at 0). Otherwise
      ``min(requested, maximum_impressions)``.
    - ``estimated_cpm``: ``base_cpm``, falling back to ``floor_cpm``. If both
      are None the product is unpriceable — HTTP 422 rather than a fabricated
      price.
    - ``total_cost``: ``available_impressions / 1000 * estimated_cpm``,
      rounded to 2 decimals.
    - ``guaranteed_impressions``: equals ``available_impressions`` when the
      product supports PROGRAMMATIC_GUARANTEED, else None.
    - ``delivery_confidence``: always None — the seller has no delivery
      forecast data source and does not invent one.
    - ``available_targeting``: sorted union of the keys of the product's
      non-None targeting dicts (audience/content/ad_product); None when the
      product declares no targeting dicts.

    Raises ``HTTPException(422)`` for unpriceable products (mirroring how
    ``quote_service`` expresses error semantics at the service layer).
    """
    cpm = priceable_cpm(product)

    if requested_impressions is not None:
        requested = requested_impressions
    elif budget is not None:
        requested = int(budget / cpm * 1000)
    else:
        requested = product.minimum_impressions

    requested = max(0, requested)
    if product.maximum_impressions is None:
        available = requested
    else:
        available = min(requested, product.maximum_impressions)

    guaranteed = available if DealType.PROGRAMMATIC_GUARANTEED in product.supported_deal_types else None

    targeting_dicts = [
        product.audience_targeting,
        product.content_targeting,
        product.ad_product_targeting,
    ]
    present = [d for d in targeting_dicts if d is not None]
    available_targeting: Optional[list[str]]
    if present:
        available_targeting = sorted({key for d in present for key in d})
    else:
        available_targeting = None

    return {
        "product_id": product.product_id,
        "available_impressions": available,
        "guaranteed_impressions": guaranteed,
        "estimated_cpm": cpm,
        "total_cost": round(available / 1000 * cpm, 2),
        "delivery_confidence": None,  # no forecast data source — never fabricated
        "available_targeting": available_targeting,
    }


async def override_inventory_type(
    product_id: str,
    inventory_type: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """Override the auto-detected inventory type for a product.

    Returns ``{"previous_type": ..., "applied_at": ...}``.
    """
    from datetime import datetime

    from ..storage.factory import get_storage

    storage = await get_storage()

    # Get current product data
    product_data = await storage.get(f"product:{product_id}")
    previous_type = None

    if product_data:
        previous_type = product_data.get("inventory_type")
        product_data["inventory_type"] = inventory_type
        product_data["inventory_type_override"] = True
        product_data["inventory_type_override_reason"] = reason
        await storage.set(f"product:{product_id}", product_data)
    else:
        # Create override record even if product not yet synced
        override_data = {
            "product_id": product_id,
            "inventory_type": inventory_type,
            "inventory_type_override": True,
            "inventory_type_override_reason": reason,
        }
        await storage.set(f"product:{product_id}", override_data)

    now = datetime.utcnow().isoformat() + "Z"

    # Store override in a separate key for persistence across syncs
    await storage.set(
        f"inventory_override:{product_id}",
        {
            "product_id": product_id,
            "inventory_type": inventory_type,
            "reason": reason,
            "applied_at": now,
        },
    )

    return {"previous_type": previous_type, "applied_at": now}


async def get_inventory_type_override(product_id: str) -> Optional[dict[str, Any]]:
    """Get the current inventory type override for a product, if any."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    return await storage.get(f"inventory_override:{product_id}")


async def delete_inventory_type_override(product_id: str) -> bool:
    """Remove an inventory type override. Returns False if none exists."""
    from ..storage.factory import get_storage

    storage = await get_storage()
    override = await storage.get(f"inventory_override:{product_id}")

    if not override:
        return False

    await storage.delete(f"inventory_override:{product_id}")

    # Remove override flag from product
    product_data = await storage.get(f"product:{product_id}")
    if product_data:
        product_data.pop("inventory_type_override", None)
        product_data.pop("inventory_type_override_reason", None)
        await storage.set(f"product:{product_id}", product_data)

    return True


def serialize_product(product: Any) -> dict[str, Any]:
    """Serialize a ProductDefinition to the public JSON shape."""
    return {
        "product_id": product.product_id,
        "name": product.name,
        "description": product.description,
        "inventory_type": product.inventory_type,
        "base_cpm": product.base_cpm,
        "floor_cpm": product.floor_cpm,
        "deal_types": [dt.value for dt in product.supported_deal_types],
    }
