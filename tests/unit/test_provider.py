# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the provider-agnostic LLM factory."""

from ad_seller.config.settings import Settings
from ad_seller.llm import LLMFactory, LLMRole, get_llm


def _settings(**overrides) -> Settings:
    """Build an isolated Settings instance that ignores any local .env file."""
    return Settings(_env_file=None, **overrides)


class TestNativeRouting:
    """Provider is selected natively from the model prefix."""

    def test_anthropic_prefix_routes_to_anthropic(self):
        factory = LLMFactory(
            settings=_settings(
                default_llm_model="anthropic/claude-sonnet-4-5-20250929",
                llm_api_key="sk-test",
            )
        )
        llm = factory.create()
        assert llm.model == "claude-sonnet-4-5-20250929"
        assert llm.provider == "anthropic"

    def test_openai_prefix_routes_to_openai(self):
        factory = LLMFactory(
            settings=_settings(default_llm_model="openai/gpt-4o", llm_api_key="sk-test")
        )
        llm = factory.create()
        assert llm.model == "gpt-4o"
        assert llm.provider == "openai"

    def test_role_selects_model(self):
        factory = LLMFactory(
            settings=_settings(
                default_llm_model="openai/gpt-4o",
                manager_llm_model="openai/gpt-4o-mini",
                llm_api_key="sk-test",
            )
        )
        assert factory.create(role=LLMRole.DEFAULT).model == "gpt-4o"
        assert factory.create(role=LLMRole.MANAGER).model == "gpt-4o-mini"


class TestOpenAICompatibleRouting:
    """A custom base_url drives any OpenAI-compatible endpoint natively."""

    def test_nvidia_nim_routes_via_openai_with_base_url(self):
        factory = LLMFactory(
            settings=_settings(
                default_llm_model="mistralai/mistral-nemotron",
                llm_api_base_url="https://integrate.api.nvidia.com/v1",
                llm_api_key="nvapi-test",
            )
        )
        llm = factory.create()
        # Unknown-to-CrewAI model name still routes natively (no LiteLLM fallback)
        # because the base_url pins the OpenAI-protocol client.
        assert llm.provider == "openai"
        assert llm.model == "mistralai/mistral-nemotron"
        assert llm.base_url == "https://integrate.api.nvidia.com/v1"

    def test_local_ollama_needs_no_key(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        factory = LLMFactory(
            settings=_settings(
                default_llm_model="llama3",
                llm_api_base_url="http://localhost:11434/v1",
            )
        )
        llm = factory.create()
        assert llm.provider == "openai"
        assert llm.base_url == "http://localhost:11434/v1"


class TestParameterPassthrough:
    """Temperature, key, and api_version flow through to the LLM."""

    def test_temperature_override_and_fallback(self):
        factory = LLMFactory(
            settings=_settings(llm_temperature=0.42, llm_api_key="sk-test")
        )
        assert factory.create(temperature=0.9).temperature == 0.9
        assert factory.create().temperature == 0.42

    def test_api_key_passed_through(self):
        factory = LLMFactory(settings=_settings(llm_api_key="sk-secret"))
        assert factory.create().api_key == "sk-secret"


def test_get_llm_convenience_returns_llm():
    """The module-level helper returns a usable LLM for the default role."""
    llm = get_llm(temperature=0.3)
    assert llm is not None
    assert llm.temperature == 0.3
