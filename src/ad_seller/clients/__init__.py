# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Protocol clients for OpenDirect 2.1, A2A, GAM, SSP, and ad server abstraction."""

from .unified_client import Protocol, UnifiedClient, UnifiedResult
from .opendirect21_client import OpenDirect21Client
from .a2a_client import A2AClient, A2AResponse
from .ucp_client import UCPClient, UCPExchangeResult
from .gam_rest_client import GAMRestClient
from .gam_soap_client import GAMSoapClient
from .freewheel_adapter import FreeWheelAdServerClient
from .ssp_base import SSPClient, SSPRegistry, SSPType, SSPDeal, SSPDealCreateRequest
from .ssp_mcp_client import MCPSSPClient
from .ssp_rest_client import RESTSSPClient
from .ssp_index_exchange import IndexExchangeSSPClient
from .ssp_factory import build_ssp_registry
from .ad_server_base import (
    AdServerClient,
    AdServerType,
    AdServerOrder,
    AdServerLineItem,
    AdServerDeal,
    AdServerInventoryItem,
    AdServerAudienceSegment,
    BookingResult,
    get_ad_server_client,
)

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
]
