# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Supply Chain Transparency Tool — sellers.json-like self-description."""

import json
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class GetSupplyChainInput(BaseModel):
    """Input schema for supply chain lookup (no params needed)."""

    pass


class GetSupplyChainTool(BaseTool):
    """Return sellers.json-like self-description of this seller.

    Provides supply chain transparency for buyer agents to evaluate
    supply paths. Returns seller identity, supported deal types,
    and schain nodes.
    """

    name: str = "get_supply_chain"
    description: str = (
        "Get the supply chain transparency info for this seller. "
        "Returns seller identity, type, supported deal types, and schain nodes "
        "in sellers.json format."
    )
    args_schema: Type[BaseModel] = GetSupplyChainInput

    def _run(self) -> str:
        """Return hardcoded supply chain info for this seller instance."""
        from ...config import get_settings

        settings = get_settings()
        seller_domain = getattr(settings, "seller_domain", "demo-publisher.example.com")
        seller_name = getattr(settings, "seller_name", "Demo Publisher")
        seller_id = getattr(settings, "seller_organization_id", "default")

        result = {
            "seller_id": seller_id,
            "seller_name": seller_name,
            "seller_type": "PUBLISHER",
            "domain": seller_domain,
            "is_direct": True,
            "supported_deal_types": ["programmatic_guaranteed", "preferred_deal", "private_auction"],
            "schain": [
                {
                    "asi": seller_domain,
                    "sid": seller_id,
                    "name": seller_name,
                    "domain": seller_domain,
                    "seller_type": "PUBLISHER",
                    "is_direct": True,
                    "comment": "Direct seller — no intermediaries",
                },
            ],
        }
        return json.dumps(result, indent=2)
