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
"""

import logging
import uuid
from typing import Any, Optional

from ..models.core import DealType, PricingModel

logger = logging.getLogger(__name__)

# Canonical default product list. Keeping the data here (instead of in the
# flow) avoids importing CrewAI plus the OpenDirect client chain just to
# read the catalog.
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
    },
]

# Module-level cache. Product IDs are generated once and cached so that
# repeated reads return stable product_ids.
_CATALOG_CACHE: Optional[dict[str, Any]] = None


def build_static_product_catalog() -> dict[str, Any]:
    """Build a fresh catalog dict from ``DEFAULT_PRODUCT_CONFIGS`` (uncached).

    Returns ``{"products": {product_id: ProductDefinition}, "inventory_types": [...]}``
    with newly generated product IDs.
    """
    from ..models.flow_state import ProductDefinition

    products: dict[str, Any] = {}
    for cfg in DEFAULT_PRODUCT_CONFIGS:
        product_def = ProductDefinition(
            product_id=f"prod-{uuid.uuid4().hex[:8]}",
            name=cfg["name"],
            description=cfg.get("description"),
            inventory_type=cfg["inventory_type"],
            supported_deal_types=cfg["supported_deal_types"],
            supported_pricing_models=cfg["supported_pricing_models"],
            base_cpm=cfg["base_cpm"],
            floor_cpm=cfg["floor_cpm"],
        )
        products[product_def.product_id] = product_def

    inventory_types = sorted({p.inventory_type for p in products.values()})

    return {
        "products": products,
        "inventory_types": inventory_types,
    }


def get_static_product_catalog() -> dict[str, Any]:
    """Return the seller's default product catalog without running the flow.

    Cached — repeated reads return stable product_ids.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        _CATALOG_CACHE = build_static_product_catalog()
    return _CATALOG_CACHE


def reset_catalog_cache() -> None:
    """Reset the cached catalog (fresh product IDs on next read)."""
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


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
