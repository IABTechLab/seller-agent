# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SSP registry factory — builds SSP clients from config.

Reads SSP_CONNECTORS and per-SSP config from settings to build
an SSPRegistry with all configured SSPs and routing rules.
"""

import logging
from typing import Any

from .ssp_base import SSPRegistry, SSPType

logger = logging.getLogger(__name__)


def build_ssp_registry(settings: Any = None) -> SSPRegistry:
    """Build an SSPRegistry from application settings.

    Reads SSP_CONNECTORS (comma-separated list) and creates the
    appropriate client for each configured SSP.

    Args:
        settings: Application settings. If None, loads from config.

    Returns:
        Configured SSPRegistry with all enabled SSPs.
    """
    if settings is None:
        from ..config import get_settings
        settings = get_settings()

    registry = SSPRegistry()

    connectors = [s.strip() for s in settings.ssp_connectors.split(",") if s.strip()]

    if not connectors:
        logger.info("No SSP connectors configured (SSP_CONNECTORS is empty)")
        return registry

    for name in connectors:
        try:
            client = _create_ssp_client(name, settings)
            if client:
                registry.register(name, client)
                logger.info("Registered SSP connector: %s (%s)", name, client.ssp_type.value)
        except Exception as e:
            logger.error("Failed to create SSP client '%s': %s", name, e)

    # Parse routing rules
    if settings.ssp_routing_rules:
        rules = {}
        for rule in settings.ssp_routing_rules.split(","):
            rule = rule.strip()
            if ":" in rule:
                key, ssp = rule.split(":", 1)
                rules[key.strip()] = ssp.strip()
        if rules:
            registry.set_routing_rules(rules)
            logger.info("SSP routing rules: %s", rules)

    return registry


def _create_ssp_client(name: str, settings: Any) -> Any:
    """Create an SSP client by name from settings.

    Determines whether to use MCP or REST based on what's configured.
    """
    name_lower = name.lower()

    if name_lower == "pubmatic":
        from .ssp_mcp_client import MCPSSPClient

        if not settings.pubmatic_mcp_url:
            logger.warning("PubMatic configured but PUBMATIC_MCP_URL not set")
            return None

        return MCPSSPClient(
            ssp_type=SSPType.PUBMATIC,
            ssp_name="PubMatic",
            mcp_url=settings.pubmatic_mcp_url,
            api_key=settings.pubmatic_api_key,
            deal_management_tool="deal_management",
            deal_troubleshooting_tool="deal_troubleshooting",
        )

    elif name_lower == "magnite":
        from .ssp_rest_client import RESTSSPClient

        if not settings.magnite_api_url:
            logger.warning("Magnite configured but MAGNITE_API_URL not set")
            return None

        return RESTSSPClient(
            ssp_type=SSPType.MAGNITE,
            ssp_name="Magnite",
            base_url=settings.magnite_api_url,
            api_key=settings.magnite_api_key,
        )

    elif name_lower == "index_exchange":
        from .ssp_index_exchange import IndexExchangeSSPClient

        if not settings.index_exchange_api_url:
            logger.warning("Index Exchange configured but INDEX_EXCHANGE_API_URL not set")
            return None

        return IndexExchangeSSPClient(
            base_url=settings.index_exchange_api_url,
            api_key=settings.index_exchange_api_key,
        )

    else:
        logger.warning(
            "Unknown SSP '%s'. To add support, create a client in ssp_factory.py "
            "or use the generic REST/MCP clients.", name,
        )
        return None
