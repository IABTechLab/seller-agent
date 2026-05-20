# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Google Ad Manager SOAP API client.

Provides methods for writing to GAM using the SOAP API.
Used for creating orders, line items, and managing audience segments.
Also provides read/reporting methods for delivery data.
"""

import io
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

from ..config import get_settings
from ..models.gam import (
    GAMAdUnit,
    GAMAdUnitSize,
    GAMAudienceSegment,
    GAMAudienceSegmentStatus,
    GAMAudienceSegmentType,
    GAMCompany,
    GAMCostType,
    GAMGoal,
    GAMGoalType,
    GAMLineItem,
    GAMLineItemStatus,
    GAMLineItemType,
    GAMMoney,
    GAMOrder,
    GAMOrderStatus,
    GAMSize,
    GAMTargeting,
    GAMUnitType,
)

_SUPPORTED_VERSIONS = ("v202505", "v202508", "v202511", "v202602")
_DEFAULT_VERSION = "v202505"


class GAMSoapClient:
    """SOAP API client for writing to Google Ad Manager.

    Uses the GAM SOAP API (via googleads library) for write operations
    like creating orders, line items, and audience segments.

    Usage:
        client = GAMSoapClient()
        client.connect()
        order = client.create_order(name="My Order", advertiser_id="123", ...)
    """

    def __init__(
        self,
        network_code: Optional[str] = None,
        credentials_path: Optional[str] = None,
    ):
        """Initialize the GAM SOAP client.

        Args:
            network_code: GAM network code (defaults to settings)
            credentials_path: Path to service account JSON key (defaults to settings)
        """
        settings = get_settings()
        self.network_code = network_code or settings.gam_network_code
        self.credentials_path = credentials_path or settings.gam_json_key_path
        self.application_name = settings.gam_application_name
        raw_version = settings.gam_api_version or _DEFAULT_VERSION
        self.api_version = raw_version if raw_version in _SUPPORTED_VERSIONS else _DEFAULT_VERSION
        self.default_trafficker_id = settings.gam_default_trafficker_id

        self._client: Optional[Any] = None

    def connect(self) -> None:
        """Connect to GAM SOAP API."""
        if not self.network_code or not self.credentials_path:
            raise ValueError(
                "GAM network_code and credentials_path are required. "
                "Set GAM_NETWORK_CODE and GAM_JSON_KEY_PATH environment variables."
            )

        try:
            from googleads import ad_manager, oauth2

            # Load credentials
            oauth2_client = oauth2.GoogleServiceAccountClient(
                self.credentials_path,
                oauth2.GetAPIScope("ad_manager"),
            )

            # Create Ad Manager client
            self._client = ad_manager.AdManagerClient(
                oauth2_client,
                self.application_name,
                network_code=self.network_code,
            )
        except ImportError:
            raise ImportError(
                "GAM SOAP client requires googleads. "
                "Install with: pip install 'ad_seller_system[gam]'"
            )

    def disconnect(self) -> None:
        """Disconnect from GAM SOAP API."""
        self._client = None

    def _ensure_connected(self) -> None:
        """Ensure the client is connected."""
        if not self._client:
            raise RuntimeError("GAM SOAP client not connected. Call connect() first.")

    def _get_service(self, service_name: str) -> Any:
        """Get a GAM service by name."""
        self._ensure_connected()
        return self._client.GetService(service_name, version=self.api_version)

    # =========================================================================
    # Company Operations
    # =========================================================================

    def get_advertiser_by_name(self, name: str) -> Optional[GAMCompany]:
        """Look up an advertiser company by name.

        Args:
            name: Company name to search for

        Returns:
            The company if found, None otherwise
        """
        company_service = self._get_service("CompanyService")

        # Build filter statement
        statement = f"WHERE type = 'ADVERTISER' AND name = '{name}' LIMIT 1"

        response = company_service.getCompaniesByStatement({"query": statement})

        # SOAP returns ZEEP objects - use getattr
        results = getattr(response, "results", None) or []
        if results:
            company_data = results[0]
            return GAMCompany(
                id=str(getattr(company_data, "id", 0)),
                name=getattr(company_data, "name", ""),
                type=getattr(company_data, "type", "ADVERTISER"),
                external_id=getattr(company_data, "externalId", None),
            )
        return None

    def get_or_create_advertiser(self, name: str, external_id: Optional[str] = None) -> str:
        """Get an existing advertiser or create a new one.

        Args:
            name: Advertiser company name
            external_id: Optional external ID for the company

        Returns:
            The company ID
        """
        # Try to find existing
        existing = self.get_advertiser_by_name(name)
        if existing:
            return existing.id

        # Create new advertiser
        company_service = self._get_service("CompanyService")

        company = {
            "name": name,
            "type": "ADVERTISER",
        }
        if external_id:
            company["externalId"] = external_id

        result = company_service.createCompanies([company])
        # SOAP returns ZEEP objects
        return str(getattr(result[0], "id", 0))

    # =========================================================================
    # Order Operations
    # =========================================================================

    def create_order(
        self,
        name: str,
        advertiser_id: str,
        trafficker_id: Optional[str] = None,
        agency_id: Optional[str] = None,
        notes: Optional[str] = None,
        external_order_id: Optional[str] = None,
        is_programmatic: bool = False,  # Default False to avoid INVALID_SUPPLY_PATH_FOR_PROGRAMMATIC_ORDER
    ) -> GAMOrder:
        """Create a new order in GAM.

        Args:
            name: Order name
            advertiser_id: Advertiser company ID
            trafficker_id: Trafficker user ID (defaults to settings)
            agency_id: Optional agency company ID
            notes: Optional order notes
            external_order_id: Optional external reference ID
            is_programmatic: Whether this is a programmatic order

        Returns:
            The created order
        """
        order_service = self._get_service("OrderService")

        # Use default trafficker if not specified
        trafficker = trafficker_id or self.default_trafficker_id
        if not trafficker:
            raise ValueError(
                "trafficker_id is required. Set GAM_DEFAULT_TRAFFICKER_ID or pass trafficker_id."
            )

        order = {
            "name": name,
            "advertiserId": int(advertiser_id),
            "traffickerId": int(trafficker),
        }

        # Only set isProgrammatic if explicitly True (avoid INVALID_SUPPLY_PATH error)
        if is_programmatic:
            order["isProgrammatic"] = True

        if agency_id:
            order["agencyId"] = int(agency_id)
        if notes:
            order["notes"] = notes
        if external_order_id:
            order["externalOrderId"] = external_order_id

        result = order_service.createOrders([order])
        order_data = result[0]

        # Use the shared _parse_order method
        return self._parse_order(order_data)

    def approve_order(self, order_id: str) -> GAMOrder:
        """Approve an order (submit for delivery).

        Args:
            order_id: The order ID

        Returns:
            The updated order
        """
        order_service = self._get_service("OrderService")

        # Perform approve action
        action = {"xsi_type": "ApproveOrders"}
        statement = f"WHERE id = {order_id}"

        order_service.performOrderAction(action, {"query": statement})

        # Fetch updated order
        response = order_service.getOrdersByStatement({"query": statement})
        # ZEEP returns objects - use getattr
        results = getattr(response, "results", None) or []
        order_data = results[0] if results else None

        return self._parse_order(order_data)

    def _parse_order(self, data: Any) -> GAMOrder:
        """Parse SOAP response (ZEEP object) into GAMOrder model."""
        # ZEEP returns objects - use getattr instead of dict access
        # For optional fields, carefully check for None/0/"" to avoid validation errors
        external_order_id = getattr(data, "externalOrderId", None)
        notes = getattr(data, "notes", None)
        agency_id = getattr(data, "agencyId", None)

        return GAMOrder(
            id=str(getattr(data, "id", 0)),
            name=getattr(data, "name", ""),
            advertiser_id=str(getattr(data, "advertiserId", 0)),
            trafficker_id=str(getattr(data, "traffickerId", 0)),
            agency_id=str(agency_id) if agency_id and agency_id != 0 else None,
            status=GAMOrderStatus(getattr(data, "status", "DRAFT")),
            external_order_id=str(external_order_id)
            if external_order_id and external_order_id not in (0, "")
            else None,
            notes=str(notes) if notes and notes not in (0, "") else None,
            is_programmatic=getattr(data, "isProgrammatic", False),
        )

    # =========================================================================
    # Line Item Operations
    # =========================================================================

    def create_line_item(
        self,
        order_id: str,
        name: str,
        line_item_type: GAMLineItemType,
        targeting: GAMTargeting,
        cost_per_unit: GAMMoney,
        goal: GAMGoal,
        start_time: datetime,
        end_time: datetime,
        cost_type: GAMCostType = GAMCostType.CPM,
        creative_sizes: Optional[list[tuple[int, int]]] = None,
        external_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> GAMLineItem:
        """Create a line item within an order.

        Args:
            order_id: The parent order ID
            name: Line item name
            line_item_type: Type of line item
            targeting: Targeting configuration
            cost_per_unit: Cost per unit (CPM, CPC, etc.)
            goal: Delivery goal
            start_time: Start datetime
            end_time: End datetime
            cost_type: Cost type (CPM, CPC, CPD, CPCV)
            creative_sizes: List of (width, height) tuples for creative placeholders.
                           Defaults to common display sizes if not provided.
            external_id: Optional external reference ID
            notes: Optional notes

        Returns:
            The created line item
        """
        line_item_service = self._get_service("LineItemService")

        # Build targeting
        targeting_dict: dict[str, Any] = {}

        if targeting.inventory_targeting:
            targeted_units = []
            for unit in targeting.inventory_targeting.targeted_ad_units:
                targeted_units.append(
                    {
                        "adUnitId": int(unit.ad_unit_id),
                        "includeDescendants": unit.include_descendants,
                    }
                )
            targeting_dict["inventoryTargeting"] = {
                "targetedAdUnits": targeted_units,
            }

        if targeting.custom_targeting:
            targeting_dict["customTargeting"] = targeting.custom_targeting.model_dump(by_alias=True)

        # Default creative sizes if not provided
        if creative_sizes is None:
            creative_sizes = [(300, 250), (728, 90)]  # Common display sizes

        # Build creative placeholders
        creative_placeholders = [
            {"size": {"width": w, "height": h, "isAspectRatio": False}} for w, h in creative_sizes
        ]

        # Build line item
        line_item = {
            "orderId": int(order_id),
            "name": name,
            "lineItemType": line_item_type.value,
            "targeting": targeting_dict,
            "costType": cost_type.value,
            "costPerUnit": {
                "currencyCode": cost_per_unit.currency_code,
                "microAmount": cost_per_unit.micro_amount,
            },
            "primaryGoal": {
                "goalType": goal.goal_type.value,
                "unitType": goal.unit_type.value,
                "units": goal.units,
            },
            "startDateTime": self._to_soap_datetime(start_time),
            "endDateTime": self._to_soap_datetime(end_time),
            "creativePlaceholders": creative_placeholders,
        }

        if external_id:
            line_item["externalId"] = external_id
        if notes:
            line_item["notes"] = notes

        result = line_item_service.createLineItems([line_item])
        line_item_data = result[0]

        return self._parse_line_item(line_item_data)

    def update_line_item(
        self,
        line_item_id: str,
        updates: dict[str, Any],
    ) -> GAMLineItem:
        """Update an existing line item.

        Args:
            line_item_id: The line item ID
            updates: Dictionary of fields to update

        Returns:
            The updated line item
        """
        line_item_service = self._get_service("LineItemService")

        # Fetch current line item
        statement = f"WHERE id = {line_item_id}"
        response = line_item_service.getLineItemsByStatement({"query": statement})
        # ZEEP returns objects - use getattr
        results = getattr(response, "results", None) or []
        line_item = results[0] if results else {}

        # Apply updates (ZEEP objects support setattr)
        for key, value in updates.items():
            setattr(line_item, key, value)

        result = line_item_service.updateLineItems([line_item])
        return self._parse_line_item(result[0])

    def _parse_line_item(self, data: Any) -> GAMLineItem:
        """Parse SOAP response (ZEEP object) into GAMLineItem model."""
        # ZEEP returns objects - use getattr instead of dict access
        cost_data = getattr(data, "costPerUnit", None)
        primary_goal = getattr(data, "primaryGoal", None)

        return GAMLineItem(
            id=str(getattr(data, "id", 0)),
            order_id=str(getattr(data, "orderId", 0)),
            name=getattr(data, "name", ""),
            line_item_type=GAMLineItemType(getattr(data, "lineItemType", "STANDARD")),
            status=GAMLineItemStatus(getattr(data, "status", "DRAFT")),
            cost_type=GAMCostType(getattr(data, "costType", "CPM")),
            cost_per_unit=GAMMoney(
                currency_code=getattr(cost_data, "currencyCode", "USD") if cost_data else "USD",
                micro_amount=getattr(cost_data, "microAmount", 0) if cost_data else 0,
            ),
            primary_goal=GAMGoal(
                goal_type=GAMGoalType(
                    getattr(primary_goal, "goalType", "LIFETIME") if primary_goal else "LIFETIME"
                ),
                unit_type=GAMUnitType(
                    getattr(primary_goal, "unitType", "IMPRESSIONS")
                    if primary_goal
                    else "IMPRESSIONS"
                ),
                units=getattr(primary_goal, "units", -1) if primary_goal else -1,
            ),
            external_id=getattr(data, "externalId", None),
            notes=getattr(data, "notes", None),
        )

    def _to_soap_datetime(self, dt: datetime) -> dict[str, Any]:
        """Convert Python datetime to SOAP datetime format."""
        return {
            "date": {
                "year": dt.year,
                "month": dt.month,
                "day": dt.day,
            },
            "hour": dt.hour,
            "minute": dt.minute,
            "second": dt.second,
            "timeZoneId": "America/New_York",
        }

    # =========================================================================
    # Audience Segment Operations
    # =========================================================================

    def list_audience_segments(
        self,
        filter_statement: Optional[str] = None,
        limit: int = 500,
    ) -> list[GAMAudienceSegment]:
        """List audience segments.

        Args:
            filter_statement: Optional PQL filter
            limit: Maximum results to return

        Returns:
            List of audience segments
        """
        segment_service = self._get_service("AudienceSegmentService")

        statement = filter_statement or ""
        if statement and not statement.upper().startswith("WHERE"):
            statement = f"WHERE {statement}"
        statement += f" LIMIT {limit}"

        response = segment_service.getAudienceSegmentsByStatement({"query": statement})

        segments = []
        # SOAP returns ZEEP objects, use attribute access not .get()
        results = getattr(response, "results", None) or []
        for data in results:
            segment = self._parse_audience_segment(data)
            segments.append(segment)

        return segments

    def get_audience_segment(self, segment_id: int) -> GAMAudienceSegment:
        """Get a specific audience segment by ID.

        Args:
            segment_id: The segment ID

        Returns:
            The audience segment
        """
        segment_service = self._get_service("AudienceSegmentService")

        statement = f"WHERE id = {segment_id}"
        response = segment_service.getAudienceSegmentsByStatement({"query": statement})

        results = getattr(response, "results", None) or []
        if not results:
            raise ValueError(f"Audience segment {segment_id} not found")

        return self._parse_audience_segment(results[0])

    def create_audience_segment(
        self,
        name: str,
        description: Optional[str] = None,
        membership_expiration_days: int = 30,
    ) -> GAMAudienceSegment:
        """Create a first-party audience segment.

        Args:
            name: Segment name
            description: Optional description
            membership_expiration_days: Days until membership expires

        Returns:
            The created segment
        """
        segment_service = self._get_service("AudienceSegmentService")

        segment = {
            "xsi_type": "RuleBasedFirstPartyAudienceSegment",
            "name": name,
            "membershipExpirationDays": membership_expiration_days,
            "status": "INACTIVE",  # Created as inactive, needs activation
        }

        if description:
            segment["description"] = description

        result = segment_service.createAudienceSegments([segment])
        return self._parse_audience_segment(result[0])

    def activate_audience_segment(self, segment_id: int) -> bool:
        """Activate an audience segment for targeting.

        Args:
            segment_id: The segment ID

        Returns:
            True if successful
        """
        segment_service = self._get_service("AudienceSegmentService")

        action = {"xsi_type": "ActivateAudienceSegments"}
        statement = f"WHERE id = {segment_id}"

        result = segment_service.performAudienceSegmentAction(action, {"query": statement})

        return getattr(result, "numChanges", 0) > 0

    def _parse_audience_segment(self, data: Any) -> GAMAudienceSegment:
        """Parse SOAP response (ZEEP object) into GAMAudienceSegment model."""
        # SOAP returns ZEEP objects - use getattr for attribute access
        # Determine segment type from the object type name
        type_name = type(data).__name__
        if "RuleBased" in type_name:
            segment_type = GAMAudienceSegmentType.RULE_BASED
        elif "NonRuleBased" in type_name:
            segment_type = GAMAudienceSegmentType.NON_RULE_BASED
        elif "ThirdParty" in type_name:
            segment_type = GAMAudienceSegmentType.THIRD_PARTY
        else:
            segment_type = GAMAudienceSegmentType.THIRD_PARTY

        return GAMAudienceSegment(
            id=getattr(data, "id", 0),
            name=getattr(data, "name", ""),
            type=segment_type,
            status=GAMAudienceSegmentStatus(getattr(data, "status", "INACTIVE")),
            description=getattr(data, "description", None),
            size=getattr(data, "size", None),
            membership_expiration_days=getattr(data, "membershipExpirationDays", 30),
        )

    # =========================================================================
    # Ad Unit Operations
    # =========================================================================

    def list_ad_units(
        self,
        filter_statement: Optional[str] = None,
        limit: int = 100,
    ) -> list[GAMAdUnit]:
        """List ad units in the network.

        Args:
            filter_statement: Optional PQL filter
            limit: Maximum results to return

        Returns:
            List of ad units
        """
        inventory_service = self._get_service("InventoryService")

        statement = filter_statement or ""
        if statement and not statement.upper().startswith("WHERE"):
            statement = f"WHERE {statement}"
        statement += f" LIMIT {limit}"

        response = inventory_service.getAdUnitsByStatement({"query": statement})

        ad_units = []
        results = getattr(response, "results", None) or []
        for data in results:
            # Parse sizes if present
            sizes = []
            ad_unit_sizes = getattr(data, "adUnitSizes", None) or []
            for size_data in ad_unit_sizes:
                size = getattr(size_data, "size", None)
                if size:
                    sizes.append(
                        GAMAdUnitSize(
                            size=GAMSize(
                                width=getattr(size, "width", 0),
                                height=getattr(size, "height", 0),
                                is_aspect_ratio=getattr(size, "isAspectRatio", False),
                            ),
                            environment_type=getattr(size_data, "environmentType", "BROWSER"),
                        )
                    )

            ad_unit = GAMAdUnit(
                id=str(getattr(data, "id", "")),
                name=getattr(data, "name", ""),
                parent_id=str(getattr(data, "parentId", ""))
                if getattr(data, "parentId", None)
                else None,
                ad_unit_code=getattr(data, "adUnitCode", None),
                status=getattr(data, "status", "ACTIVE"),
                ad_unit_sizes=sizes,
            )
            ad_units.append(ad_unit)

        return ad_units

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_current_user(self) -> dict[str, Any]:
        """Get the current authenticated user info.

        Returns:
            User information dictionary
        """
        user_service = self._get_service("UserService")
        return user_service.getCurrentUser()

    # =========================================================================
    # Reporting — Read Operations
    # =========================================================================

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent orders in the network.

        Args:
            limit: Maximum number of orders to return

        Returns:
            List of dicts with id, name, status
        """
        from googleads import ad_manager

        order_service = self._get_service("OrderService")
        sb = ad_manager.StatementBuilder()
        sb.Limit(limit)
        result = order_service.getOrdersByStatement(sb.ToStatement())
        return [
            {
                "id": str(getattr(o, "id", "")),
                "name": getattr(o, "name", ""),
                "status": str(getattr(o, "status", "")),
            }
            for o in (getattr(result, "results", None) or [])
        ]

    def get_order_by_id(self, order_id: str) -> dict[str, Any]:
        """Fetch a single order by numeric ID.

        Args:
            order_id: GAM order ID

        Returns:
            Dict with id, name, status, advertiser_id, start_date, end_date
        """
        from googleads import ad_manager

        order_service = self._get_service("OrderService")
        sb = ad_manager.StatementBuilder()
        sb.Where("id = :id").WithBindVariable("id", int(order_id))
        sb.Limit(1)
        result = order_service.getOrdersByStatement(sb.ToStatement())
        orders = getattr(result, "results", None) or []
        if not orders:
            return {}
        o = orders[0]
        return {
            "id": str(getattr(o, "id", "")),
            "name": getattr(o, "name", ""),
            "status": str(getattr(o, "status", "")),
            "advertiser_id": str(getattr(o, "advertiserId", "")),
            "start_date": str(getattr(o, "startDateTime", "")),
            "end_date": str(getattr(o, "endDateTime", "")),
        }

    def list_line_items_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """List line items for a given order ID.

        Args:
            order_id: GAM order ID

        Returns:
            List of dicts with id, name, status, impressions_goal, cost_type
        """
        from googleads import ad_manager

        li_service = self._get_service("LineItemService")
        sb = ad_manager.StatementBuilder()
        sb.Where("orderId = :orderId").WithBindVariable("orderId", int(order_id))
        result = li_service.getLineItemsByStatement(sb.ToStatement())
        return [
            {
                "id": str(getattr(li, "id", "")),
                "name": getattr(li, "name", ""),
                "status": str(getattr(li, "status", "")),
                "impressions_goal": getattr(
                    getattr(li, "primaryGoal", None), "units", -1
                ),
                "cost_type": str(getattr(li, "costType", "CPM")),
            }
            for li in (getattr(result, "results", None) or [])
        ]

    def run_delivery_report(
        self,
        order_ids: list[str],
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Submit a delivery report job and return rows as dicts.

        Polls until the job completes (max ~60 s) then downloads CSV rows.

        Args:
            order_ids: List of GAM order IDs (numeric strings)
            days: Look-back window in days (default 30)

        Returns:
            List of row dicts with keys:
                order_id, order_name, line_item_id, line_item_name,
                impressions, clicks, revenue_usd
        """
        report_svc = self._get_service("ReportService")
        today = date.today()
        start = today - timedelta(days=days)

        report_job = {
            "reportQuery": {
                "dimensions": [
                    "ORDER_ID",
                    "ORDER_NAME",
                    "LINE_ITEM_ID",
                    "LINE_ITEM_NAME",
                ],
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {
                    "year": start.year,
                    "month": start.month,
                    "day": start.day,
                },
                "endDate": {
                    "year": today.year,
                    "month": today.month,
                    "day": today.day,
                },
            }
        }

        job = report_svc.runReportJob(report_job)
        job_id = job.id

        for _ in range(20):
            time.sleep(3)
            status = str(report_svc.getReportJobStatus(job_id))
            if status == "COMPLETED":
                break
            if status == "FAILED":
                return [{"error": f"Report job {job_id} failed"}]

        downloader = self._client.GetDataDownloader(version=self.api_version)
        buf = io.BytesIO()
        downloader.DownloadReportToFile(
            job_id, "CSV_EXCEL", buf, use_gzip_compression=False
        )
        buf.seek(0)
        lines = buf.read().decode("utf-8").splitlines()

        if len(lines) < 2:
            return []

        rows = []
        for line in lines[1:]:
            if line.startswith("Total"):
                continue
            parts = line.split(",")
            if len(parts) < 7:
                continue
            rows.append(
                {
                    "order_id": parts[0].strip(),
                    "order_name": parts[1].strip(),
                    "line_item_id": parts[2].strip(),
                    "line_item_name": parts[3].strip(),
                    "impressions": int(parts[4].strip() or 0),
                    "clicks": int(parts[5].strip() or 0),
                    "revenue_usd": float(parts[6].strip() or 0),
                }
            )
        return rows

    def get_delivery_report(
        self,
        order_ids: list[str],
        days: int = 30,
    ) -> dict[str, Any]:
        """Fetch order metadata + line items + delivery rows for given order IDs.

        Args:
            order_ids: List of GAM order IDs
            days: Look-back window in days

        Returns:
            {
                "orders": [{"order_id", "order_name", "status", "line_items": [...]}],
                "report_rows": [...],
                "summary": {"impressions", "clicks", "revenue_usd"}
            }
        """
        orders_out: list[dict[str, Any]] = []
        for oid in order_ids:
            entry: dict[str, Any] = {"order_id": oid}
            try:
                order = self.get_order_by_id(oid)
                entry.update(
                    {
                        "order_name": order.get("name", oid),
                        "status": order.get("status", "UNKNOWN"),
                    }
                )
            except Exception as e:
                entry.update({"order_name": oid, "status": "ERROR", "error": str(e)})
            try:
                entry["line_items"] = self.list_line_items_for_order(oid)
            except Exception as e:
                entry["line_items"] = []
                entry["line_items_error"] = str(e)
            orders_out.append(entry)

        report_rows: list[dict[str, Any]] = []
        try:
            report_rows = self.run_delivery_report(order_ids, days=days)
        except Exception as e:
            report_rows = [{"error": str(e)}]

        ok = [r for r in report_rows if "error" not in r]
        summary = {
            "impressions": sum(r.get("impressions", 0) for r in ok),
            "clicks": sum(r.get("clicks", 0) for r in ok),
            "revenue_usd": round(sum(r.get("revenue_usd", 0) for r in ok), 2),
        }

        return {
            "orders": orders_out,
            "report_rows": report_rows,
            "summary": summary,
        }
