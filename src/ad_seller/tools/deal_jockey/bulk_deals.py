# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Bulk Deal Operations Tool — batch create/update/cancel deals."""

import json
from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class BulkDealOperationsInput(BaseModel):
    """Input schema for bulk deal operations."""

    operations: str = Field(
        description=(
            "JSON array of operations. Each object has: "
            '"action" (create|update|cancel), '
            '"deal_id" (for update/cancel), '
            '"quote_id" (for create), '
            '"notes" (optional). '
            'Example: [{"action":"cancel","deal_id":"DEMO-ABC123"}]'
        ),
    )
    base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL of the seller API",
    )


class BulkDealOperationsTool(BaseTool):
    """Process a batch of deal operations via the seller API.

    Enables the Deal Jockey buyer agent to efficiently manage multiple
    deals in a single request. Calls POST /api/v1/deals/bulk.
    """

    name: str = "bulk_deal_operations"
    description: str = (
        "Execute multiple deal operations (create, update, cancel) in a single batch. "
        "Input is a JSON array of operations. Returns per-operation success/failure."
    )
    args_schema: Type[BaseModel] = BulkDealOperationsInput

    def _run(self, operations: str, base_url: str = "http://localhost:8000") -> str:
        """Call the bulk deals endpoint."""
        import httpx

        ops = json.loads(operations)
        response = httpx.post(
            f"{base_url}/api/v1/deals/bulk",
            json={"operations": ops},
            timeout=30,
        )
        response.raise_for_status()
        return json.dumps(response.json(), indent=2)
