# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Protocol clients for OpenDirect 2.1, A2A, GAM, SSP, deal-sync, and ad server abstraction."""

from .a2a_client import A2AClient, A2AResponse
from .ad_server_base import (
    AdServerAudienceSegment,
    AdServerClient,
    AdServerDeal,
    AdServerInventoryItem,
    AdServerLineItem,
    AdServerOrder,
    AdServerType,
    BookingResult,
    get_ad_server_client,
)
from .csv_adapter import CSVAdServerClient
from .deal_sync_base import DealSyncClient, DealSyncProvider, DealSyncRegistry
from .deal_sync_factory import build_deal_sync_registry
from .deals_api_mcp_client import DealsAPIMCPClient
from .freewheel_adapter import FreeWheelAdServerClient
from .gam_rest_client import GAMRestClient
from .gam_soap_client import GAMSoapClient
from .opendirect21_client import OpenDirect21Client
from .ssp_base import SSPClient, SSPDeal, SSPDealCreateRequest, SSPRegistry, SSPType
from .ssp_factory import build_ssp_registry
from .ssp_index_exchange import IndexExchangeSSPClient
from .ssp_mcp_client import MCPSSPClient
from .ssp_rest_client import RESTSSPClient
from .ucp_client import UCPClient, UCPExchangeResult
from .unified_client import Protocol, UnifiedClient, UnifiedResult

__all__ = [
    "Protocol",
    "UnifiedClient",
    "UnifiedResult",
    "OpenDirect21Client",
    "A2AClient",
    "A2AResponse",
    # UCP client for audience validation
    "UCPClient",
    "UCPExchangeResult",
    # GAM clients (direct access)
    "GAMRestClient",
    "GAMSoapClient",
    # Ad server abstraction
    "AdServerClient",
    "AdServerType",
    "AdServerOrder",
    "AdServerLineItem",
    "AdServerDeal",
    "AdServerInventoryItem",
    "AdServerAudienceSegment",
    "BookingResult",
    "get_ad_server_client",
    # CSV adapter
    "CSVAdServerClient",
    # FreeWheel adapter
    "FreeWheelAdServerClient",
    # SSP abstraction
    "SSPClient",
    "SSPRegistry",
    "SSPType",
    "SSPDeal",
    "SSPDealCreateRequest",
    "MCPSSPClient",
    "RESTSSPClient",
    "build_ssp_registry",
    "IndexExchangeSSPClient",
    # Deal-sync abstraction
    "DealSyncClient",
    "DealSyncProvider",
    "DealSyncRegistry",
    "DealsAPIMCPClient",
    "build_deal_sync_registry",
]
