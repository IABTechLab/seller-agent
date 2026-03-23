# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Create Deal From Template Tool — one-step deal creation from structured params."""

import json
from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class CreateDealFromTemplateInput(BaseModel):
    """Input schema for template-based deal creation."""

    deal_type: str = Field(description="Deal type: PG, PD, or PA")
    product_id: str = Field(description="Product ID from the seller catalog")
    impressions: Optional[int] = Field(default=None, description="Impression count (required for PG)")
    max_cpm: Optional[float] = Field(default=None, description="Buyer's maximum CPM — rejects if below seller floor")
    flight_start: Optional[str] = Field(default=None, description="Flight start date (YYYY-MM-DD)")
    flight_end: Optional[str] = Field(default=None, description="Flight end date (YYYY-MM-DD)")
    base_url: str = Field(default="http://localhost:8000", description="Base URL of the seller API")
    api_key: Optional[str] = Field(default=None, description="API key for authentication")


class CreateDealFromTemplateTool(BaseTool):
    """Create a deal directly from template parameters.

    Calls POST /api/v1/deals/from-template on the seller API.
    Combines quote + auto-book in one step. Returns the created deal
    or a rejection with the seller's minimum price.
    """

    name: str = "create_deal_from_template"
    description: str = (
        "Create a deal from template parameters (deal type, product, impressions, max CPM, flight dates). "
        "Returns the created deal on success or rejection details if max_cpm is below seller floor."
    )
    args_schema: Type[BaseModel] = CreateDealFromTemplateInput

    def _run(
        self,
        deal_type: str,
        product_id: str,
        impressions: Optional[int] = None,
        max_cpm: Optional[float] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
    ) -> str:
        """Call the from-template endpoint."""
        import httpx

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body = {
            "deal_type": deal_type,
            "product_id": product_id,
        }
        if impressions is not None:
            body["impressions"] = impressions
        if max_cpm is not None:
            body["max_cpm"] = max_cpm
        if flight_start:
            body["flight_start"] = flight_start
        if flight_end:
            body["flight_end"] = flight_end

        response = httpx.post(
            f"{base_url}/api/v1/deals/from-template",
            json=body,
            headers=headers,
            timeout=30,
        )

        if response.status_code == 201:
            return json.dumps(response.json(), indent=2)
        elif response.status_code == 422:
            return json.dumps({"rejected": True, **response.json().get("detail", {})}, indent=2)
        else:
            response.raise_for_status()
            return response.text
