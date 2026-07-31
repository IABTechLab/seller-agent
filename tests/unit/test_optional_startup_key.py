# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""ANTHROPIC_API_KEY is optional at startup (community report, buyer#114).

A pristine clone with no ``.env`` used to crash at import with a pydantic
``ValidationError`` because ``settings.anthropic_api_key`` had no default —
even though the deterministic surface never touches the key. The buyer
agent's contract is the model: the key is optional to START the server and
required only when LLM-backed flows actually run.

These tests pin the three sides of that contract:

1. Settings load keyless — no ValidationError, field is ``None``.
2. LLM-backed construction fails lazily, at use time, with a clear and
   actionable ``MissingApiKeyError`` (never a cryptic provider error deep
   inside a client). No network involved.
3. The deterministic proposal path still answers keyless: the proposal flow
   degrades to the existing rule-based fallback evaluation, exactly as it
   does for any other crew failure.
"""

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Same pattern used in test_proposal_flow_time_budget.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from ad_seller.config.settings import Settings  # noqa: E402
from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow  # noqa: E402
from ad_seller.llm import MissingApiKeyError, build_llm  # noqa: E402


def _keyless_settings(**overrides) -> Settings:
    """Settings as a pristine clone sees them: no .env, no Anthropic key."""
    overrides.setdefault("anthropic_api_key", None)
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def no_key_env(monkeypatch):
    """Strip the key from the process environment (CI exports a dummy)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# =============================================================================
# (1) Startup: settings load with no key at all
# =============================================================================


class TestKeylessSettings:
    def test_settings_load_without_key(self, no_key_env):
        """A pristine clone (no .env, no env var) must construct Settings."""
        settings = Settings(_env_file=None)
        assert settings.anthropic_api_key is None

    def test_key_still_read_when_provided(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
        settings = Settings(_env_file=None)
        assert settings.anthropic_api_key == "sk-ant-from-env"


# =============================================================================
# (2) LLM path: lazy, clear, actionable failure at use time
# =============================================================================


class TestBuildLlmKeyGuard:
    def test_anthropic_model_raises_clear_error_keyless(self, no_key_env, monkeypatch):
        monkeypatch.setattr("ad_seller.llm.get_settings", _keyless_settings)
        with pytest.raises(MissingApiKeyError) as exc_info:
            build_llm(
                model="anthropic/claude-sonnet-4-5-20250929",
                temperature=0.3,
                max_tokens=4096,
            )
        message = str(exc_info.value)
        # Actionable: names the missing variable, the model that needs it,
        # and both remedies (set the key / switch provider).
        assert "ANTHROPIC_API_KEY" in message
        assert "anthropic/claude-sonnet-4-5-20250929" in message
        assert "DEFAULT_LLM_MODEL" in message

    def test_key_in_settings_builds_normally(self, no_key_env, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _keyless_settings(anthropic_api_key="sk-ant-test"),
        )
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.3,
            max_tokens=4096,
        )
        assert llm.provider == "anthropic"

    def test_key_in_environment_builds_normally(self, monkeypatch):
        """Env-only key (e.g. exported after settings were cached) counts."""
        monkeypatch.setattr("ad_seller.llm.get_settings", _keyless_settings)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.3,
            max_tokens=4096,
        )
        assert llm.provider == "anthropic"

    def test_non_anthropic_model_needs_no_anthropic_key(self, no_key_env, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _keyless_settings(openai_api_key="sk-openai-test"),
        )
        llm = build_llm(model="openai/gpt-4o", temperature=0.5, max_tokens=4096)
        assert llm.provider == "openai"

    def test_openai_compatible_endpoint_needs_no_anthropic_key(self, no_key_env, monkeypatch):
        """A local Ollama-style endpoint stays fully keyless (existing contract)."""
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _keyless_settings(
                openai_compatible_llm_api_base_url="http://localhost:11434/v1"
            ),
        )
        llm = build_llm(model="llama3", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.base_url == "http://localhost:11434/v1"


# =============================================================================
# (3) Deterministic proposal path: keyless server still answers
# =============================================================================


def _make_product(product_id="ctv-premium-sports", base_cpm=35.0, floor_cpm=20.0):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

    return ProductDefinition(
        product_id=product_id,
        name="Premium CTV - Sports",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=base_cpm,
        floor_cpm=floor_cpm,
        minimum_impressions=100000,
    )


def _proposal_data(price=25.0, product_id="ctv-premium-sports"):
    return {
        "product_id": product_id,
        "deal_type": "preferred_deal",
        "price": price,
        "impressions": 1_000_000,
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "buyer_id": "buyer-1",
    }


def _flow_settings():
    """Minimal settings stand-in for the flow module (same idiom as
    test_proposal_flow_time_budget.py)."""
    return SimpleNamespace(
        proposal_flow_time_budget_seconds=20.0,
        approval_gate_enabled=False,
        approval_required_flows="",
        approval_required_above_value=0.0,
    )


@pytest.mark.asyncio
class TestKeylessDeterministicProposalPath:
    async def _run_keyless_flow(self, price):
        """Run the REAL flow + REAL crew factory + REAL build_llm keyless.

        Only the key sources are stripped: build_llm's settings see no
        Anthropic key and the env var is absent, so crew creation raises
        MissingApiKeyError and the flow must fall back deterministically.
        """
        from ad_seller.interfaces.api.deps import _build_buyer_context

        with (
            patch("ad_seller.llm.get_settings", _keyless_settings),
            patch(
                "ad_seller.flows.proposal_handling_flow.get_settings",
                _flow_settings,
            ),
            patch(
                "ad_seller.flows.proposal_handling_flow.emit_event",
                new_callable=AsyncMock,
            ),
            patch.dict(os.environ),
        ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            flow = ProposalHandlingFlow()
            return await flow.handle_proposal_async(
                proposal_id="prop-keyless-test",
                proposal_data=_proposal_data(price=price),
                buyer_context=_build_buyer_context(buyer_tier="agency", agency_id="agency-1"),
                products={"ctv-premium-sports": _make_product(floor_cpm=20.0)},
            )

    async def test_above_floor_proposal_accepted_keyless(self):
        result = await self._run_keyless_flow(price=25.0)
        assert result["recommendation"] == "accept"
        assert result["status"] == "accepted"
        # The failure was surfaced (not swallowed silently) with the
        # actionable message from build_llm.
        assert any(
            "Crew evaluation failed" in w and "ANTHROPIC_API_KEY" in w for w in result["warnings"]
        )

    async def test_below_floor_proposal_countered_keyless(self):
        result = await self._run_keyless_flow(price=15.0)
        assert result["recommendation"] == "counter"
        assert result["status"] == "counter_pending"
        assert result["counter_terms"] is not None
