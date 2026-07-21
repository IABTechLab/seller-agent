# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Catalog Avails Tool — grounded availability for the availability agent.

Wraps :func:`ad_seller.services.catalog_service.check_avails`, the same
calculation behind ``POST /products/avails`` and the quote path's
``QuoteAvailability`` (ar-f0ky). Every number in the output is derived
from the product catalog's declared data (capacity caps, CPMs, targeting
dicts); anything without a data source (fill rates, demand levels,
delivery confidence) is reported as unavailable rather than invented.
"""

from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...models.core import DealType


class CatalogAvailsInput(BaseModel):
    """Input schema for the catalog avails tool."""

    product_id: Optional[str] = Field(
        default=None,
        description=(
            "Product ID to check avails for. Omit to get a declared-capacity "
            "summary of the whole catalog."
        ),
    )
    requested_impressions: Optional[int] = Field(
        default=None,
        description="Requested impression volume for the avails check.",
    )
    budget: Optional[float] = Field(
        default=None,
        description="Budget to derive impressions from (at the product CPM).",
    )
    inventory_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter the catalog summary to one inventory type "
            "(e.g. display, video, ctv, mobile_app, native, linear_tv)."
        ),
    )


class CatalogAvailsTool(BaseTool):
    """Check product availability from declared catalog data only.

    Same calculation as the seller's avails endpoint: requested volume is
    capped at the product's declared ``maximum_impressions``; pricing uses
    ``base_cpm`` (falling back to ``floor_cpm``); products with neither are
    reported as unpriceable. No forecasts, fill rates, or demand levels are
    fabricated — the reference implementation has no data source for them.
    """

    name: str = "catalog_avails"
    description: str = """Check inventory availability for a product from the
    seller's catalog data (declared capacity caps, CPMs, targeting).
    Provide product_id (plus optional requested_impressions or budget) for a
    product avails check, or omit product_id for a catalog capacity summary.
    Only reports numbers derived from catalog data — fill rates, demand
    levels, and delivery confidence have no data source and are never
    invented."""
    args_schema: Type[BaseModel] = CatalogAvailsInput

    def _run(
        self,
        product_id: Optional[str] = None,
        requested_impressions: Optional[int] = None,
        budget: Optional[float] = None,
        inventory_type: Optional[str] = None,
    ) -> str:
        from ...services import catalog_service

        catalog = catalog_service.get_static_product_catalog()
        products = catalog["products"]

        if product_id is None:
            return self._catalog_summary(products, inventory_type)

        product = products.get(product_id)
        if product is None:
            known = ", ".join(sorted(products)) or "(catalog is empty)"
            return f"Product '{product_id}' not found in the catalog. Known product IDs: {known}"

        return self._product_avails(catalog_service, product, requested_impressions, budget)

    # -- formatting -------------------------------------------------------

    def _product_avails(
        self,
        catalog_service,
        product,
        requested_impressions: Optional[int],
        budget: Optional[float],
    ) -> str:
        from fastapi import HTTPException

        try:
            avails = catalog_service.check_avails(
                product,
                requested_impressions=requested_impressions,
                budget=budget,
            )
        except HTTPException as exc:
            # Unpriceable product (no base_cpm/floor_cpm) — report honestly.
            return f"Avails for '{product.product_id}': {exc.detail}"

        cap = (
            f"{product.maximum_impressions:,}"
            if product.maximum_impressions is not None
            else "none declared"
        )
        guaranteed = (
            f"{avails['guaranteed_impressions']:,}"
            if avails["guaranteed_impressions"] is not None
            else "n/a (product does not support programmatic guaranteed)"
        )
        targeting = (
            ", ".join(avails["available_targeting"])
            if avails["available_targeting"]
            else "none declared"
        )

        return "\n".join(
            [
                f"Catalog avails — {product.name} ({product.product_id})",
                f"  Inventory type: {product.inventory_type}",
                f"  Available impressions: {avails['available_impressions']:,}",
                f"  Guaranteed impressions: {guaranteed}",
                f"  Declared capacity cap: {cap}",
                f"  Minimum impressions: {product.minimum_impressions:,}",
                f"  Estimated CPM: {avails['estimated_cpm']} {product.currency}",
                f"  Total cost at available volume: {avails['total_cost']} {product.currency}",
                f"  Available targeting: {targeting}",
                "  Delivery confidence: no data source — not forecast",
                "  Fill rate / competing demand: no data source — not forecast",
            ]
        )

    def _catalog_summary(self, products: dict, inventory_type: Optional[str]) -> str:
        rows = [
            p
            for p in products.values()
            if inventory_type is None or p.inventory_type == inventory_type
        ]
        if not rows:
            scope = f" for inventory type '{inventory_type}'" if inventory_type else ""
            return f"No products in the catalog{scope}."

        lines = ["Catalog capacity summary (declared data only):"]
        for p in sorted(rows, key=lambda p: p.name):
            cpm = (
                f"base {p.base_cpm}"
                if p.base_cpm is not None
                else f"floor {p.floor_cpm}"
                if p.floor_cpm is not None
                else "unpriced — pricing on request"
            )
            cap = (
                f"{p.maximum_impressions:,}"
                if p.maximum_impressions is not None
                else "none declared"
            )
            pg = "yes" if DealType.PROGRAMMATIC_GUARANTEED in p.supported_deal_types else "no"
            lines.append(
                f"  - {p.name} ({p.product_id}) [{p.inventory_type}]: "
                f"CPM {cpm}; capacity cap {cap}; "
                f"min {p.minimum_impressions:,}; guaranteed-capable: {pg}"
            )
        lines.append(
            "Fill rates, demand levels, and delivery forecasts have no data "
            "source and are not reported."
        )
        return "\n".join(lines)
