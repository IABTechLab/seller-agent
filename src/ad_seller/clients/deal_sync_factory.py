# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal-sync registry factory — builds deal-sync clients from config.

Reads DEAL_SYNC_CONNECTORS from settings to build a DealSyncRegistry
with all configured providers.
"""

import logging
from typing import Any

from .deal_sync_base import DealSyncRegistry

logger = logging.getLogger(__name__)


def build_deal_sync_registry(settings: Any = None) -> DealSyncRegistry:
    """Build a DealSyncRegistry from application settings.

    Reads DEAL_SYNC_CONNECTORS (comma-separated list) and creates the
    appropriate client for each configured provider.
    """
    if settings is None:
        from ..config import get_settings

        settings = get_settings()

    registry = DealSyncRegistry()

    connectors = [s.strip() for s in settings.deal_sync_connectors.split(",") if s.strip()]

    for name in connectors:
        name_lower = name.lower()
        if name_lower == "deals_api_mcp":
            from .deals_api_mcp_client import DealsAPIMCPClient

            if not settings.deals_api_mcp_url:
                logger.warning("deals_api_mcp configured but DEALS_API_MCP_URL not set")
                continue

            registry.register(
                name_lower,
                DealsAPIMCPClient(
                    mcp_url=settings.deals_api_mcp_url,
                    api_key=settings.deals_api_mcp_key,
                    seller_origin=settings.deals_api_mcp_seller_origin,
                ),
            )
            logger.info("Registered deal-sync connector: %s", name_lower)
        else:
            logger.warning("Unknown deal-sync provider '%s'", name)

    return registry
