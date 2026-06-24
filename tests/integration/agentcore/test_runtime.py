"""AgentCore runtime tests for the Seller HTTP runtime.

These tests invoke the deployed runtime via `agentcore invoke` and validate
real responses. They require a deployed runtime and AWS credentials.

Usage:
    # Run all agentcore runtime tests
    pytest tests/integration/test_agentcore_runtime.py -v --profile genai

    # Run specific test groups
    pytest tests/integration/ -v -k "agentcore and chat" --profile genai
    pytest tests/integration/ -v -k "agentcore and crew" --profile genai
    pytest tests/integration/ -v -k "agentcore and create_deal" --profile genai

    # Via runner script
    bash tests/integration/run_runtime_tests.sh --profile genai
    bash tests/integration/run_runtime_tests.sh --profile genai -k "create_deal"

    # From deploy.sh
    bash infra/aws/agentcore/deploy.sh --mode http --name NAME --profile genai --test

Environment:
    SELLER_RUNTIME_ARN: Runtime ARN (auto-detected from .bedrock_agentcore.yaml)
    AWS_PROFILE: AWS CLI profile (or --profile pytest arg)
    AWS_REGION: Region (default: us-west-2)
"""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

logger = logging.getLogger(__name__)


@dataclass
class RuntimeConfig:
    arn: str
    region: str
    profile: Optional[str]
    agent_name: str


@pytest.fixture(scope="session")
def runtime_config(request) -> RuntimeConfig:
    """Resolve the runtime ARN and config for tests."""
    profile = request.config.getoption("--profile") or os.environ.get("AWS_PROFILE")
    region = os.environ.get("AWS_REGION", "us-west-2")
    arn = request.config.getoption("--runtime-arn") or os.environ.get("SELLER_RUNTIME_ARN", "")
    agent_name = request.config.getoption("--agent-name") or ""

    # Auto-detect from .bedrock_agentcore.yaml
    if not arn:
        yaml_path = Path(__file__).parent.parent.parent.parent / ".bedrock_agentcore.yaml"
        if yaml_path.exists():
            try:
                import yaml

                with open(yaml_path) as f:
                    cfg = yaml.safe_load(f)
                agents = cfg.get("agents", {})
                # Find the first agent with a runtime ARN
                for name, agent_cfg in agents.items():
                    bc = agent_cfg.get("bedrock_agentcore", {})
                    candidate = bc.get("agent_arn", "")
                    if candidate:
                        arn = candidate
                        agent_name = name
                        break
            except Exception as e:
                logger.warning("Failed to read .bedrock_agentcore.yaml: %s", e)

    if not arn:
        pytest.skip("No runtime ARN available — set SELLER_RUNTIME_ARN or deploy first")

    return RuntimeConfig(arn=arn, region=region, profile=profile, agent_name=agent_name)


def invoke_runtime(
    config: RuntimeConfig,
    payload: dict,
    timeout: int = 120,
    max_retries: int = 3,
    retry_wait: int = 30,
) -> dict:
    """Invoke the runtime and return parsed response.

    Returns dict with:
        - response: str (the text response)
        - raw: str (full agentcore invoke output)
        - success: bool
        - error: str (if failed)
    """
    payload_json = json.dumps(payload)

    # Build agentcore invoke command
    cmd = ["agentcore", "invoke", payload_json]
    env = os.environ.copy()
    if config.profile:
        env["AWS_PROFILE"] = config.profile
    env["AWS_REGION"] = config.region

    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(Path(__file__).parent.parent.parent.parent),
            )
            output = result.stdout + result.stderr

            # Check for cold start timeout (retryable)
            if re.search(
                r"initialization time exceeded|32010|RuntimeClientError", output, re.IGNORECASE
            ):
                if attempt < max_retries:
                    logger.warning("Cold start timeout (attempt %d/%d)", attempt, max_retries)
                    time.sleep(retry_wait)
                    continue
                return {
                    "response": "",
                    "raw": output,
                    "success": False,
                    "error": "Cold start timeout",
                }

            # Extract response text
            response_text = _extract_response(output)

            # Check for errors in response
            if re.search(r'"error":|"exception":|Invocation failed', output, re.IGNORECASE):
                return {
                    "response": response_text,
                    "raw": output,
                    "success": False,
                    "error": response_text,
                }

            return {"response": response_text, "raw": output, "success": True, "error": ""}

        except subprocess.TimeoutExpired:
            if attempt < max_retries:
                logger.warning("Invoke timeout (attempt %d/%d)", attempt, max_retries)
                time.sleep(retry_wait)
                continue
            return {"response": "", "raw": "", "success": False, "error": "Invoke timeout"}

    return {"response": "", "raw": "", "success": False, "error": "Max retries exceeded"}


def _extract_response(output: str) -> str:
    """Extract the response text from agentcore invoke output."""
    # Try to find "Response:" section
    match = re.search(r"Response:\s*\n?(.*)", output, re.DOTALL)
    if match:
        text = match.group(1).strip()
        # Remove box-drawing characters
        text = re.sub(r"[│╭╰╮─╯┌┐└┘├┤┬┴┼]", "", text)
        return text.strip()

    # Fallback: remove box-drawing and return everything
    cleaned = re.sub(r"[│╭╰╮─╯┌┐└┘├┤┬┴┼]", "", output)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Chat mode tests
# ---------------------------------------------------------------------------


@pytest.mark.agentcore
class TestChatMode:
    """Tests for the chat routing mode (keyword-based ChatInterface)."""

    def test_list_products(self, runtime_config):
        """Chat mode responds to 'list products' with inventory data."""
        result = invoke_runtime(runtime_config, {"prompt": "list products"})
        assert result["success"], f"Invoke failed: {result['error']}"
        # Should mention products or inventory
        response = result["response"].lower()
        assert any(kw in response for kw in ["product", "inventory", "ctv", "video", "display"]), (
            f"Response doesn't mention products: {result['response'][:200]}"
        )


# ---------------------------------------------------------------------------
# Crew mode tests — individual tools
# ---------------------------------------------------------------------------


@pytest.mark.agentcore
class TestCrewListProducts:
    """Crew mode: list_products tool."""

    def test_returns_real_products(self, runtime_config):
        result = invoke_runtime(
            runtime_config,
            {"prompt": "show me all available inventory", "routing_mode": "crew"},
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"]
        # Should contain real Meridian Media Group product IDs
        assert any(
            pid in response for pid in ["inv-ctv-", "inv-dig-", "inv-lin-", "inv-vid-", "inv-aud-"]
        ), f"No real product IDs in response: {response[:300]}"


@pytest.mark.agentcore
class TestCrewGetPricing:
    """Crew mode: get_pricing tool."""

    def test_pricing_with_product_id(self, runtime_config):
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": "get pricing for inv-ctv-apex-sports-nba for preferred agency tier with 5M impressions",
                "routing_mode": "crew",
            },
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"]
        # Should contain CPM pricing
        assert re.search(r"\$\d+", response), f"No pricing in response: {response[:300]}"
        assert "inv-ctv-apex-sports-nba" in response or "apex" in response.lower()


@pytest.mark.agentcore
class TestCrewGetRateCard:
    """Crew mode: get_rate_card tool."""

    def test_rate_card_by_type(self, runtime_config):
        result = invoke_runtime(
            runtime_config,
            {"prompt": "get the rate card organized by inventory type", "routing_mode": "crew"},
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"].lower()
        # Should have inventory type groupings
        assert any(kw in response for kw in ["display", "video", "linear", "ctv", "audio"]), (
            f"No inventory types in response: {result['response'][:300]}"
        )


@pytest.mark.agentcore
class TestCrewDiscoverInventory:
    """Crew mode: discover_inventory tool."""

    def test_discover_ctv_sports(self, runtime_config):
        result = invoke_runtime(
            runtime_config,
            {"prompt": "find CTV sports inventory", "routing_mode": "crew"},
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"].lower()
        assert any(kw in response for kw in ["ctv", "sports", "apex", "inv-"]), (
            f"No CTV sports results: {result['response'][:300]}"
        )


@pytest.mark.agentcore
class TestCrewGetProductDetails:
    """Crew mode: get_product_details tool."""

    def test_product_details_by_id(self, runtime_config):
        result = invoke_runtime(
            runtime_config,
            {"prompt": "get details for product inv-ctv-apex-sports-nba", "routing_mode": "crew"},
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"]
        assert "inv-ctv-apex-sports-nba" in response or "apex" in response.lower()


@pytest.mark.agentcore
class TestCrewCreateDeal:
    """Crew mode: create_deal tool."""

    def test_deal_below_floor_rejected(self, runtime_config):
        """Offer below floor price returns pricing mismatch, not 401."""
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": "negotiate a deal for inv-ctv-apex-sports-nba at $30 CPM for 3M impressions as a Preferred Deal",
                "routing_mode": "crew",
            },
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"].lower()
        # Should mention floor price or price below floor — NOT 401 auth error
        assert "401" not in response, f"Got 401 auth error: {result['response'][:300]}"
        assert any(kw in response for kw in ["floor", "below", "minimum", "price"]), (
            f"No pricing rejection in response: {result['response'][:300]}"
        )

    def test_deal_above_floor_succeeds(self, runtime_config):
        """Offer above floor price creates a deal with Deal ID."""
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": "create a deal for inv-ctv-apex-sports-nba at $55 CPM for 2M impressions as a Preferred Deal",
                "routing_mode": "crew",
            },
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"]
        # Should contain a DEAL ID
        assert re.search(r"DEAL-[A-Z0-9]+", response), f"No Deal ID in response: {response[:300]}"
        assert "401" not in response.lower() or "deal-" in response.lower()


# ---------------------------------------------------------------------------
# Complex multi-step scenario
# ---------------------------------------------------------------------------


@pytest.mark.agentcore
class TestCrewComplexScenario:
    """Crew mode: complex multi-tool scenario combining discovery + pricing."""

    def test_inventory_with_pricing_recommendation(self, runtime_config):
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": "Show me all CTV sports inventory with pricing, and recommend the best products for a $200K automotive campaign targeting adults 25-54.",
                "routing_mode": "crew",
            },
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"].lower()
        # Should contain real inventory data with pricing
        assert any(kw in response for kw in ["inv-ctv", "cpm", "$", "apex", "sports"]), (
            f"No inventory/pricing data: {result['response'][:300]}"
        )
