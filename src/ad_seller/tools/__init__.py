# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CrewAI tools for the Ad Seller System."""

from .pricing import PricingLookupTool, FloorPriceCheckTool
from .availability import AvailsCheckTool, ForecastTool
from .proposal import ProposalValidationTool, CounterProposalTool
from .audience import (
    AudienceValidationTool,
    AudienceCapabilityTool,
    CoverageCalculatorTool,
)
from .deal_jockey import (
    GetSupplyChainTool,
    GetDealPerformanceTool,
    BulkDealOperationsTool,
)
from .gam import (
    # Inventory tools
    ListAdUnitsTool,
    GetGAMPricingTool,
    SyncGAMInventoryTool,
    # Booking tools (reserved deals)
    CreateGAMOrderTool,
    CreateGAMLineItemTool,
    BookDealInGAMTool,
    # Private auction tools (non-reserved deals)
    ListPrivateAuctionsTool,
    CreatePrivateAuctionDealTool,
    # Audience tools
    ListAudienceSegmentsTool,
    SyncGAMAudiencesTool,
)

__all__ = [
    # Pricing tools
    "PricingLookupTool",
    "FloorPriceCheckTool",
    # Availability tools
    "AvailsCheckTool",
    "ForecastTool",
    # Proposal tools
    "ProposalValidationTool",
    "CounterProposalTool",
    # Audience tools
    "AudienceValidationTool",
    "AudienceCapabilityTool",
    "CoverageCalculatorTool",
    # GAM Inventory tools
    "ListAdUnitsTool",
    "GetGAMPricingTool",
    "SyncGAMInventoryTool",
    # GAM Booking tools (Programmatic Guaranteed, Preferred Deal)
    "CreateGAMOrderTool",
    "CreateGAMLineItemTool",
    "BookDealInGAMTool",
    # GAM Private Auction tools
    "ListPrivateAuctionsTool",
    "CreatePrivateAuctionDealTool",
    # GAM Audience tools (with IAB Audience Taxonomy 1.1 support)
    "ListAudienceSegmentsTool",
    "SyncGAMAudiencesTool",
    # Deal Jockey tools
    "GetSupplyChainTool",
    "GetDealPerformanceTool",
    "BulkDealOperationsTool",
]
