# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Product Setup Flow - Define and configure sellable inventory products.

This flow handles:
- Syncing inventory from ad server (GAM/FreeWheel)
- Defining products backed by inventory segments
- Attaching IAB taxonomies (Audience, Content, Ad Product)
- Setting commercial terms (deal types, pricing models)
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from crewai.flow.flow import Flow, listen, start

from ..config import get_settings
from ..models.core import DealType
from ..models.flow_state import (
    ExecutionStatus,
    ProductDefinition,
    SellerFlowState,
)
from ..models.media_kit import (
    Package,
    PackageLayer,
    PackagePlacement,
    PackageStatus,
)

logger = logging.getLogger(__name__)


class ProductSetupState(SellerFlowState):
    """State for product setup flow."""

    # Ad server sync state
    ad_server_config_id: Optional[str] = None
    synced_segments: list[str] = []

    # Product creation state
    products_to_create: list[dict[str, Any]] = []
    created_products: list[str] = []


class ProductSetupFlow(Flow[ProductSetupState]):
    """Flow for setting up products in the seller catalog.

    Steps:
    1. Initialize seller organization
    2. Sync inventory from ad server (optional)
    3. Create inventory segments
    4. Define products with taxonomy targeting
    5. Set commercial terms
    """

    def __init__(self) -> None:
        """Initialize the product setup flow."""
        super().__init__()
        self._settings = get_settings()

    @start()
    async def initialize_setup(self) -> None:
        """Initialize the product setup flow."""
        self.state.flow_id = str(uuid.uuid4())
        self.state.flow_type = "product_setup"
        self.state.started_at = datetime.utcnow()
        self.state.status = ExecutionStatus.PRODUCT_SETUP

        # Set seller identity from settings
        self.state.seller_organization_id = (
            self._settings.seller_organization_id or f"seller-{uuid.uuid4().hex[:8]}"
        )
        self.state.seller_name = self._settings.seller_organization_name

    @staticmethod
    def _synced_package_id(source: str, key: str) -> str:
        """Deterministic SYNCED-layer package id.

        Keyed on layer ("synced") + ad server source + inventory type (or
        mock-package slug) so repeated syncs upsert the same records
        instead of minting fresh ``pkg-{uuid8}`` ids on every run
        (issue #34: non-idempotent sync duplicated the whole layer).
        """

        def _slug(value: str) -> str:
            return value.lower().replace("_", "-").replace(" ", "-")

        return f"pkg-synced-{_slug(source)}-{_slug(key)}"

    async def _upsert_synced_package(self, storage: Any, package: Package) -> None:
        """Store a synced package, preserving created_at on re-seed.

        With deterministic ids a re-sync overwrites the same record; keeping
        the original created_at makes unchanged re-seeded packages stable
        (identity + timestamps) across syncs.
        """
        existing = await storage.get_package(package.package_id)
        if existing and existing.get("created_at"):
            package.created_at = datetime.fromisoformat(existing["created_at"])
        await storage.set_package(package.package_id, package.model_dump(mode="json"))
        self.state.synced_segments.append(package.package_id)

    async def _prune_stale_synced_packages(self, storage: Any) -> None:
        """Delete SYNCED-layer packages not re-seeded by this sync run.

        Only the synced layer converges to the ad server's current
        inventory; curated/operator/dynamic packages are never touched.
        """
        keep = set(self.state.synced_segments)
        for pkg in await storage.list_packages():
            pkg_id = pkg.get("package_id")
            if pkg.get("layer") == PackageLayer.SYNCED.value and pkg_id and pkg_id not in keep:
                await storage.delete_package(pkg_id)
                logger.info("Pruned stale synced package: %s", pkg_id)

    @listen(initialize_setup)
    async def sync_from_ad_server(self) -> None:
        """Sync inventory from ad server if configured.

        When an ad server is configured, imports inventory via
        AdServerClient.list_inventory() and creates Layer 1 synced packages.
        Otherwise, creates mock synced packages for development.

        Idempotent (issue #34): synced packages use deterministic ids with
        upsert semantics, and SYNCED-layer packages that are not re-seeded
        by the current run are pruned, so repeated syncs converge to the
        same package set instead of duplicating the layer.
        """
        if (
            not self._settings.gam_network_code
            and not self._settings.freewheel_sh_mcp_url
            and self._settings.ad_server_type not in ("csv", "s3")
        ):
            self.state.warnings.append("No ad server configured, creating mock synced packages")
            await self._create_mock_synced_packages()
            await self._finish_sync()
            return

        try:
            from ..clients.ad_server_base import get_ad_server_client
            from ..storage.factory import get_storage

            storage = await get_storage()
            client = get_ad_server_client()
            async with client:
                items = await client.list_inventory()

            # Group items by inferred type and create packages
            grouped: dict[str, list] = {}
            for item in items:
                inv_type = self._classify_inventory_type(item)
                grouped.setdefault(inv_type, []).append(item)

            # Also create ProductDefinition entries from CSV items
            # so the /products REST API endpoint returns real data.
            # Canonical item→product mapping lives in catalog_service
            # (shared with the CSV-mode catalog build) so flow-seeded
            # products and API catalog products cannot diverge.
            from ..services.catalog_service import product_from_inventory_item

            for item in items:
                product_def = product_from_inventory_item(item)
                self.state.products[product_def.product_id] = product_def

            logger.info("Created %d products from ad server inventory", len(self.state.products))

            for inv_type, inv_items in grouped.items():
                ad_formats = self._classify_ad_formats_from_type(inv_type)
                device_types = self._classify_device_types_from_type(inv_type)
                base_cpm = self._estimate_base_cpm(inv_type)

                package = Package(
                    package_id=self._synced_package_id(client.ad_server_type.value, inv_type),
                    name=f"{inv_type.replace('_', ' ').title()} - Synced",
                    description=f"Synced {inv_type} inventory ({len(inv_items)} ad units)",
                    layer=PackageLayer.SYNCED,
                    status=PackageStatus.ACTIVE,
                    placements=[
                        PackagePlacement(
                            product_id=item.id,
                            product_name=item.name,
                            ad_formats=ad_formats,
                            device_types=device_types,
                        )
                        for item in inv_items
                    ],
                    ad_formats=ad_formats,
                    device_types=device_types,
                    cat=["IAB1"],
                    cattax=2,
                    base_price=base_cpm,
                    floor_price=round(base_cpm * 0.7, 2),
                    ad_server_source=client.ad_server_type.value,
                    is_featured=inv_type == "ctv",
                )

                await self._upsert_synced_package(storage, package)

            logger.info("Synced %d packages from ad server", len(grouped))

        except Exception as e:
            self.state.warnings.append(f"Ad server sync failed, using mocks: {e}")
            await self._create_mock_synced_packages()

        await self._finish_sync()

    async def _finish_sync(self) -> None:
        """Terminal step of every sync path: prune the stale synced layer."""
        from ..storage.factory import get_storage

        storage = await get_storage()
        await self._prune_stale_synced_packages(storage)

    async def _create_mock_synced_packages(self) -> None:
        """Create mock Layer 1 packages for development without ad server creds."""
        from ..storage.factory import get_storage

        storage = await get_storage()

        mock_packages = [
            Package(
                package_id=self._synced_package_id("mock", "display-network"),
                name="Display Network Bundle",
                description="Standard and high-impact display across web and mobile web",
                layer=PackageLayer.SYNCED,
                status=PackageStatus.ACTIVE,
                placements=[
                    PackagePlacement(
                        product_id="prod-display-hp",
                        product_name="Premium Display - Homepage",
                        ad_formats=["banner"],
                        device_types=[2, 4, 5],
                    ),
                    PackagePlacement(
                        product_id="prod-display-ros",
                        product_name="Standard Display - ROS",
                        ad_formats=["banner"],
                        device_types=[2, 4, 5],
                    ),
                ],
                ad_formats=["banner"],
                device_types=[2, 4, 5],  # PC, Phone, Tablet
                cat=["IAB1", "IAB3", "IAB19"],  # Arts, Business, Sports
                cattax=2,
                audience_segment_ids=["3", "4", "5", "6", "7"],  # Age 18-34 (AT 1.1)
                geo_targets=["US"],
                base_price=12.0,
                floor_price=8.0,
                tags=["display", "standard", "high-impact"],
                is_featured=False,
            ),
            Package(
                package_id=self._synced_package_id("mock", "video-suite"),
                name="Video Suite",
                description="Pre-roll and mid-roll video across desktop and mobile",
                layer=PackageLayer.SYNCED,
                status=PackageStatus.ACTIVE,
                placements=[
                    PackagePlacement(
                        product_id="prod-video-preroll",
                        product_name="Pre-Roll Video",
                        ad_formats=["video"],
                        device_types=[2, 4, 5],
                    ),
                ],
                ad_formats=["video"],
                device_types=[2, 4, 5],  # PC, Phone, Tablet
                cat=["IAB1"],  # Arts & Entertainment
                cattax=2,
                audience_segment_ids=["3", "4", "5", "6", "49", "50"],  # Age 18-34, Gender
                geo_targets=["US"],
                base_price=25.0,
                floor_price=18.0,
                tags=["video", "pre-roll", "in-stream"],
                is_featured=False,
            ),
            Package(
                package_id=self._synced_package_id("mock", "ctv-premium"),
                name="CTV Premium Bundle",
                description="Connected TV inventory on premium streaming apps",
                layer=PackageLayer.SYNCED,
                status=PackageStatus.ACTIVE,
                placements=[
                    PackagePlacement(
                        product_id="prod-ctv-premium",
                        product_name="CTV Premium Streaming",
                        ad_formats=["video"],
                        device_types=[3, 7],
                    ),
                ],
                ad_formats=["video"],
                device_types=[3, 7],  # CTV, Set Top Box
                cat=["IAB1", "IAB19"],  # Arts & Entertainment, Sports
                cattax=2,
                audience_segment_ids=["3", "4", "5", "6", "7", "8"],  # Age 18-44
                geo_targets=["US"],
                base_price=35.0,
                floor_price=28.0,
                tags=["ctv", "premium", "streaming", "living room"],
                is_featured=True,
            ),
            Package(
                package_id=self._synced_package_id("mock", "linear-tv-nbcu"),
                name="NBCU Linear TV Broadcast Bundle",
                description="Linear TV inventory across NBC broadcast and NBCU cable networks",
                layer=PackageLayer.SYNCED,
                status=PackageStatus.ACTIVE,
                placements=[
                    PackagePlacement(
                        product_id="prod-ltv-nbc-prime",
                        product_name="NBC Primetime :30",
                        ad_formats=["video"],
                        device_types=[3, 7],
                    ),
                    PackagePlacement(
                        product_id="prod-ltv-nbc-late",
                        product_name="NBC Late Night :30",
                        ad_formats=["video"],
                        device_types=[3, 7],
                    ),
                    PackagePlacement(
                        product_id="prod-ltv-nbcu-cable",
                        product_name="NBCU Cable :30 (Bravo/USA/CNBC)",
                        ad_formats=["video"],
                        device_types=[3, 7],
                    ),
                ],
                ad_formats=["video"],
                device_types=[3, 7],  # CTV, Set Top Box
                cat=["IAB1", "IAB19"],  # Arts & Entertainment, Sports
                cattax=2,
                audience_segment_ids=["3", "4", "5", "6", "7", "8"],  # Age 18-44
                geo_targets=["US"],
                base_price=40.0,
                floor_price=28.0,
                tags=["linear-tv", "broadcast", "primetime", "nbcu"],
                is_featured=True,
            ),
        ]

        for pkg in mock_packages:
            await self._upsert_synced_package(storage, pkg)

        logger.info("Created %d mock synced packages", len(mock_packages))

    @staticmethod
    def _classify_inventory_type(item: Any) -> str:
        """Classify an ad server inventory item into an inventory type string.

        Delegates to the canonical mapping in ``catalog_service`` (shared
        with the CSV-mode catalog build). Kept as a flow staticmethod for
        backward compatibility with existing callers/tests.
        """
        from ..services.catalog_service import classify_inventory_type

        return classify_inventory_type(item)

    @staticmethod
    def _infer_deal_types(inv_type: str) -> list[DealType]:
        """Infer supported deal types from inventory type (canonical mapping)."""
        from ..services.catalog_service import infer_deal_types

        return infer_deal_types(inv_type)

    @staticmethod
    def _classify_ad_formats_from_type(inv_type: str) -> list[str]:
        """Map inventory type to OpenRTB ad format names."""
        return {
            "display": ["banner"],
            "video": ["video"],
            "ctv": ["video"],
            "mobile_app": ["banner", "video"],
            "native": ["native"],
            "linear_tv": ["video"],
        }.get(inv_type, ["banner"])

    @staticmethod
    def _classify_device_types_from_type(inv_type: str) -> list[int]:
        """Map inventory type to AdCOM DeviceType integers."""
        return {
            "display": [2, 4, 5],
            "video": [2, 4, 5],
            "ctv": [3, 7],
            "mobile_app": [4, 5],
            "native": [2, 4, 5],
            "linear_tv": [3, 7],
        }.get(inv_type, [2])

    @staticmethod
    def _estimate_base_cpm(inv_type: str) -> float:
        """Estimate base CPM for an inventory type."""
        return {
            "display": 12.0,
            "video": 25.0,
            "ctv": 35.0,
            "mobile_app": 18.0,
            "native": 10.0,
            "linear_tv": 40.0,
        }.get(inv_type, 10.0)

    @listen(sync_from_ad_server)
    async def create_default_products(self) -> None:
        """Create default products for common inventory types.

        Skipped when products were already loaded from an ad server
        (GAM, FreeWheel, or CSV adapter) during sync_from_ad_server.
        """
        if self.state.synced_segments:
            logger.info(
                "Skipping default products — %d synced segments already loaded from ad server",
                len(self.state.synced_segments),
            )
            return

        # Canonical default product data lives in services.catalog_service
        # (single catalog source — EP-3.1/EP-3.3). Deferred import so the
        # flow module keeps its import graph unchanged at import time.
        from ..services.catalog_service import DEFAULT_PRODUCT_CONFIGS, product_from_config

        default_products = DEFAULT_PRODUCT_CONFIGS

        for i, product_config in enumerate(default_products):
            # Shared config→product mapping: enrichment fields
            # (caps, targeting, deliberate unpricing) flow through here too.
            product_def = product_from_config(product_config, f"prod-{i + 1:03d}")

            self.state.products[product_def.product_id] = product_def
            self.state.created_products.append(product_def.product_id)

    @listen(create_default_products)
    async def finalize_setup(self) -> None:
        """Finalize the product setup flow."""
        self.state.status = ExecutionStatus.COMPLETED
        self.state.completed_at = datetime.utcnow()

    def get_products(self) -> dict[str, ProductDefinition]:
        """Get all configured products."""
        return self.state.products

    def add_product(self, product: ProductDefinition) -> None:
        """Add a product to the catalog."""
        self.state.products[product.product_id] = product
