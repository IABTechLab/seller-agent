# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal Performance Tool — delivery stats for buyer SPO feedback loop."""

import json
from datetime import datetime
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class GetDealPerformanceInput(BaseModel):
    """Input schema for deal performance lookup."""

    deal_id: str = Field(description="Deal ID to get performance metrics for")


class GetDealPerformanceTool(BaseTool):
    """Return delivery stats for a deal.

    Provides performance feedback for buyer Supply Path Optimization (SPO).
    Returns impressions, fill rate, win rate, pacing, and actual CPM.
    Placeholder/mock stats initially — real ad server integration later.
    """

    name: str = "get_deal_performance"
    description: str = (
        "Get delivery and performance metrics for a deal. "
        "Returns impressions available/served, fill rate, win rate, "
        "actual CPM, and delivery pacing status."
    )
    args_schema: Type[BaseModel] = GetDealPerformanceInput

    def _run(self, deal_id: str) -> str:
        """Return placeholder performance data for a deal."""
        # Placeholder — real stats come from ad server integration
        now = datetime.utcnow().isoformat() + "Z"
        result = {
            "deal_id": deal_id,
            "impressions_available": 1000000,
            "impressions_served": 0,
            "fill_rate": 0.0,
            "win_rate": 0.0,
            "avg_cpm_actual": 0.0,
            "delivery_pacing": "not_started",
            "last_updated": now,
        }
        return json.dumps(result, indent=2)
