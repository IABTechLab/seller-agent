# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Google Ad Manager REST API client.

Provides async methods for reading from GAM using the REST API (Beta).
Used for listing ad units, orders, line items, and managing private auctions.
"""

from typing import Any, Optional

from ..config import get_settings
from ..models.gam import (
    GAMAdUnit,
    GAMAdUnitSize,
    GAMLineItem,
    GAMMoney,
    GAMOrder,
    GAMPrivateAuction,
    GAMPrivateAuctionDeal,
    GAMTargeting,
)


class GAMRestClient:
    """REST API client for reading from Google Ad Manager.

    Uses the GAM REST API (Beta) for read operations and private auction management.
    Requires google-api-python-client and google-auth packages.

    Usage:
        async with GAMRestClient() as client:
            ad_units = await client.list_ad_units()
    """

    def __init__(
        self,
        network_code: Optional[str] = None,
        credentials_path: Optional[str] = None,
    ):
        """Initialize the GAM REST client.

        Args:
            network_code: GAM network code (defaults to settings)
            credentials_path: Path to service account JSON key (defaults to settings)
        """
        settings = get_settings()
        self.network_code = network_code or settings.gam_network_code
        self.credentials_path = credentials_path or settings.gam_json_key_path
        self.application_name = settings.gam_application_name

        self._service: Optional[Any] = None
        self._credentials: Optional[Any] = None

    async def __aenter__(self) -> "GAMRestClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to GAM REST API."""
        if not self.network_code or not self.credentials_path:
            raise ValueError(
                "GAM network_code and credentials_path are required. "
                "Set GAM_NETWORK_CODE and GAM_JSON_KEY_PATH environment variables."
            )

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            # Load service account credentials
            self._credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=["https://www.googleapis.com/auth/admanager"],
            )

            # Build the GAM API service. Ad Manager self-hosts its own
            # discovery document (admanager.googleapis.com/$discovery/rest)
            # rather than registering with the generic, centrally-indexed
            # discovery service googleapiclient.discovery.build() queries
            # by default (and it isn't in the locally bundled discovery
            # docs either) -- without an explicit discoveryServiceUrl this
            # raises UnknownApiNameOrVersion for every real connection
            # attempt, silently degrading every GAM sync to mock data.
            self._service = build(
                "admanager",
                "v1",
                credentials=self._credentials,
                discoveryServiceUrl="https://admanager.googleapis.com/$discovery/rest?version=v1",
                static_discovery=False,
                cache_discovery=False,
            )
        except ImportError:
            raise ImportError(
                "GAM REST client requires google-api-python-client and google-auth. "
                "Install with: pip install 'ad_seller_system[gam]'"
            )

    async def disconnect(self) -> None:
        """Disconnect from GAM REST API."""
        self._service = None
        self._credentials = None

    def _ensure_connected(self) -> None:
        """Ensure the client is connected."""
        if not self._service:
            raise RuntimeError("GAM REST client not connected. Use 'async with' context.")

    # =========================================================================
    # Ad Unit Operations
    # =========================================================================

    async def list_ad_units(
        self,
        page_size: int = 100,
        page_token: Optional[str] = None,
        filter_str: Optional[str] = None,
    ) -> tuple[list[GAMAdUnit], Optional[str]]:
        """List ad units in the network.

        Args:
            page_size: Number of results per page (max 100)
            page_token: Token for pagination
            filter_str: Optional filter string

        Returns:
            Tuple of (list of ad units, next page token or None)
        """
        self._ensure_connected()

        parent = f"networks/{self.network_code}"
        request = (
            self._service.networks()
            .adUnits()
            .list(
                parent=parent,
                pageSize=min(page_size, 100),
                pageToken=page_token,
                filter=filter_str,
            )
        )

        response = request.execute()

        ad_units = []
        for item in response.get("adUnits", []):
            ad_unit = self._parse_ad_unit(item)
            ad_units.append(ad_unit)

        next_token = response.get("nextPageToken")
        return ad_units, next_token

    async def get_ad_unit(self, ad_unit_id: str) -> GAMAdUnit:
        """Get a specific ad unit by ID.

        Args:
            ad_unit_id: The ad unit ID

        Returns:
            The ad unit
        """
        self._ensure_connected()

        name = f"networks/{self.network_code}/adUnits/{ad_unit_id}"
        response = self._service.networks().adUnits().get(name=name).execute()

        return self._parse_ad_unit(response)

    def _parse_ad_unit(self, data: dict[str, Any]) -> GAMAdUnit:
        """Parse API response into GAMAdUnit model."""
        # Extract ID from resource name (networks/123/adUnits/456 -> 456)
        name = data.get("name", "")
        ad_unit_id = name.split("/")[-1] if "/" in name else name

        # Parse ad unit sizes
        sizes = []
        for size_data in data.get("adUnitSizes", []):
            size = size_data.get("size", {})
            sizes.append(
                GAMAdUnitSize(
                    size={
                        "width": size.get("width", 0),
                        "height": size.get("height", 0),
                        "is_aspect_ratio": size.get("isAspectRatio", False),
                    },
                    environment_type=size_data.get("environmentType", "BROWSER"),
                )
            )

        return GAMAdUnit(
            id=ad_unit_id,
            name=data.get("displayName", ""),
            parent_id=data.get("parentAdUnit", "").split("/")[-1]
            if data.get("parentAdUnit")
            else None,
            description=data.get("description"),
            ad_unit_code=data.get("adUnitCode"),
            status=data.get("status", "ACTIVE"),
            ad_unit_sizes=sizes,
            has_children=data.get("hasChildren", False),
        )

    # =========================================================================
    # Order Operations (Read-only via REST)
    # =========================================================================

    async def list_orders(
        self,
        page_size: int = 100,
        page_token: Optional[str] = None,
        filter_str: Optional[str] = None,
    ) -> tuple[list[GAMOrder], Optional[str]]:
        """List orders in the network.

        Args:
            page_size: Number of results per page
            page_token: Token for pagination
            filter_str: Optional filter string

        Returns:
            Tuple of (list of orders, next page token or None)
        """
        self._ensure_connected()

        parent = f"networks/{self.network_code}"
        request = (
            self._service.networks()
            .orders()
            .list(
                parent=parent,
                pageSize=min(page_size, 100),
                pageToken=page_token,
                filter=filter_str,
            )
        )

        response = request.execute()

        orders = []
        for item in response.get("orders", []):
            order = self._parse_order(item)
            orders.append(order)

        next_token = response.get("nextPageToken")
        return orders, next_token

    async def get_order(self, order_id: str) -> GAMOrder:
        """Get a specific order by ID."""
        self._ensure_connected()

        name = f"networks/{self.network_code}/orders/{order_id}"
        response = self._service.networks().orders().get(name=name).execute()

        return self._parse_order(response)

    def _parse_order(self, data: dict[str, Any]) -> GAMOrder:
        """Parse API response into GAMOrder model."""
        name = data.get("name", "")
        order_id = name.split("/")[-1] if "/" in name else name

        return GAMOrder(
            id=order_id,
            name=data.get("displayName", ""),
            advertiser_id=data.get("advertiser", "").split("/")[-1],
            trafficker_id=data.get("trafficker", "").split("/")[-1],
            agency_id=data.get("agency", "").split("/")[-1] if data.get("agency") else None,
            status=data.get("status", "DRAFT"),
            external_order_id=data.get("externalOrderId"),
            notes=data.get("notes"),
            is_programmatic=data.get("isProgrammatic", False),
        )

    # =========================================================================
    # Line Item Operations (Read-only via REST)
    # =========================================================================

    async def list_line_items(
        self,
        order_id: Optional[str] = None,
        page_size: int = 100,
        page_token: Optional[str] = None,
        filter_str: Optional[str] = None,
    ) -> tuple[list[GAMLineItem], Optional[str]]:
        """List line items, optionally filtered by order.

        Args:
            order_id: Optional order ID to filter by
            page_size: Number of results per page
            page_token: Token for pagination
            filter_str: Optional filter string

        Returns:
            Tuple of (list of line items, next page token or None)
        """
        self._ensure_connected()

        parent = f"networks/{self.network_code}"

        # Build filter
        filters = []
        if order_id:
            filters.append(f'order="networks/{self.network_code}/orders/{order_id}"')
        if filter_str:
            filters.append(filter_str)

        combined_filter = " AND ".join(filters) if filters else None

        request = (
            self._service.networks()
            .lineItems()
            .list(
                parent=parent,
                pageSize=min(page_size, 100),
                pageToken=page_token,
                filter=combined_filter,
            )
        )

        response = request.execute()

        line_items = []
        for item in response.get("lineItems", []):
            line_item = self._parse_line_item(item)
            line_items.append(line_item)

        next_token = response.get("nextPageToken")
        return line_items, next_token

    async def get_line_item(self, line_item_id: str) -> GAMLineItem:
        """Get a specific line item by ID."""
        self._ensure_connected()

        name = f"networks/{self.network_code}/lineItems/{line_item_id}"
        response = self._service.networks().lineItems().get(name=name).execute()

        return self._parse_line_item(response)

    def _parse_line_item(self, data: dict[str, Any]) -> GAMLineItem:
        """Parse API response into GAMLineItem model."""
        name = data.get("name", "")
        line_item_id = name.split("/")[-1] if "/" in name else name

        # Parse cost
        cost_data = data.get("costPerUnit", {})
        cost_per_unit = GAMMoney(
            currency_code=cost_data.get("currencyCode", "USD"),
            micro_amount=int(cost_data.get("units", "0")) * 1_000_000
            + int(cost_data.get("nanos", 0)) // 1000,
        )

        return GAMLineItem(
            id=line_item_id,
            order_id=data.get("order", "").split("/")[-1],
            name=data.get("displayName", ""),
            line_item_type=data.get("lineItemType", "STANDARD"),
            status=data.get("status", "DRAFT"),
            cost_type=data.get("costType", "CPM"),
            cost_per_unit=cost_per_unit,
            primary_goal={
                "goal_type": data.get("primaryGoal", {}).get("goalType", "LIFETIME"),
                "unit_type": data.get("primaryGoal", {}).get("unitType", "IMPRESSIONS"),
                "units": data.get("primaryGoal", {}).get("units", -1),
            },
            external_id=data.get("externalId"),
        )

    # =========================================================================
    # Private Auction Operations
    # =========================================================================

    async def list_private_auctions(
        self,
        page_size: int = 100,
        page_token: Optional[str] = None,
    ) -> tuple[list[GAMPrivateAuction], Optional[str]]:
        """List private auctions in the network.

        Returns:
            Tuple of (list of private auctions, next page token or None)
        """
        self._ensure_connected()

        parent = f"networks/{self.network_code}"
        request = (
            self._service.networks()
            .privateAuctions()
            .list(
                parent=parent,
                pageSize=min(page_size, 100),
                pageToken=page_token,
            )
        )

        response = request.execute()

        auctions = []
        for item in response.get("privateAuctions", []):
            auction = GAMPrivateAuction(
                id=item.get("name", "").split("/")[-1],
                name=item.get("displayName", ""),
                description=item.get("description"),
                status=item.get("status", "ACTIVE"),
            )
            auctions.append(auction)

        next_token = response.get("nextPageToken")
        return auctions, next_token

    async def get_private_auction(self, private_auction_id: str) -> GAMPrivateAuction:
        """Get a specific private auction by ID."""
        self._ensure_connected()

        name = f"networks/{self.network_code}/privateAuctions/{private_auction_id}"
        response = self._service.networks().privateAuctions().get(name=name).execute()

        return GAMPrivateAuction(
            id=response.get("name", "").split("/")[-1],
            name=response.get("displayName", ""),
            description=response.get("description"),
            status=response.get("status", "ACTIVE"),
        )

    async def create_private_auction(
        self,
        display_name: str,
        description: Optional[str] = None,
    ) -> GAMPrivateAuction:
        """Create a new private auction.

        Args:
            display_name: Display name for the auction
            description: Optional description

        Returns:
            The created private auction
        """
        self._ensure_connected()

        parent = f"networks/{self.network_code}"
        body = {
            "displayName": display_name,
        }
        if description:
            body["description"] = description

        response = (
            self._service.networks().privateAuctions().create(parent=parent, body=body).execute()
        )

        return GAMPrivateAuction(
            id=response.get("name", "").split("/")[-1],
            name=response.get("displayName", ""),
            description=response.get("description"),
            status=response.get("status", "ACTIVE"),
        )

    # =========================================================================
    # Private Auction Deal Operations
    # =========================================================================

    async def list_private_auction_deals(
        self,
        private_auction_id: Optional[str] = None,
        page_size: int = 100,
        page_token: Optional[str] = None,
    ) -> tuple[list[GAMPrivateAuctionDeal], Optional[str]]:
        """List private auction deals.

        Args:
            private_auction_id: Optional auction ID to filter by
            page_size: Number of results per page
            page_token: Token for pagination

        Returns:
            Tuple of (list of deals, next page token or None)
        """
        self._ensure_connected()

        if private_auction_id:
            parent = f"networks/{self.network_code}/privateAuctions/{private_auction_id}"
        else:
            parent = f"networks/{self.network_code}"

        request = (
            self._service.networks()
            .privateAuctionDeals()
            .list(
                parent=parent,
                pageSize=min(page_size, 100),
                pageToken=page_token,
            )
        )

        response = request.execute()

        deals = []
        for item in response.get("privateAuctionDeals", []):
            deal = self._parse_private_auction_deal(item)
            deals.append(deal)

        next_token = response.get("nextPageToken")
        return deals, next_token

    async def create_private_auction_deal(
        self,
        private_auction_id: str,
        buyer_account_id: str,
        floor_price: float,
        currency: str = "USD",
        targeting: Optional[GAMTargeting] = None,
        external_deal_id: Optional[str] = None,
    ) -> GAMPrivateAuctionDeal:
        """Create a private auction deal.

        Args:
            private_auction_id: The parent private auction ID
            buyer_account_id: The buyer's account ID
            floor_price: Floor price CPM
            currency: Currency code (default USD)
            targeting: Optional targeting configuration
            external_deal_id: Optional external reference ID

        Returns:
            The created deal
        """
        self._ensure_connected()

        parent = f"networks/{self.network_code}/privateAuctions/{private_auction_id}"

        # Convert floor price to Money format
        units = int(floor_price)
        nanos = int((floor_price - units) * 1_000_000_000)

        body: dict[str, Any] = {
            "buyer": buyer_account_id,
            "floorPrice": {
                "currencyCode": currency,
                "units": str(units),
                "nanos": nanos,
            },
        }

        if external_deal_id:
            body["externalDealId"] = external_deal_id

        if targeting and targeting.inventory_targeting:
            body["targeting"] = {
                "inventoryTargeting": {
                    "targetedAdUnits": [
                        {"adUnitId": t.ad_unit_id}
                        for t in targeting.inventory_targeting.targeted_ad_units
                    ]
                }
            }

        response = (
            self._service.networks()
            .privateAuctionDeals()
            .create(parent=parent, body=body)
            .execute()
        )

        return self._parse_private_auction_deal(response)

    async def update_private_auction_deal(
        self,
        deal_id: str,
        floor_price: Optional[float] = None,
        currency: str = "USD",
    ) -> GAMPrivateAuctionDeal:
        """Update a private auction deal.

        Args:
            deal_id: The deal ID
            floor_price: New floor price (optional)
            currency: Currency code

        Returns:
            The updated deal
        """
        self._ensure_connected()

        name = f"networks/{self.network_code}/privateAuctionDeals/{deal_id}"

        body: dict[str, Any] = {}
        update_mask = []

        if floor_price is not None:
            units = int(floor_price)
            nanos = int((floor_price - units) * 1_000_000_000)
            body["floorPrice"] = {
                "currencyCode": currency,
                "units": str(units),
                "nanos": nanos,
            }
            update_mask.append("floorPrice")

        response = (
            self._service.networks()
            .privateAuctionDeals()
            .patch(name=name, body=body, updateMask=",".join(update_mask))
            .execute()
        )

        return self._parse_private_auction_deal(response)

    def _parse_private_auction_deal(self, data: dict[str, Any]) -> GAMPrivateAuctionDeal:
        """Parse API response into GAMPrivateAuctionDeal model."""
        name = data.get("name", "")
        deal_id = name.split("/")[-1] if "/" in name else name

        # Extract private auction ID from name
        parts = name.split("/")
        private_auction_id = ""
        for i, part in enumerate(parts):
            if part == "privateAuctions" and i + 1 < len(parts):
                private_auction_id = parts[i + 1]
                break

        # Parse floor price
        floor_data = data.get("floorPrice", {})
        floor_price = GAMMoney(
            currency_code=floor_data.get("currencyCode", "USD"),
            micro_amount=int(floor_data.get("units", "0")) * 1_000_000
            + int(floor_data.get("nanos", 0)) // 1000,
        )

        return GAMPrivateAuctionDeal(
            id=deal_id,
            private_auction_id=private_auction_id,
            buyer_account_id=data.get("buyer", ""),
            external_deal_id=data.get("externalDealId"),
            floor_price=floor_price,
            status=data.get("status", "ACTIVE"),
        )

    # =========================================================================
    # Report Operations
    # =========================================================================

    async def run_report(self, report_query: dict[str, Any]) -> dict[str, Any]:
        """Run a GAM report.

        Args:
            report_query: Report query specification

        Returns:
            Report results
        """
        self._ensure_connected()

        parent = f"networks/{self.network_code}"

        # Create report job
        response = (
            self._service.networks().reports().create(parent=parent, body=report_query).execute()
        )

        return response
