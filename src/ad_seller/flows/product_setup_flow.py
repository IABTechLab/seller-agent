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
from ..models.core import DealType, PricingModel
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

    @listen(initialize_setup)
    async def sync_from_ad_server(self) -> None:
        """Sync inventory from ad server if configured.

        When an ad server is configured, imports inventory via
        AdServerClient.list_inventory() and creates Layer 1 synced packages.
        Otherwise, creates mock synced packages for development.
        """
        if not self._settings.gam_network_code and not self._settings.freewheel_sh_mcp_url:
            self.state.warnings.append("No ad server configured, creating mock synced packages")
            await self._create_mock_synced_packages()
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

            for inv_type, inv_items in grouped.items():
                ad_formats = self._classify_ad_formats_from_type(inv_type)
                device_types = self._classify_device_types_from_type(inv_type)
                base_cpm = self._estimate_base_cpm(inv_type)

                package = Package(
                    package_id=f"pkg-{uuid.uuid4().hex[:8]}",
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

                await storage.set_package(package.package_id, package.model_dump(mode="json"))
                self.state.synced_segments.append(package.package_id)

            logger.info("Synced %d packages from ad server", len(grouped))

        except Exception as e:
            self.state.warnings.append(f"Ad server sync failed, using mocks: {e}")
            await self._create_mock_synced_packages()

    async def _create_mock_synced_packages(self) -> None:
        """Create mock Layer 1 packages for development without ad server creds."""
        from ..storage.factory import get_storage

        storage = await get_storage()

        mock_packages = [
            Package(
                package_id=f"pkg-{uuid.uuid4().hex[:8]}",
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
                package_id=f"pkg-{uuid.uuid4().hex[:8]}",
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
                package_id=f"pkg-{uuid.uuid4().hex[:8]}",
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
                package_id=f"pkg-{uuid.uuid4().hex[:8]}",
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
            await storage.set_package(pkg.package_id, pkg.model_dump(mode="json"))
            self.state.synced_segments.append(pkg.package_id)

        logger.info("Created %d mock synced packages", len(mock_packages))

    @staticmethod
    def _classify_inventory_type(item: Any) -> str:
        """Classify an ad server inventory item into an inventory type string."""
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
        """Create default products for common inventory types."""
        default_products = [
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

        for i, product_config in enumerate(default_products):
            product_def = ProductDefinition(
                product_id=f"prod-{i + 1:03d}",
                name=product_config["name"],
                description=product_config.get("description"),
                inventory_type=product_config["inventory_type"],
                supported_deal_types=product_config["supported_deal_types"],
                supported_pricing_models=product_config["supported_pricing_models"],
                base_cpm=product_config["base_cpm"],
                floor_cpm=product_config["floor_cpm"],
            )

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
