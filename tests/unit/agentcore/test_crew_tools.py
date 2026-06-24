"""Tests for AgentCore CrewAI tools (BaseTool subclasses).

Validates:
- Tool instantiation and schema correctness
- Tool injection into CrewAI agents
- REST API calls via httpx (mocked for unit tests)
- Error handling when REST API is unavailable
- Live integration with real FastAPI server (gated behind env var)

The tools call the seller's own REST API (localhost:8001) which is the
correct abstraction: CSV → ProductSetupFlow → FastAPI → tools.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel

# Mock bedrock_agentcore before importing http_main
_mock_agentcore = MagicMock()
_mock_app = MagicMock()
_mock_app.entrypoint = lambda fn: fn
_mock_agentcore.BedrockAgentCoreApp.return_value = _mock_app
sys.modules.setdefault("bedrock_agentcore", MagicMock())
sys.modules.setdefault("bedrock_agentcore.runtime", _mock_agentcore)

from ad_seller.interfaces.agentcore.crew_tools import (  # noqa: E402
    AGENTCORE_SELLER_TOOLS,
    CreateDealInput,
    CreateDealTool,
    DiscoverInventoryTool,
    DiscoveryInput,
    EmptyInput,
    GetPricingTool,
    GetProductDetailsTool,
    GetRateCardTool,
    ListProductsTool,
    PricingInput,
    ProductIdInput,
)

# ---------------------------------------------------------------------------
# Tool instantiation and schema tests
# ---------------------------------------------------------------------------


class TestToolInstantiation:
    """Verify all tools instantiate correctly as BaseTool subclasses."""

    def test_all_tools_are_base_tool_instances(self):
        assert len(AGENTCORE_SELLER_TOOLS) == 6
        for tool in AGENTCORE_SELLER_TOOLS:
            assert isinstance(tool, BaseTool), f"{tool.name} is {type(tool).__name__}, not BaseTool"

    def test_tool_names_are_unique(self):
        names = [t.name for t in AGENTCORE_SELLER_TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    def test_tool_names_match_expected(self):
        expected = {
            "list_products",
            "get_product_details",
            "get_pricing",
            "discover_inventory",
            "create_deal",
            "get_rate_card",
        }
        actual = {t.name for t in AGENTCORE_SELLER_TOOLS}
        assert actual == expected

    def test_all_tools_have_descriptions(self):
        for tool in AGENTCORE_SELLER_TOOLS:
            assert tool.description, f"{tool.name} has empty description"
            assert len(tool.description) > 20

    def test_all_tools_have_args_schema(self):
        for tool in AGENTCORE_SELLER_TOOLS:
            assert tool.args_schema is not None
            assert issubclass(tool.args_schema, BaseModel)


class TestToolSchemas:
    """Verify Pydantic schemas produce correct JSON schemas for the LLM."""

    def test_empty_input_schema(self):
        schema = EmptyInput.model_json_schema()
        assert schema.get("required") is None or schema["required"] == []

    def test_product_id_input_schema(self):
        schema = ProductIdInput.model_json_schema()
        assert "product_id" in schema["properties"]
        assert "product_id" in schema.get("required", [])

    def test_pricing_input_schema(self):
        schema = PricingInput.model_json_schema()
        assert "product_id" in schema["properties"]
        assert "buyer_tier" in schema["properties"]
        assert "volume" in schema["properties"]
        assert "product_id" in schema.get("required", [])
        assert "buyer_tier" not in schema.get("required", [])

    def test_discovery_input_schema(self):
        schema = DiscoveryInput.model_json_schema()
        assert "query" in schema["properties"]

    def test_create_deal_input_schema(self):
        schema = CreateDealInput.model_json_schema()
        assert "product_id" in schema["properties"]
        assert "deal_type" in schema["properties"]
        assert "product_id" in schema.get("required", [])


# ---------------------------------------------------------------------------
# CrewAI agent injection tests
# ---------------------------------------------------------------------------


class TestCrewAIInjection:
    """Verify tools can be injected into CrewAI agents."""

    def test_inject_into_agent(self):
        from crewai import LLM, Agent

        agent = Agent(
            role="Test Inventory Manager",
            goal="Test tool injection",
            backstory="Testing",
            tools=AGENTCORE_SELLER_TOOLS,
            llm=LLM(model="bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            memory=False,
            verbose=False,
        )
        assert len(agent.tools) == 6
        tool_names = {t.name for t in agent.tools}
        assert "list_products" in tool_names
        assert "get_pricing" in tool_names

    def test_inject_replaces_empty_tools(self):
        from crewai import LLM, Agent

        agent = Agent(
            role="Inventory Manager",
            goal="Maximize yield",
            backstory="Seasoned strategist",
            tools=[],
            llm=LLM(model="bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            memory=False,
            verbose=False,
        )
        assert len(agent.tools) == 0
        agent.tools = AGENTCORE_SELLER_TOOLS
        assert len(agent.tools) == 6


# ---------------------------------------------------------------------------
# Tool execution tests (mocked httpx)
# ---------------------------------------------------------------------------


class TestListProductsTool:
    def test_success(self):
        mock_response = httpx.Response(
            200,
            json={
                "products": [{"product_id": "prod_001", "name": "CTV Premium", "base_cpm": 42.5}]
            },
            request=httpx.Request("GET", "http://localhost:8001/products"),
        )
        tool = ListProductsTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.get", return_value=mock_response
        ):
            result = tool._run()
        data = json.loads(result)
        assert "products" in data
        assert data["products"][0]["product_id"] == "prod_001"

    def test_connection_error(self):
        tool = ListProductsTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = tool._run()
        assert "Error listing products" in result

    def test_http_500_error(self):
        mock_response = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("GET", "http://localhost:8001/products"),
        )
        tool = ListProductsTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.get", return_value=mock_response
        ):
            result = tool._run()
        assert "Error listing products" in result


class TestGetProductDetailsTool:
    def test_success(self):
        mock_response = httpx.Response(
            200,
            json={
                "product_id": "prod_001",
                "name": "CTV Premium",
                "base_cpm": 42.5,
                "inventory_type": "ctv",
            },
            request=httpx.Request("GET", "http://localhost:8001/products/prod_001"),
        )
        tool = GetProductDetailsTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.get", return_value=mock_response
        ):
            result = tool._run(product_id="prod_001")
        data = json.loads(result)
        assert data["product_id"] == "prod_001"
        assert data["inventory_type"] == "ctv"

    def test_not_found(self):
        mock_response = httpx.Response(
            404,
            json={"detail": "Product not found"},
            request=httpx.Request("GET", "http://localhost:8001/products/nonexistent"),
        )
        tool = GetProductDetailsTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.get", return_value=mock_response
        ):
            result = tool._run(product_id="nonexistent")
        assert "Error getting product" in result


class TestGetPricingTool:
    def test_success_public_tier(self):
        mock_response = httpx.Response(
            200,
            json={
                "product_id": "prod_001",
                "base_price": 42.5,
                "final_price": 42.5,
                "currency": "USD",
                "tier_discount": 0.0,
                "volume_discount": 0.0,
                "rationale": "Public tier",
            },
            request=httpx.Request("POST", "http://localhost:8001/pricing"),
        )
        tool = GetPricingTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
        ):
            result = tool._run(product_id="prod_001")
        data = json.loads(result)
        assert data["base_price"] == 42.5

    def test_volume_passed_in_body(self):
        mock_response = httpx.Response(
            200,
            json={
                "product_id": "p1",
                "base_price": 42.5,
                "final_price": 38.25,
                "currency": "USD",
                "tier_discount": 0.0,
                "volume_discount": 0.1,
                "rationale": "Volume discount",
            },
            request=httpx.Request("POST", "http://localhost:8001/pricing"),
        )
        tool = GetPricingTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
        ) as mock_post:
            tool._run(product_id="p1", buyer_tier="preferred", volume=5_000_000)
        body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert body["volume"] == 5_000_000
        assert body["buyer_tier"] == "preferred"

    def test_zero_volume_not_sent(self):
        mock_response = httpx.Response(
            200,
            json={
                "product_id": "p1",
                "base_price": 10,
                "final_price": 10,
                "currency": "USD",
                "tier_discount": 0,
                "volume_discount": 0,
                "rationale": "ok",
            },
            request=httpx.Request("POST", "http://localhost:8001/pricing"),
        )
        tool = GetPricingTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
        ) as mock_post:
            tool._run(product_id="p1")
        body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "volume" not in body


class TestDiscoverInventoryTool:
    def test_success_with_query(self):
        mock_response = httpx.Response(
            200,
            json={"results": [{"product_id": "prod_ctv_001", "relevance": 0.95}]},
            request=httpx.Request("POST", "http://localhost:8001/discovery"),
        )
        tool = DiscoverInventoryTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
        ) as mock_post:
            result = tool._run(query="CTV inventory for sports")
        data = json.loads(result)
        assert "results" in data
        body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert body["query"] == "CTV inventory for sports"

    def test_empty_query_sends_empty_string(self):
        mock_response = httpx.Response(
            200,
            json={"results": []},
            request=httpx.Request("POST", "http://localhost:8001/discovery"),
        )
        tool = DiscoverInventoryTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
        ) as mock_post:
            tool._run(query="")
        body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert body == {"query": ""}


class TestCreateDealTool:
    def test_success_with_api_key(self):
        """REST API path succeeds when INTERNAL_API_KEY is set."""
        mock_response = httpx.Response(
            200,
            json={
                "deal_id": "DEAL-2026-001",
                "deal_type": "preferred_deal",
                "price": 38.0,
                "pricing_model": "cpm",
                "openrtb_params": {"bidfloor": 38.0},
                "activation_instructions": {"dsp": "Use deal ID"},
            },
            request=httpx.Request("POST", "http://localhost:8001/api/v1/deals/from-template"),
        )
        tool = CreateDealTool()
        with patch.dict(os.environ, {"INTERNAL_API_KEY": "test-key-123"}):
            with patch(
                "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
            ):
                result = tool._run(
                    product_id="prod_001", deal_type="PD", max_cpm=40.0, impressions=1_000_000
                )
        data = json.loads(result)
        assert data["deal_id"] == "DEAL-2026-001"

    def test_minimal_params_no_optional_fields(self):
        mock_response = httpx.Response(
            200,
            json={
                "deal_id": "DEAL-002",
                "deal_type": "preferred_deal",
                "price": 15.0,
                "pricing_model": "cpm",
                "openrtb_params": {},
                "activation_instructions": {},
            },
            request=httpx.Request("POST", "http://localhost:8001/api/v1/deals/from-template"),
        )
        tool = CreateDealTool()
        with patch.dict(os.environ, {"INTERNAL_API_KEY": "test-key-123"}):
            with patch(
                "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
            ) as mock_post:
                tool._run(product_id="prod_001")
        body = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert body["product_id"] == "prod_001"
        assert body["deal_type"] == "PD"
        assert "max_cpm" not in body
        assert "impressions" not in body

    def test_fallback_to_direct_when_no_api_key(self):
        """Falls back to direct in-process deal creation when no API key."""
        tool = CreateDealTool()
        with patch.dict(os.environ, {}, clear=False):
            # Remove INTERNAL_API_KEY if present
            os.environ.pop("INTERNAL_API_KEY", None)
            with patch.object(
                CreateDealTool,
                "_create_deal_direct",
                return_value=json.dumps({"deal_id": "DEAL-DIRECT-001", "status": "booked"}),
            ) as mock_direct:
                result = tool._run(product_id="prod_001", deal_type="PD", max_cpm=40.0)
        mock_direct.assert_called_once_with("prod_001", "PD", 40.0, 0)
        data = json.loads(result)
        assert data["deal_id"] == "DEAL-DIRECT-001"

    def test_fallback_to_direct_on_401(self):
        """Falls back to direct when REST API returns 401."""
        mock_response = httpx.Response(
            401,
            json={"detail": "Invalid API key"},
            request=httpx.Request("POST", "http://localhost:8001/api/v1/deals/from-template"),
        )
        tool = CreateDealTool()
        with patch.dict(os.environ, {"INTERNAL_API_KEY": "bad-key"}):
            with patch(
                "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
            ):
                with patch.object(
                    CreateDealTool,
                    "_create_deal_direct",
                    return_value=json.dumps({"deal_id": "DEAL-FALLBACK", "status": "booked"}),
                ) as mock_direct:
                    result = tool._run(product_id="prod_001", deal_type="PG", impressions=5_000_000)
        mock_direct.assert_called_once()
        data = json.loads(result)
        assert data["deal_id"] == "DEAL-FALLBACK"

    def test_non_401_error_returns_error_message(self):
        """Non-401 HTTP errors return error message without fallback."""
        mock_response = httpx.Response(
            422,
            json={"detail": "Validation error"},
            request=httpx.Request("POST", "http://localhost:8001/api/v1/deals/from-template"),
        )
        tool = CreateDealTool()
        with patch.dict(os.environ, {"INTERNAL_API_KEY": "test-key"}):
            with patch(
                "ad_seller.interfaces.agentcore.crew_tools.httpx.post", return_value=mock_response
            ):
                result = tool._run(product_id="prod_001", deal_type="PD", max_cpm=5.0)
        assert "Error creating deal" in result


class TestGetRateCardTool:
    def test_builds_rate_card_from_products(self):
        mock_response = httpx.Response(
            200,
            json={
                "products": [
                    {
                        "product_id": "p1",
                        "name": "CTV Premium",
                        "inventory_type": "ctv",
                        "base_cpm": 42.5,
                    },
                    {
                        "product_id": "p2",
                        "name": "Display Standard",
                        "inventory_type": "display",
                        "base_cpm": 12.0,
                    },
                    {
                        "product_id": "p3",
                        "name": "CTV Sports",
                        "inventory_type": "ctv",
                        "base_cpm": 45.0,
                    },
                ]
            },
            request=httpx.Request("GET", "http://localhost:8001/products"),
        )
        tool = GetRateCardTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.get", return_value=mock_response
        ):
            result = tool._run()
        data = json.loads(result)
        assert "rate_card" in data
        assert "ctv" in data["rate_card"]
        assert "display" in data["rate_card"]
        assert len(data["rate_card"]["ctv"]) == 2

    def test_handles_plain_list_response(self):
        mock_response = httpx.Response(
            200,
            json=[
                {
                    "product_id": "p1",
                    "name": "Video Pre-roll",
                    "channel": "video",
                    "avg_cpm_usd": 25.0,
                }
            ],
            request=httpx.Request("GET", "http://localhost:8001/products"),
        )
        tool = GetRateCardTool()
        with patch(
            "ad_seller.interfaces.agentcore.crew_tools.httpx.get", return_value=mock_response
        ):
            result = tool._run()
        data = json.loads(result)
        assert "video" in data["rate_card"]


# ---------------------------------------------------------------------------
# BASE_URL configuration test
# ---------------------------------------------------------------------------


class TestBaseURLConfig:
    def test_default_base_url(self):
        from ad_seller.interfaces.agentcore import crew_tools

        assert "localhost" in crew_tools._BASE_URL or "8001" in crew_tools._BASE_URL
