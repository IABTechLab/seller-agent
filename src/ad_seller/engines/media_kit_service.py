# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Media Kit Service - Package CRUD, search, and tier-gated views.

Stateless service that manages inventory packages for buyer discovery.
Delegates pricing to PricingRulesEngine (no duplicated logic).
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from ..models.buyer_identity import AccessTier, BuyerContext
from ..models.flow_state import ProductDefinition
from ..models.media_kit import (
    AuthenticatedPackageView,
    Package,
    PackageLayer,
    PackagePlacement,
    PackageStatus,
    PublicPackageView,
)
from ..storage.base import StorageBackend
from .pricing_rules_engine import PricingRulesEngine

logger = logging.getLogger(__name__)


class MediaKitService:
    """Service for package management and tier-gated inventory discovery.

    Provides:
    - Tier-gated reads (public vs authenticated views)
    - Package CRUD (create, update, archive)
    - Layer 3 dynamic package assembly from product IDs
    - Keyword search across packages

    Example:
        service = MediaKitService(storage, pricing_engine)
        public_packages = await service.list_packages_public()
        auth_packages = await service.list_packages_authenticated(buyer_context)
    """

    def __init__(
        self,
        storage: StorageBackend,
        pricing_engine: PricingRulesEngine,
    ) -> None:
        self._storage = storage
        self._pricing = pricing_engine

    # =========================================================================
    # Tier-gated reads
    # =========================================================================

    async def list_packages_public(
        self,
        layer: Optional[PackageLayer] = None,
        featured_only: bool = False,
    ) -> list[PublicPackageView]:
        """List active packages as public views (price ranges, no placements)."""
        packages = await self._load_active_packages(layer=layer)
        if featured_only:
            packages = [p for p in packages if p.is_featured]
        return [self._to_public_view(p) for p in packages]

    async def list_packages_authenticated(
        self,
        buyer_context: BuyerContext,
        layer: Optional[PackageLayer] = None,
    ) -> list[AuthenticatedPackageView]:
        """List active packages as authenticated views (exact pricing)."""
        packages = await self._load_active_packages(layer=layer)
        return [self._to_authenticated_view(p, buyer_context) for p in packages]

    async def get_package_public(self, package_id: str) -> Optional[PublicPackageView]:
        """Get a single package as public view."""
        package = await self._load_package(package_id)
        if not package or package.status != PackageStatus.ACTIVE:
            return None
        return self._to_public_view(package)

    async def get_package_authenticated(
        self,
        package_id: str,
        buyer_context: BuyerContext,
    ) -> Optional[AuthenticatedPackageView]:
        """Get a single package as authenticated view."""
        package = await self._load_package(package_id)
        if not package or package.status != PackageStatus.ACTIVE:
            return None
        return self._to_authenticated_view(package, buyer_context)

    # =========================================================================
    # CRUD
    # =========================================================================

    async def create_package(self, package: Package) -> Package:
        """Create a new package and persist it."""
        await self._storage.set_package(
            package.package_id,
            package.model_dump(mode="json"),
        )
        logger.info("Package created: %s (%s)", package.package_id, package.name)
        return package

    async def update_package(
        self,
        package_id: str,
        updates: dict[str, Any],
    ) -> Optional[Package]:
        """Update an existing package. Returns None if not found."""
        package = await self._load_package(package_id)
        if not package:
            return None

        for key, value in updates.items():
            if hasattr(package, key):
                setattr(package, key, value)
        package.updated_at = datetime.utcnow()

        await self._storage.set_package(
            package.package_id,
            package.model_dump(mode="json"),
        )
        logger.info("Package updated: %s", package_id)
        return package

    async def delete_package(self, package_id: str) -> bool:
        """Archive a package (soft delete)."""
        package = await self._load_package(package_id)
        if not package:
            return False

        package.status = PackageStatus.ARCHIVED
        package.updated_at = datetime.utcnow()
        await self._storage.set_package(
            package.package_id,
            package.model_dump(mode="json"),
        )
        logger.info("Package archived: %s", package_id)
        return True

    # =========================================================================
    # Layer 3: dynamic assembly
    # =========================================================================

    async def assemble_dynamic_package(
        self,
        name: str,
        product_ids: list[str],
    ) -> Optional[Package]:
        """Assemble a dynamic package from product IDs.

        Fetches products, merges their inventory characteristics,
        computes blended pricing, and creates an ephemeral package.
        """
        products: list[ProductDefinition] = []
        for pid in product_ids:
            data = await self._storage.get_product(pid)
            if data:
                products.append(ProductDefinition(**data))

        if not products:
            return None

        # Build placements and merge characteristics
        placements = []
        all_ad_formats: set[str] = set()
        all_device_types: set[int] = set()
        all_cat: set[str] = set()
        all_tags: set[str] = set()
        total_base = 0.0
        min_floor = float("inf")

        for prod in products:
            placement = PackagePlacement(
                product_id=prod.product_id,
                product_name=prod.name,
                ad_formats=self._classify_ad_formats(prod.inventory_type),
                device_types=self._classify_device_types(prod.inventory_type),
                weight=1.0,
            )
            placements.append(placement)
            all_ad_formats.update(placement.ad_formats)
            all_device_types.update(placement.device_types)
            total_base += prod.base_cpm
            min_floor = min(min_floor, prod.floor_cpm)

            # Merge content targeting if present
            if prod.content_targeting and "cat" in prod.content_targeting:
                all_cat.update(prod.content_targeting["cat"])

        blended_price = round(total_base / len(products), 2)

        package = Package(
            package_id=f"pkg-{uuid.uuid4().hex[:8]}",
            name=name,
            description=f"Dynamic package: {', '.join(p.name for p in products)}",
            layer=PackageLayer.DYNAMIC,
            status=PackageStatus.ACTIVE,
            placements=placements,
            ad_formats=sorted(all_ad_formats),
            device_types=sorted(all_device_types),
            cat=sorted(all_cat),
            tags=sorted(all_tags),
            base_price=blended_price,
            floor_price=round(min_floor, 2) if min_floor != float("inf") else blended_price,
        )

        # Persist dynamic packages too (for reference)
        await self._storage.set_package(
            package.package_id,
            package.model_dump(mode="json"),
        )
        logger.info("Dynamic package assembled: %s from %d products", package.package_id, len(products))
        return package

    # =========================================================================
    # Search
    # =========================================================================

    async def search_packages(
        self,
        query: str,
        buyer_context: Optional[BuyerContext] = None,
    ) -> list[PublicPackageView | AuthenticatedPackageView]:
        """Search packages by keyword query.

        Tokenizes query and matches against name, description, tags,
        and content category IDs. Featured items get a score boost.
        """
        packages = await self._load_active_packages()
        if not packages:
            return []

        tokens = set(query.lower().split())
        scored: list[tuple[float, Package]] = []

        for pkg in packages:
            score = self._score_package(pkg, tokens)
            if score > 0:
                scored.append((score, pkg))

        scored.sort(key=lambda x: x[0], reverse=True)

        is_authenticated = buyer_context and buyer_context.effective_tier != AccessTier.PUBLIC
        results: list[PublicPackageView | AuthenticatedPackageView] = []
        for _score, pkg in scored:
            if is_authenticated:
                results.append(self._to_authenticated_view(pkg, buyer_context))
            else:
                results.append(self._to_public_view(pkg))

        return results

    # =========================================================================
    # Private helpers
    # =========================================================================

    async def _load_active_packages(
        self,
        layer: Optional[PackageLayer] = None,
    ) -> list[Package]:
        """Load all active packages from storage."""
        raw_list = await self._storage.list_packages()
        packages = []
        for data in raw_list:
            pkg = Package(**data)
            if pkg.status != PackageStatus.ACTIVE:
                continue
            if layer and pkg.layer != layer:
                continue
            packages.append(pkg)
        return packages

    async def _load_package(self, package_id: str) -> Optional[Package]:
        """Load a single package from storage."""
        data = await self._storage.get_package(package_id)
        if not data:
            return None
        return Package(**data)

    def _to_public_view(self, package: Package) -> PublicPackageView:
        """Convert a package to its public (unauthenticated) view."""
        price_display = self._pricing.get_price_display(package.base_price)
        if price_display["type"] == "range":
            price_range = price_display["display"]
        else:
            price_range = f"${price_display['price']:.0f} CPM"

        return PublicPackageView(
            package_id=package.package_id,
            name=package.name,
            description=package.description,
            ad_formats=package.ad_formats,
            device_types=package.device_types,
            cat=package.cat,
            cattax=package.cattax,
            geo_targets=package.geo_targets,
            tags=package.tags,
            price_range=price_range,
            rate_type=package.rate_type.value,
            is_featured=package.is_featured,
        )

    def _to_authenticated_view(
        self,
        package: Package,
        buyer_context: BuyerContext,
    ) -> AuthenticatedPackageView:
        """Convert a package to its authenticated view with exact pricing."""
        price_display = self._pricing.get_price_display(
            package.base_price,
            buyer_context=buyer_context,
        )
        tier_config = self._pricing.config.get_tier_config(buyer_context.effective_tier)

        if price_display["type"] == "exact":
            exact_price = price_display["price"]
        else:
            # Fallback: apply tier discount manually
            exact_price = round(
                package.base_price * (1 - tier_config.tier_discount), 2
            )

        if price_display["type"] == "range":
            price_range = price_display["display"]
        else:
            price_range = f"${exact_price:.0f} CPM"

        return AuthenticatedPackageView(
            package_id=package.package_id,
            name=package.name,
            description=package.description,
            ad_formats=package.ad_formats,
            device_types=package.device_types,
            cat=package.cat,
            cattax=package.cattax,
            geo_targets=package.geo_targets,
            tags=package.tags,
            price_range=price_range,
            rate_type=package.rate_type.value,
            is_featured=package.is_featured,
            exact_price=exact_price,
            floor_price=package.floor_price,
            currency=package.currency,
            placements=package.placements,
            audience_segment_ids=package.audience_segment_ids,
            negotiation_enabled=tier_config.negotiation_enabled,
            volume_discounts_available=tier_config.volume_discounts_enabled,
        )

    def _score_package(self, package: Package, tokens: set[str]) -> float:
        """Score a package against search tokens."""
        score = 0.0

        # Searchable text fields
        searchable = " ".join([
            package.name.lower(),
            (package.description or "").lower(),
            " ".join(t.lower() for t in package.tags),
            " ".join(c.lower() for c in package.cat),
            " ".join(package.ad_formats),
        ])

        for token in tokens:
            if token in searchable:
                score += 1.0

        # Featured boost
        if package.is_featured and score > 0:
            score *= 1.5

        return score

    @staticmethod
    def _classify_ad_formats(inventory_type: str) -> list[str]:
        """Derive OpenRTB ad formats from a product's inventory_type string."""
        mapping: dict[str, list[str]] = {
            "display": ["banner"],
            "video": ["video"],
            "ctv": ["video"],
            "mobile_app": ["banner", "video"],
            "native": ["native"],
            "audio": ["audio"],
        }
        return mapping.get(inventory_type, ["banner"])

    @staticmethod
    def _classify_device_types(inventory_type: str) -> list[int]:
        """Derive AdCOM DeviceType ints from a product's inventory_type string."""
        mapping: dict[str, list[int]] = {
            "display": [2, 4, 5],       # PC, Phone, Tablet
            "video": [2, 4, 5],         # PC, Phone, Tablet
            "ctv": [3, 7],              # CTV, STB
            "mobile_app": [4, 5],       # Phone, Tablet
            "native": [2, 4, 5],        # PC, Phone, Tablet
            "audio": [2, 4, 5, 6],      # PC, Phone, Tablet, Connected Device
        }
        return mapping.get(inventory_type, [2])
