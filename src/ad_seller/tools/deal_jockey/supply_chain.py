# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Supply Chain Transparency Tool — sellers.json-based self-description."""

import json
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class GetSupplyChainInput(BaseModel):
    """Input schema for supply chain lookup (no params needed)."""

    pass


class GetSupplyChainTool(BaseTool):
    """Return sellers.json-based self-description of this seller.

    Provides supply chain transparency for buyer agents to evaluate
    supply paths. If SELLERS_JSON_PATH is configured, parses the real
    sellers.json file. Otherwise returns a default single-node chain.
    """

    name: str = "get_supply_chain"
    description: str = (
        "Get the supply chain transparency info for this seller. "
        "Returns seller identity, type, supported deal types, and schain nodes "
        "in sellers.json format. Parses real sellers.json if configured."
    )
    args_schema: Type[BaseModel] = GetSupplyChainInput

    def _run(self) -> str:
        """Return supply chain info, from sellers.json if available."""
        from ...config import get_settings
        from ...models.supply_chain import load_sellers_json, build_schain_from_sellers_json

        settings = get_settings()
        seller_domain = getattr(settings, "seller_domain", "demo-publisher.example.com")
        seller_name = getattr(settings, "seller_name", "Demo Publisher")
        seller_id = getattr(settings, "seller_organization_id", "default")
        sellers_json_path = getattr(settings, "sellers_json_path", None)

        sellers_json = load_sellers_json(sellers_json_path)

        if sellers_json:
            primary = next(
                (s for s in sellers_json.sellers if s.seller_id == seller_id),
                sellers_json.sellers[0] if sellers_json.sellers else None,
            )
            schain_obj = build_schain_from_sellers_json(sellers_json, seller_id)

            result = {
                "seller_id": primary.seller_id if primary else seller_id,
                "seller_name": primary.name if primary else seller_name,
                "seller_type": primary.seller_type if primary else "PUBLISHER",
                "domain": primary.domain if primary else seller_domain,
                "is_direct": primary.seller_type == "PUBLISHER" if primary else True,
                "supported_deal_types": ["programmatic_guaranteed", "preferred_deal", "private_auction"],
                "contact_email": sellers_json.contact_email,
                "schain": schain_obj.model_dump(),
                "version": sellers_json.version,
            }
        else:
            result = {
                "seller_id": seller_id,
                "seller_name": seller_name,
                "seller_type": "PUBLISHER",
                "domain": seller_domain,
                "is_direct": True,
                "supported_deal_types": ["programmatic_guaranteed", "preferred_deal", "private_auction"],
                "schain": {
                    "ver": "1.0",
                    "complete": 1,
                    "nodes": [
                        {"asi": seller_domain, "sid": seller_id, "hp": 1, "name": seller_name, "domain": seller_domain},
                    ],
                },
            }

        return json.dumps(result, indent=2)
