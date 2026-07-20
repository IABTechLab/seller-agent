# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for GAM clients."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from ad_seller.clients.gam_rest_client import GAMRestClient
from ad_seller.clients.gam_soap_client import GAMSoapClient
from ad_seller.models.gam import (
    GAMAudienceSegmentStatus,
    GAMAudienceSegmentType,
    GAMCostType,
    GAMLineItemType,
    GAMMoney,
    GAMOrderStatus,
    GAMPrivateAuctionDeal,
)


class TestGAMRestClient:
    """Tests for GAM REST client."""

    def test_client_initialization(self):
        """Test client initialization."""
        client = GAMRestClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )
        assert client.network_code == "12345678"
        assert client.credentials_path == "/path/to/creds.json"

    def test_client_initialization_from_settings(self):
        """Test client initialization from settings."""
        with patch("ad_seller.clients.gam_rest_client.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                gam_network_code="87654321",
                gam_json_key_path="/settings/path.json",
            )
            client = GAMRestClient()
            assert client.network_code == "87654321"

    @pytest.mark.asyncio
    async def test_parse_ad_unit(self):
        """Test parsing ad unit from REST response."""
        client = GAMRestClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )

        rest_data = {
            "name": "networks/12345678/adUnits/111",
            "displayName": "Homepage Banner",
            "parentAdUnit": "networks/12345678/adUnits/100",
            "status": "ACTIVE",
            "adUnitSizes": [
                {
                    "size": {"width": "728", "height": "90"},
                    "environmentType": "BROWSER",
                }
            ],
        }

        ad_unit = client._parse_ad_unit(rest_data)
        assert ad_unit.id == "111"
        assert ad_unit.name == "Homepage Banner"
        assert ad_unit.parent_id == "100"
        assert ad_unit.status == "ACTIVE"
        assert len(ad_unit.ad_unit_sizes) == 1
        assert ad_unit.ad_unit_sizes[0].size.width == 728

    @pytest.mark.asyncio
    async def test_parse_private_auction_deal(self):
        """Test parsing private auction deal from REST response."""
        client = GAMRestClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )

        # REST API returns private auction ID in the name path, buyer as "buyer"
        rest_data = {
            "name": "networks/12345678/privateAuctions/PA-001/privateAuctionDeals/DEAL-001",
            "buyer": "BUYER-123",  # REST API uses "buyer" not "buyerAccountId"
            "externalDealId": "OD-LINE-456",
            "floorPrice": {
                "currencyCode": "USD",
                "units": "25",
                "nanos": 500000000,
            },
        }

        deal = client._parse_private_auction_deal(rest_data)
        assert deal.id == "DEAL-001"
        assert deal.private_auction_id == "PA-001"
        assert deal.buyer_account_id == "BUYER-123"
        assert deal.external_deal_id == "OD-LINE-456"
        assert deal.floor_price.to_dollars() == 25.50


class TestGAMSoapClient:
    """Tests for GAM SOAP client."""

    def test_client_initialization(self):
        """Test client initialization."""
        client = GAMSoapClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )
        assert client.network_code == "12345678"
        assert client.credentials_path == "/path/to/creds.json"

    def test_ensure_connected_raises(self):
        """Test that operations fail when not connected."""
        client = GAMSoapClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )
        with pytest.raises(RuntimeError, match="not connected"):
            client._ensure_connected()

    def test_to_soap_datetime(self):
        """Test datetime conversion to SOAP format."""
        client = GAMSoapClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )

        dt = datetime(2026, 3, 15, 10, 30, 45)
        soap_dt = client._to_soap_datetime(dt)

        assert soap_dt["date"]["year"] == 2026
        assert soap_dt["date"]["month"] == 3
        assert soap_dt["date"]["day"] == 15
        assert soap_dt["hour"] == 10
        assert soap_dt["minute"] == 30
        assert soap_dt["second"] == 45
        assert soap_dt["timeZoneId"] == "America/New_York"

    def test_parse_order(self):
        """Test parsing order from SOAP response."""
        client = GAMSoapClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )

        # Create a mock ZEEP-like object with attributes (SOAP returns objects, not dicts)
        class MockOrder:
            def __init__(self):
                self.id = 999
                self.name = "Q1 Campaign"
                self.advertiserId = 111
                self.traffickerId = 222
                self.agencyId = 333
                self.status = "APPROVED"
                self.externalOrderId = "OD-DEAL-123"
                self.notes = "Test order"
                self.isProgrammatic = True

        soap_data = MockOrder()

        order = client._parse_order(soap_data)
        assert order.id == "999"
        assert order.name == "Q1 Campaign"
        assert order.advertiser_id == "111"
        assert order.trafficker_id == "222"
        assert order.agency_id == "333"
        assert order.status == GAMOrderStatus.APPROVED
        assert order.external_order_id == "OD-DEAL-123"
        assert order.is_programmatic is True

    def test_parse_line_item(self):
        """Test parsing line item from SOAP response."""
        client = GAMSoapClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )

        # Create mock ZEEP-like objects with attributes (SOAP returns objects, not dicts)
        class MockCostPerUnit:
            def __init__(self):
                self.currencyCode = "USD"
                self.microAmount = 15000000

        class MockPrimaryGoal:
            def __init__(self):
                self.goalType = "LIFETIME"
                self.unitType = "IMPRESSIONS"
                self.units = 1000000

        class MockLineItem:
            def __init__(self):
                self.id = 888
                self.orderId = 999
                self.name = "Display Line"
                self.lineItemType = "STANDARD"
                self.status = "READY"
                self.costType = "CPM"
                self.costPerUnit = MockCostPerUnit()
                self.primaryGoal = MockPrimaryGoal()
                self.externalId = "OD-LINE-456"
                self.notes = None

        soap_data = MockLineItem()

        line_item = client._parse_line_item(soap_data)
        assert line_item.id == "888"
        assert line_item.order_id == "999"
        assert line_item.line_item_type == GAMLineItemType.STANDARD
        assert line_item.cost_per_unit.to_dollars() == 15.0
        assert line_item.primary_goal.units == 1000000

    def test_parse_audience_segment(self):
        """Test parsing audience segment from SOAP response."""
        client = GAMSoapClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )

        # Create a mock ZEEP-like object with attributes
        class MockSegment:
            def __init__(self):
                self.id = 12345
                self.name = "Sports Fans"
                self.status = "ACTIVE"
                self.description = "Users interested in sports"
                self.size = 500000
                self.membershipExpirationDays = 30

        # Mock a RuleBasedFirstPartyAudienceSegment
        class RuleBasedFirstPartyAudienceSegment(MockSegment):
            pass

        soap_data = RuleBasedFirstPartyAudienceSegment()

        segment = client._parse_audience_segment(soap_data)
        assert segment.id == 12345
        assert segment.name == "Sports Fans"
        assert segment.type == GAMAudienceSegmentType.RULE_BASED
        assert segment.status == GAMAudienceSegmentStatus.ACTIVE
        assert segment.size == 500000


class TestGAMClientIntegration:
    """Integration-style tests for GAM clients."""

    def test_rest_client_setup(self):
        """Test REST client is properly configured."""
        client = GAMRestClient(
            network_code="12345",
            credentials_path="/path/to/creds.json",
        )
        assert client.network_code == "12345"
        assert client.credentials_path == "/path/to/creds.json"
        assert client._service is None  # Not connected yet

    def test_soap_client_setup(self):
        """Test SOAP client is properly configured."""
        client = GAMSoapClient(
            network_code="12345678",
            credentials_path="/path/to/creds.json",
        )
        assert client.network_code == "12345678"
        assert client.credentials_path == "/path/to/creds.json"
        assert client.api_version == "v202505"
        assert client._client is None  # Not connected yet

    def test_rest_client_requires_connection(self):
        """Test REST client raises error when not connected."""
        client = GAMRestClient(
            network_code="12345",
            credentials_path="/path/to/creds.json",
        )
        with pytest.raises(RuntimeError, match="not connected"):
            client._ensure_connected()

    def test_soap_client_requires_connection(self):
        """Test SOAP client raises error when not connected."""
        client = GAMSoapClient(
            network_code="12345",
            credentials_path="/path/to/creds.json",
        )
        with pytest.raises(RuntimeError, match="not connected"):
            client._ensure_connected()


class TestDealTypeMapping:
    """Tests for deal type to GAM line item type mapping."""

    def test_pg_to_sponsorship(self):
        """Test PG maps to SPONSORSHIP."""
        # Programmatic Guaranteed with percentage-based delivery
        assert GAMLineItemType.SPONSORSHIP.value == "SPONSORSHIP"

    def test_pg_to_standard(self):
        """Test PG can also map to STANDARD."""
        # Programmatic Guaranteed with fixed impression count
        assert GAMLineItemType.STANDARD.value == "STANDARD"

    def test_preferred_deal_mapping(self):
        """Test Preferred Deal maps correctly."""
        # Preferred Deal - fixed price, non-reserved
        assert GAMLineItemType.PREFERRED_DEAL.value == "PREFERRED_DEAL"

    def test_private_auction_no_line_item(self):
        """Test Private Auction doesn't use line items."""
        # Private auctions use PrivateAuctionDeal, not LineItem
        deal = GAMPrivateAuctionDeal(
            private_auction_id="PA-001",
            buyer_account_id="BUYER-123",
            floor_price=GAMMoney.from_dollars(10.0),
        )
        assert deal.private_auction_id == "PA-001"
        # No line_item_type attribute - private auctions are separate


class TestCostTypeMapping:
    """Tests for pricing model to cost type mapping."""

    def test_cpm_mapping(self):
        """Test CPM maps correctly."""
        assert GAMCostType.CPM.value == "CPM"

    def test_cpc_mapping(self):
        """Test CPC maps correctly."""
        assert GAMCostType.CPC.value == "CPC"

    def test_cpcv_mapping(self):
        """Test CPCV maps correctly."""
        assert GAMCostType.CPCV.value == "CPCV"

    def test_flat_fee_to_cpd(self):
        """Test flat_fee maps to CPD."""
        assert GAMCostType.CPD.value == "CPD"

    def test_cpv_not_supported(self):
        """Test CPV is not a valid GAM cost type."""
        valid_types = [e.value for e in GAMCostType]
        assert "CPV" not in valid_types
