# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the custom OpenAI-compatible endpoint alternative (build_llm)."""

from ad_seller.config.settings import Settings
from ad_seller.llm import build_llm


def _settings(**overrides) -> Settings:
    """Build an isolated Settings instance that ignores any local .env file."""
    overrides.setdefault("anthropic_api_key", "sk-ant-test")
    return Settings(_env_file=None, **overrides)


class TestUnchangedWhenNoBaseUrl:
    """No OPENAI_COMPATIBLE_LLM_API_BASE_URL configured — identical to
    constructing LLM directly, so DEFAULT_LLM_MODEL/MANAGER_LLM_MODEL
    provider swapping (Anthropic, OpenAI, Gemini, Bedrock) works exactly as
    before this module existed."""

    def test_anthropic_model_routes_natively(self, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _settings(default_llm_model="anthropic/claude-sonnet-4-5-20250929"),
        )
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.3,
            max_tokens=4096,
        )
        assert llm.model == "claude-sonnet-4-5-20250929"
        assert llm.provider == "anthropic"

    def test_openai_model_routes_natively(self, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _settings(openai_api_key="sk-openai-test"),
        )
        llm = build_llm(model="openai/gpt-4o", temperature=0.5, max_tokens=4096)
        assert llm.model == "gpt-4o"
        assert llm.provider == "openai"

    def test_temperature_and_max_tokens_pass_through(self, monkeypatch):
        monkeypatch.setattr("ad_seller.llm.get_settings", lambda: _settings())
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.7,
            max_tokens=2048,
        )
        assert llm.temperature == 0.7
        assert llm.max_tokens == 2048


class TestCustomOpenAICompatibleEndpoint:
    """OPENAI_COMPATIBLE_LLM_API_BASE_URL configured — pins routing to the
    native OpenAI client regardless of the model id's shape, covering NVIDIA
    NIM, Ollama, HuggingFace TGI, and similar endpoints."""

    def test_nvidia_nim_routes_via_openai_with_base_url(self, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _settings(
                openai_compatible_llm_api_key="nvapi-test",
                openai_compatible_llm_api_base_url="https://integrate.api.nvidia.com/v1",
            ),
        )
        llm = build_llm(
            model="meta/llama-3.1-70b-instruct", temperature=0.3, max_tokens=4096
        )
        assert llm.provider == "openai"
        assert llm.model == "meta/llama-3.1-70b-instruct"
        assert llm.base_url == "https://integrate.api.nvidia.com/v1"
        assert llm.api_key == "nvapi-test"

    def test_local_ollama_needs_no_key(self, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _settings(
                openai_compatible_llm_api_base_url="http://localhost:11434/v1"
            ),
        )
        llm = build_llm(model="llama3", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.model == "llama3"
        assert llm.base_url == "http://localhost:11434/v1"
