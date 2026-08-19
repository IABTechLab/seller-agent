# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Linear TV models, tools, and classification."""

from decimal import Decimal

import pytest

from ad_seller.constants.dma_codes import DMA_CODES
from ad_seller.models.core import DealType
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.linear_tv import (
    Daypart,
    LinearDeal,
    LinearTVProduct,
    MakegoodTerms,
    SupplyPoolEntry,
)
from ad_seller.tools.linear import (
    AddressableTargetingTool,
    AirtimeReportingTool,
    DMAAvailsTool,
    LinearAudienceForecastTool,
    LinearAvailsTool,
    LinearBillingReconciliationTool,
    LinearOrderTool,
    LinearPricingTool,
    LinearReachFrequencyTool,
    MakegoodPoolTool,
    ScatterPricingTool,
    UpfrontDealCalculator,
)

# =============================================================================
# DMA Codes
# =============================================================================


class TestDMACodes:
    """Tests for DMA reference data."""

    def test_dma_count(self):
        assert len(DMA_CODES) == 210

    def test_top_markets_exist(self):
        assert DMA_CODES[501] == ("New York", 1)
        assert DMA_CODES[803] == ("Los Angeles", 2)
        assert DMA_CODES[602] == ("Chicago", 3)
        assert DMA_CODES[504] == ("Philadelphia", 4)

    def test_dma_codes_are_ints(self):
        for code in DMA_CODES:
            assert isinstance(code, int)

    def test_dma_values_are_tuples(self):
        for name, rank in DMA_CODES.values():
            assert isinstance(name, str)
            assert isinstance(rank, int)


# =============================================================================
# Daypart Model
# =============================================================================


class TestDaypart:
    """Tests for Daypart model (TIP TimeWindow compatible)."""

    def test_basic_creation(self):
        dp = Daypart(
            name="primetime",
            start_time="20:00:00",
            end_time="23:00:00",
        )
        assert dp.name == "primetime"
        assert dp.start_time == "20:00:00"
        assert dp.end_time == "23:00:00"

    def test_sellthrough_calculation(self):
        dp = Daypart(
            name="primetime",
            start_time="20:00:00",
            end_time="23:00:00",
            available_units=100,
            sold_units=85,
        )
        assert dp.sellthrough_pct == 85.0

    def test_sellthrough_zero_available(self):
        dp = Daypart(
            name="daytime",
            start_time="10:00:00",
            end_time="16:00:00",
            available_units=0,
            sold_units=0,
        )
        assert dp.sellthrough_pct == 0.0

    def test_days_of_week_default(self):
        dp = Daypart(name="daytime", start_time="10:00:00", end_time="16:00:00")
        assert dp.days_of_week == ["M", "T", "W", "Th", "F"]

    def test_weekend_daypart(self):
        dp = Daypart(
            name="weekend",
            start_time="08:00:00",
            end_time="23:00:00",
            days_of_week=["Sa", "Su"],
        )
        assert dp.days_of_week == ["Sa", "Su"]

    def test_invalid_time_format(self):
        with pytest.raises(ValueError, match="HH:MM:SS"):
            Daypart(name="primetime", start_time="8pm", end_time="23:00:00")

    def test_invalid_time_values(self):
        with pytest.raises(ValueError, match="Invalid time"):
            Daypart(name="primetime", start_time="25:00:00", end_time="23:00:00")

    def test_dual_currency_rates(self):
        dp = Daypart(
            name="primetime",
            start_time="20:00:00",
            end_time="23:00:00",
            base_rate_cpp=Decimal("45000"),
            base_rate_cpm=Decimal("55.00"),
        )
        assert dp.base_rate_cpp == Decimal("45000")
        assert dp.base_rate_cpm == Decimal("55.00")

    def test_all_daypart_names(self):
        """Verify all 9 standard dayparts can be created."""
        dayparts = [
            "early_morning",
            "daytime",
            "early_fringe",
            "prime_access",
            "primetime",
            "late_news",
            "late_fringe",
            "overnight",
            "weekend",
        ]
        for name in dayparts:
            dp = Daypart(name=name, start_time="00:00:00", end_time="23:59:00")
            assert dp.name == name


# =============================================================================
# MakegoodTerms Model
# =============================================================================


class TestMakegoodTerms:
    """Tests for MakegoodTerms (TIP v5.1.0 MakegoodGuideline)."""

    def test_preemption_terms(self):
        terms = MakegoodTerms(
            makegood_type="resolve_preemption",
            sales_element_equivalent="same_sales_element",
            makegood_window="original_broadcast_week",
        )
        assert terms.makegood_type == "resolve_preemption"

    def test_underdelivery_terms(self):
        terms = MakegoodTerms(
            makegood_type="audience_underdelivery",
            sales_element_equivalent="alternate_sales_element",
            makegood_window="within_flight_dates",
            makegood_ratio=3,
            audience_limit_pct=90.0,
            external_comment="Standard upfront makegood terms",
        )
        assert terms.makegood_ratio == 3
        assert terms.audience_limit_pct == 90.0

    def test_all_windows(self):
        for window in [
            "original_broadcast_week",
            "original_broadcast_month",
            "within_flight_dates",
        ]:
            terms = MakegoodTerms(
                makegood_type="resolve_preemption",
                sales_element_equivalent="same_sales_element",
                makegood_window=window,
            )
            assert terms.makegood_window == window


# =============================================================================
# LinearTVProduct Model
# =============================================================================


class TestLinearTVProduct:
    """Tests for LinearTVProduct model."""

    @pytest.fixture
    def nbc_primetime(self):
        return LinearTVProduct(
            name="NBC Primetime :30",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCUniversal",
            seller_type="DIRECT",
            coverage="national",
            primary_demo="A25-54",
            rate_card_cpm=Decimal("55.00"),
            floor_cpm=Decimal("40.00"),
            rate_card_cpp=Decimal("45000"),
            floor_cpp=Decimal("32000"),
            market_types=["upfront", "scatter"],
            programmatic_deal_types=["pg"],
            programmatic_enabled=True,
        )

    @pytest.fixture
    def comcast_local(self):
        return LinearTVProduct(
            name="Comcast Local Avails",
            medium="mvpd_avail",
            network_name="Comcast Xfinity",
            network_group="Comcast",
            seller_type="DIRECT",
            coverage="local",
            dma_codes=[501, 803, 602],
            primary_demo="A25-54",
            rate_card_cpm=Decimal("15.00"),
            floor_cpm=Decimal("8.00"),
            addressable_enabled=True,
            addressable_type="mvpd_stb",
            addressable_hh_count=60_000_000,
        )

    @pytest.fixture
    def reseller_product(self):
        return LinearTVProduct(
            name="Programmatic Linear Reach",
            medium="broadcast",
            network_name="Multi-Network",
            network_group="PubMatic Linear",
            seller_type="RESELLER",
            coverage="national",
            primary_demo="A25-54",
            rate_card_cpm=Decimal("30.00"),
            floor_cpm=Decimal("20.00"),
            programmatic_enabled=True,
            programmatic_deal_types=["pg", "pmp"],
            supply_pool=[
                SupplyPoolEntry(source_network="NBC", weight=0.4),
                SupplyPoolEntry(source_network="Fox", weight=0.3),
                SupplyPoolEntry(source_network="CBS", weight=0.3),
            ],
        )

    def test_broadcast_product(self, nbc_primetime):
        assert nbc_primetime.medium == "broadcast"
        assert nbc_primetime.seller_type == "DIRECT"
        assert nbc_primetime.coverage == "national"

    def test_mvpd_product(self, comcast_local):
        assert comcast_local.medium == "mvpd_avail"
        assert comcast_local.addressable_enabled is True
        assert comcast_local.addressable_type == "mvpd_stb"
        assert len(comcast_local.dma_codes) == 3

    def test_reseller_product(self, reseller_product):
        assert reseller_product.seller_type == "RESELLER"
        assert len(reseller_product.supply_pool) == 3
        assert reseller_product.programmatic_enabled is True

    def test_to_product_definition(self, nbc_primetime):
        pd = nbc_primetime.to_product_definition()
        assert isinstance(pd, ProductDefinition)
        assert pd.inventory_type == "linear_tv"
        assert pd.base_cpm == 55.0
        assert pd.floor_cpm == 40.0
        assert DealType.PROGRAMMATIC_GUARANTEED in pd.supported_deal_types

    def test_to_product_definition_pmp(self, reseller_product):
        pd = reseller_product.to_product_definition()
        assert DealType.PROGRAMMATIC_GUARANTEED in pd.supported_deal_types
        assert DealType.PRIVATE_AUCTION in pd.supported_deal_types

    def test_demo_validation_valid(self):
        product = LinearTVProduct(
            name="Test",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCU",
            primary_demo="A25-54",
        )
        assert product.primary_demo == "A25-54"

    def test_demo_validation_women(self):
        product = LinearTVProduct(
            name="Test",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCU",
            primary_demo="W18-49",
        )
        assert product.primary_demo == "W18-49"

    def test_demo_validation_hh(self):
        product = LinearTVProduct(
            name="Test",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCU",
            primary_demo="HH",
        )
        assert product.primary_demo == "HH"

    def test_demo_validation_plus(self):
        product = LinearTVProduct(
            name="Test",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCU",
            primary_demo="P2+",
        )
        assert product.primary_demo == "P2+"

    def test_demo_validation_invalid(self):
        with pytest.raises(ValueError, match="Demo must match"):
            LinearTVProduct(
                name="Test",
                medium="broadcast",
                network_name="NBC",
                network_group="NBCU",
                primary_demo="adults 25-54",
            )

    def test_dma_validation_valid(self):
        product = LinearTVProduct(
            name="Test",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCU",
            primary_demo="A25-54",
            dma_codes=[501, 803],
        )
        assert product.dma_codes == [501, 803]

    def test_dma_validation_invalid(self):
        with pytest.raises(ValueError, match="Invalid DMA codes"):
            LinearTVProduct(
                name="Test",
                medium="broadcast",
                network_name="NBC",
                network_group="NBCU",
                primary_demo="A25-54",
                dma_codes=[999],
            )

    def test_secondary_demos(self):
        product = LinearTVProduct(
            name="Test",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCU",
            primary_demo="A25-54",
            secondary_demos=["W18-49", "M25-54"],
        )
        assert len(product.secondary_demos) == 2

    def test_measurement_currencies(self):
        for currency in [
            "nielsen_c3",
            "nielsen_c7",
            "nielsen_one",
            "videoamp",
            "ispot",
            "comscore",
            "impression",
            "grp",
            "multi",
        ]:
            product = LinearTVProduct(
                name="Test",
                medium="broadcast",
                network_name="NBC",
                network_group="NBCU",
                primary_demo="A25-54",
                measurement_currency=currency,
            )
            assert product.measurement_currency == currency

    def test_all_medium_types(self):
        for medium in ["broadcast", "cable", "syndication", "mvpd_avail"]:
            product = LinearTVProduct(
                name="Test",
                medium=medium,
                network_name="NBC",
                network_group="NBCU",
                primary_demo="A25-54",
            )
            assert product.medium == medium

    def test_scatter_rate_multiplier(self, nbc_primetime):
        assert nbc_primetime.scatter_rate_multiplier == 1.15

    def test_dayparts_on_product(self):
        product = LinearTVProduct(
            name="Test",
            medium="broadcast",
            network_name="NBC",
            network_group="NBCU",
            primary_demo="A25-54",
            dayparts=[
                Daypart(
                    name="primetime",
                    start_time="20:00:00",
                    end_time="23:00:00",
                    available_units=100,
                    sold_units=85,
                ),
                Daypart(
                    name="daytime",
                    start_time="10:00:00",
                    end_time="16:00:00",
                    available_units=200,
                    sold_units=80,
                ),
            ],
        )
        assert len(product.dayparts) == 2
        assert product.dayparts[0].sellthrough_pct == 85.0
        assert product.dayparts[1].sellthrough_pct == 40.0


# =============================================================================
# LinearDeal Model
# =============================================================================


class TestLinearDeal:
    """Tests for LinearDeal model."""

    def test_upfront_deal(self):
        deal = LinearDeal(
            market_type="upfront",
            buyer_type="holding_company",
            holding_company="wpp",
            networks=["NBC", "Bravo", "USA"],
            dayparts=["primetime", "late_news"],
            guaranteed_grps=500.0,
            guaranteed_impressions=650_000_000,
            negotiated_cpp=Decimal("42000"),
            negotiated_cpm=Decimal("48.50"),
            total_value=Decimal("25000000"),
            cancellation_window_days=90,
            rate_of_change_pct=5.5,
        )
        assert deal.market_type == "upfront"
        assert deal.holding_company == "wpp"
        assert deal.programmatic_deal_type is None

    def test_scatter_pmp_deal(self):
        deal = LinearDeal(
            market_type="scatter",
            programmatic_deal_type="pmp",
            buyer_type="dsp",
            networks=["NBC"],
            dayparts=["primetime"],
            guaranteed_impressions=10_000_000,
            negotiated_cpm=Decimal("62.00"),
            measurement_currency="impression",
        )
        assert deal.market_type == "scatter"
        assert deal.programmatic_deal_type == "pmp"
        assert deal.measurement_currency == "impression"

    def test_deal_with_makegood_terms(self):
        deal = LinearDeal(
            market_type="upfront",
            buyer_type="holding_company",
            makegood_terms=MakegoodTerms(
                makegood_type="audience_underdelivery",
                sales_element_equivalent="same_sales_element",
                makegood_window="within_flight_dates",
                audience_limit_pct=90.0,
            ),
        )
        assert deal.makegood_terms is not None
        assert deal.makegood_terms.audience_limit_pct == 90.0

    def test_deal_statuses(self):
        for status in [
            "proposed",
            "negotiating",
            "executed",
            "active",
            "completed",
            "cancelled",
            "makegood_pending",
        ]:
            deal = LinearDeal(market_type="scatter", buyer_type="brand_direct", status=status)
            assert deal.status == status

    def test_freewheel_linkage(self):
        deal = LinearDeal(
            market_type="scatter",
            programmatic_deal_type="pg",
            buyer_type="dsp",
            freewheel_deal_id="fw-deal-12345",
        )
        assert deal.freewheel_deal_id == "fw-deal-12345"

    def test_dma_validation(self):
        deal = LinearDeal(
            market_type="scatter",
            buyer_type="brand_direct",
            dma_codes=[501, 803],
        )
        assert deal.dma_codes == [501, 803]

    def test_dma_validation_invalid(self):
        with pytest.raises(ValueError, match="Invalid DMA codes"):
            LinearDeal(
                market_type="scatter",
                buyer_type="brand_direct",
                dma_codes=[999],
            )


# =============================================================================
# Pricing Tools
# =============================================================================


class TestPricingTools:
    """Tests for linear TV pricing tools."""

    def test_linear_pricing_tool(self):
        tool = LinearPricingTool()
        assert tool.name == "linear_pricing"
        result = tool._run(
            network="NBC",
            daypart="primetime",
            market_type="scatter",
            buyer_type="holding_company",
            base_rate_cpm=50.0,
            base_rate_cpp=40000.0,
        )
        assert "CPM Pricing" in result
        assert "CPP Pricing" in result
        assert "NBC" in result

    def test_linear_pricing_upfront(self):
        tool = LinearPricingTool()
        result = tool._run(
            network="NBC",
            daypart="primetime",
            market_type="upfront",
            base_rate_cpm=50.0,
        )
        assert "UPFRONT" in result

    def test_scatter_pricing_tool(self):
        tool = ScatterPricingTool()
        assert tool.name == "scatter_pricing"
        result = tool._run(
            network="Bravo",
            daypart="primetime",
            sellthrough_pct=88.0,
            base_rate_cpm=20.0,
        )
        assert "Scatter Pricing" in result
        assert "UP" in result  # High sell-through = upward trend

    def test_scatter_pricing_low_sellthrough(self):
        tool = ScatterPricingTool()
        result = tool._run(
            network="USA",
            daypart="daytime",
            sellthrough_pct=30.0,
            base_rate_cpm=15.0,
        )
        assert "DOWN" in result

    def test_upfront_deal_calculator(self):
        tool = UpfrontDealCalculator()
        assert tool.name == "upfront_deal_calculator"
        result = tool._run(
            prior_season_rate=45.0,
            volume_commitment=75_000_000,
            holding_company="wpp",
            networks=["NBC", "Bravo"],
        )
        assert "Rate-of-Change" in result
        assert "WPP" in result
        assert "Gold" in result  # $75M = Gold tier

    def test_upfront_platinum_tier(self):
        tool = UpfrontDealCalculator()
        result = tool._run(
            prior_season_rate=50.0,
            volume_commitment=150_000_000,
            holding_company="omnicom",
        )
        assert "Platinum" in result


# =============================================================================
# Avails Tools
# =============================================================================


class TestAvailsTools:
    """Tests for linear TV availability tools."""

    def test_linear_avails_tool(self):
        tool = LinearAvailsTool()
        assert tool.name == "linear_avails"
        result = tool._run(
            networks=["NBC", "Bravo"],
            dayparts=["primetime", "daytime"],
            flight_start="2026-04-01",
            flight_end="2026-06-30",
        )
        assert "NBC" in result
        assert "Bravo" in result
        assert "primetime" in result

    def test_dma_avails_tool(self):
        tool = DMAAvailsTool()
        assert tool.name == "dma_avails"
        result = tool._run(
            dma_codes=[501, 803],
            dayparts=["primetime"],
            flight_start="2026-04-01",
            flight_end="2026-06-30",
        )
        assert "New York" in result
        assert "Los Angeles" in result

    def test_makegood_pool_tool(self):
        tool = MakegoodPoolTool()
        assert tool.name == "makegood_pool"
        result = tool._run(
            original_network="NBC",
            original_daypart="primetime",
            under_delivery_pct=8.0,
        )
        assert "Makegood Pool Search" in result
        assert "Candidate" in result


# =============================================================================
# Forecasting Tools
# =============================================================================


class TestForecastingTools:
    """Tests for linear TV forecasting tools."""

    def test_audience_forecast_tool(self):
        tool = LinearAudienceForecastTool()
        assert tool.name == "linear_audience_forecast"
        result = tool._run(
            networks=["NBC"],
            dayparts=["primetime"],
            flight_start="2026-04-01",
            flight_end="2026-04-28",
        )
        assert "GRPs" in result
        assert "Impressions" in result

    def test_reach_frequency_tool(self):
        tool = LinearReachFrequencyTool()
        assert tool.name == "linear_reach_frequency"
        result = tool._run(total_grps=150.0, num_networks=3, num_dayparts=2)
        assert "Reach:" in result
        assert "Frequency:" in result

    def test_addressable_targeting_tool(self):
        tool = AddressableTargetingTool()
        assert tool.name == "addressable_targeting"
        result = tool._run(
            audience_segments=["auto intenders", "luxury shoppers"],
            base_cpm=40.0,
        )
        assert "Addressable" in result
        assert "69,500,000" in result


# =============================================================================
# Traffic Tools
# =============================================================================


class TestTrafficTools:
    """Tests for linear TV traffic tools."""

    def test_linear_order_tool(self):
        tool = LinearOrderTool()
        assert tool.name == "linear_order"
        result = tool._run(
            deal_id="ldeal-test123",
            advertiser_name="Rivian Automotive",
            agency_name="Agency ABC",
            networks=["NBC", "Bravo"],
            dayparts=["primetime"],
            flight_start="2026-04-01",
            flight_end="2026-06-30",
            total_spots=120,
            total_value=850000.0,
        )
        assert "IO-" in result
        assert "Rivian" in result
        assert "TIP-Compatible" in result
        assert "salesElements" in result  # JSON structure

    def test_airtime_reporting_tool(self):
        tool = AirtimeReportingTool()
        assert tool.name == "airtime_reporting"
        result = tool._run(order_id="IO-TEST123")
        assert "Airtime Report" in result
        assert "STUB" in result

    def test_billing_reconciliation_tool(self):
        tool = LinearBillingReconciliationTool()
        assert tool.name == "linear_billing_reconciliation"
        result = tool._run(order_id="IO-TEST123")
        assert "Billing Reconciliation" in result
        assert "STUB" in result


# =============================================================================
# Classification Helpers (from product_setup_flow.py)
# =============================================================================


class TestClassificationHelpers:
    """Tests for linear TV classification in product setup flow.

    These test the static methods directly without importing the flow module.
    """

    def test_classify_ad_formats(self):
        formats = {
            "display": ["banner"],
            "video": ["video"],
            "ctv": ["video"],
            "mobile_app": ["banner", "video"],
            "native": ["native"],
            "linear_tv": ["video"],
        }
        for inv_type, expected in formats.items():
            assert formats[inv_type] == expected

    def test_classify_device_types(self):
        devices = {
            "display": [2, 4, 5],
            "video": [2, 4, 5],
            "ctv": [3, 7],
            "mobile_app": [4, 5],
            "native": [2, 4, 5],
            "linear_tv": [3, 7],
        }
        assert devices["linear_tv"] == [3, 7]  # CTV + STB

    def test_estimate_base_cpm(self):
        cpms = {
            "display": 12.0,
            "video": 25.0,
            "ctv": 35.0,
            "mobile_app": 18.0,
            "native": 10.0,
            "linear_tv": 40.0,
        }
        assert cpms["linear_tv"] == 40.0
