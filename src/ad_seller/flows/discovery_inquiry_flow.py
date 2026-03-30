# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Discovery & Inquiry Flow - Respond to buyer inventory queries.

This flow handles informational queries from buyers:
- What inventory is available?
- What are the pricing tiers?
- What targeting options exist?
- No commitment created - purely informational
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from crewai.flow.flow import Flow, listen, or_, start

from ..config import get_settings
from ..models.buyer_identity import AccessTier, BuyerContext
from ..models.flow_state import ExecutionStatus, SellerFlowState
from ..models.pricing_tiers import TieredPricingConfig


class DiscoveryState(SellerFlowState):
    """State for discovery inquiry flow."""

    # Query context
    query: str = ""
    buyer_context: Optional[BuyerContext] = None

    # Response state
    response_type: str = "catalog"  # catalog, pricing, availability, targeting
    response_data: dict[str, Any] = {}


class DiscoveryInquiryFlow(Flow[DiscoveryState]):
    """Flow for handling buyer discovery queries.

    Buyers can explore inventory without commitment:
    - Browse product catalog
    - Get pricing information (tiered by identity)
    - Check availability ranges
    - Understand targeting options

    Pricing disclosure is tiered:
    - Public: Price ranges only
    - Agency: Agency-specific rates
    - Advertiser: Best available rates
    """

    def __init__(self, pricing_config: Optional[TieredPricingConfig] = None) -> None:
        """Initialize the discovery flow.

        Args:
            pricing_config: Optional tiered pricing configuration
        """
        super().__init__()
        self._settings = get_settings()
        self._pricing_config = pricing_config or TieredPricingConfig(
            seller_organization_id=self._settings.seller_organization_id or "default-seller"
        )

    @start()
    async def receive_query(self) -> None:
        """Receive and categorize the discovery query."""
        self.state.flow_id = str(uuid.uuid4())
        self.state.flow_type = "discovery_inquiry"
        self.state.started_at = datetime.utcnow()
        self.state.status = ExecutionStatus.EVALUATING

        # Categorize query type based on content
        query_lower = self.state.query.lower()

        if "price" in query_lower or "cost" in query_lower or "cpm" in query_lower:
            self.state.response_type = "pricing"
        elif "avail" in query_lower or "inventory" in query_lower or "impressions" in query_lower:
            self.state.response_type = "availability"
        elif "target" in query_lower or "audience" in query_lower:
            self.state.response_type = "targeting"
        else:
            self.state.response_type = "catalog"

    @listen(receive_query)
    async def determine_access_tier(self) -> None:
        """Determine buyer's access tier based on identity."""
        if self.state.buyer_context:
            tier = self.state.buyer_context.effective_tier
        else:
            tier = AccessTier.PUBLIC

        self.state.response_data["access_tier"] = tier.value
        self.state.response_data["tier_config"] = self._pricing_config.get_tier_config(tier)

    @listen(determine_access_tier)
    async def prepare_catalog_response(self) -> None:
        """Prepare product catalog response."""
        if self.state.response_type != "catalog":
            return

        tier = AccessTier(self.state.response_data.get("access_tier", "public"))
        tier_config = self._pricing_config.get_tier_config(tier)

        catalog = []
        for product_id, product in self.state.products.items():
            product_info = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "inventory_type": product.inventory_type,
                "deal_types": [dt.value for dt in product.supported_deal_types],
            }

            # Add pricing based on tier
            if tier_config.show_exact_price:
                product_info["price"] = product.base_cpm
                product_info["currency"] = product.currency
            else:
                # Show range for public tier
                variance = tier_config.price_range_variance
                low = product.base_cpm * (1 - variance)
                high = product.base_cpm * (1 + variance)
                product_info["price_range"] = f"${low:.0f}-${high:.0f} CPM"

            catalog.append(product_info)

        self.state.response_data["catalog"] = catalog

    @listen(determine_access_tier)
    async def prepare_pricing_response(self) -> None:
        """Prepare pricing information response."""
        if self.state.response_type != "pricing":
            return

        tier = AccessTier(self.state.response_data.get("access_tier", "public"))
        tier_config = self._pricing_config.get_tier_config(tier)

        pricing_info = {
            "tier": tier.value,
            "tier_name": tier_config.tier_name,
            "description": tier_config.description,
            "negotiation_enabled": tier_config.negotiation_enabled,
            "volume_discounts_enabled": tier_config.volume_discounts_enabled,
        }

        if tier_config.show_exact_price:
            pricing_info["discount_from_msrp"] = f"{tier_config.tier_discount * 100:.0f}%"

        # Product-specific pricing
        product_pricing = []
        for product_id, product in self.state.products.items():
            price_info = {
                "product_id": product.product_id,
                "name": product.name,
            }

            if tier_config.show_exact_price:
                # Apply tier discount
                discounted_price = product.base_cpm * (1 - tier_config.tier_discount)
                price_info["price"] = round(discounted_price, 2)
                price_info["floor"] = product.floor_cpm
                price_info["pricing_model"] = "CPM"
            else:
                variance = tier_config.price_range_variance
                low = product.base_cpm * (1 - variance)
                high = product.base_cpm * (1 + variance)
                price_info["price_range"] = f"${low:.0f}-${high:.0f}"

            product_pricing.append(price_info)

        pricing_info["products"] = product_pricing
        self.state.response_data["pricing"] = pricing_info

    @listen(determine_access_tier)
    async def prepare_availability_response(self) -> None:
        """Prepare availability information response."""
        if self.state.response_type != "availability":
            return

        tier = AccessTier(self.state.response_data.get("access_tier", "public"))
        tier_config = self._pricing_config.get_tier_config(tier)

        availability_info = {
            "granularity": tier_config.avails_granularity,
        }

        # Product availability (simplified for discovery)
        product_avails = []
        for product_id, product in self.state.products.items():
            avail_info = {
                "product_id": product.product_id,
                "name": product.name,
                "inventory_type": product.inventory_type,
            }

            # Availability detail depends on tier
            if tier_config.avails_granularity == "detailed":
                avail_info["estimated_monthly_impressions"] = "10M-50M"
                avail_info["fill_rate"] = "85-95%"
            elif tier_config.avails_granularity == "moderate":
                avail_info["availability"] = "High"
            else:
                avail_info["availability"] = "Available"

            product_avails.append(avail_info)

        availability_info["products"] = product_avails
        self.state.response_data["availability"] = availability_info

    @listen(determine_access_tier)
    async def prepare_targeting_response(self) -> None:
        """Prepare targeting options response."""
        if self.state.response_type != "targeting":
            return

        tier = AccessTier(self.state.response_data.get("access_tier", "public"))

        targeting_info = {
            "audience_targeting": {
                "demographics": ["Age", "Gender", "HHI"],
                "interests": ["IAB Content Categories"],
                "behaviors": ["Purchase Intent", "In-Market"],
            },
            "contextual_targeting": {
                "content_categories": "IAB Content Taxonomy",
                "keywords": "Keyword targeting available",
                "brand_safety": "Pre-bid and post-bid solutions",
            },
            "geographic_targeting": {
                "country": True,
                "region": True,
                "dma": True,
                "zip": tier != AccessTier.PUBLIC,  # Zip only for authenticated
            },
            "device_targeting": {
                "device_type": ["Desktop", "Mobile", "Tablet", "CTV"],
                "os": ["iOS", "Android", "Windows", "MacOS"],
                "browser": True,
            },
        }

        # Premium targeting for authenticated tiers
        if tier != AccessTier.PUBLIC:
            targeting_info["advanced_targeting"] = {
                "first_party_data": "Available with data sharing agreement",
                "custom_segments": "Build custom audience segments",
                "lookalike_modeling": "Expand reach with similar audiences",
            }

        self.state.response_data["targeting"] = targeting_info

    @listen(or_(
        prepare_catalog_response,
        prepare_pricing_response,
        prepare_availability_response,
        prepare_targeting_response,
    ))
    async def finalize_response(self) -> None:
        """Finalize the discovery response."""
        self.state.status = ExecutionStatus.COMPLETED
        self.state.completed_at = datetime.utcnow()

    def query(
        self,
        query: str,
        buyer_context: Optional[BuyerContext] = None,
        products: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Execute a discovery query (synchronous - for CLI use).

        Args:
            query: The discovery query
            buyer_context: Optional buyer identity context
            products: Product catalog to query against

        Returns:
            Discovery response data
        """
        self.state.query = query
        self.state.buyer_context = buyer_context
        if products:
            self.state.products = products

        # Run the flow synchronously
        self.kickoff()

        return self.state.response_data

    async def query_async(
        self,
        query: str,
        buyer_context: Optional[BuyerContext] = None,
        products: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Execute a discovery query (async - for API use).

        Args:
            query: The discovery query
            buyer_context: Optional buyer identity context
            products: Product catalog to query against

        Returns:
            Discovery response data
        """
        self.state.query = query
        self.state.buyer_context = buyer_context
        if products:
            self.state.products = products

        # Run the flow asynchronously
        await self.kickoff_async()

        return self.state.response_data
