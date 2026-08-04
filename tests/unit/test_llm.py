# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the custom OpenAI-compatible endpoint alternative (build_llm)."""

from ad_seller.config.settings import Settings
from ad_seller.llm import _model_accepts_temperature, build_llm


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
        llm = build_llm(model="meta/llama-3.1-70b-instruct", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.model == "meta/llama-3.1-70b-instruct"
        assert llm.base_url == "https://integrate.api.nvidia.com/v1"
        assert llm.api_key == "nvapi-test"

    def test_local_ollama_needs_no_key(self, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _settings(openai_compatible_llm_api_base_url="http://localhost:11434/v1"),
        )
        llm = build_llm(model="llama3", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.model == "llama3"
        assert llm.base_url == "http://localhost:11434/v1"


class TestModelAcceptsTemperature:
    """Anthropic rejects ``temperature`` on Opus 4.7+, Sonnet 5+, and
    Fable/Mythos (400 invalid_request_error), regardless of provider prefix."""

    def test_opus_4_8_rejects(self):
        assert not _model_accepts_temperature("anthropic/claude-opus-4-8")

    def test_opus_4_7_rejects(self):
        assert not _model_accepts_temperature("anthropic/claude-opus-4-7")

    def test_bedrock_prefixed_opus_rejects(self):
        assert not _model_accepts_temperature("bedrock/us.anthropic.claude-opus-4-8-v1:0")

    def test_sonnet_5_rejects(self):
        assert not _model_accepts_temperature("anthropic/claude-sonnet-5")

    def test_fable_rejects(self):
        assert not _model_accepts_temperature("anthropic/claude-fable-5")

    def test_case_insensitive(self):
        assert not _model_accepts_temperature("anthropic/Claude-Opus-4-8")

    def test_sonnet_4_5_accepts(self):
        assert _model_accepts_temperature("anthropic/claude-sonnet-4-5-20250929")

    def test_haiku_accepts(self):
        assert _model_accepts_temperature("anthropic/claude-haiku-4-5")

    def test_non_claude_model_accepts(self):
        assert _model_accepts_temperature("openai/gpt-4o")


class TestTemperatureOmittedWhenUnsupported:
    """build_llm leaves ``temperature`` unset for models whose API rejects it;
    CrewAI only forwards the parameter when it is not None, so nothing reaches
    the API request."""

    def test_manager_default_model_omits_temperature(self, monkeypatch):
        monkeypatch.setattr("ad_seller.llm.get_settings", lambda: _settings())
        llm = build_llm(model="anthropic/claude-opus-4-8", temperature=0.3, max_tokens=4096)
        assert llm.temperature is None

    def test_omitted_temperature_stays_out_of_request_params(self, monkeypatch):
        monkeypatch.setattr("ad_seller.llm.get_settings", lambda: _settings())
        llm = build_llm(model="anthropic/claude-opus-4-8", temperature=0.3, max_tokens=4096)
        params = llm._prepare_completion_params(messages=[{"role": "user", "content": "hi"}])
        assert "temperature" not in params

    def test_supported_model_keeps_temperature(self, monkeypatch):
        monkeypatch.setattr("ad_seller.llm.get_settings", lambda: _settings())
        llm = build_llm(
            model="anthropic/claude-sonnet-4-5-20250929",
            temperature=0.3,
            max_tokens=4096,
        )
        assert llm.temperature == 0.3
        params = llm._prepare_completion_params(messages=[{"role": "user", "content": "hi"}])
        assert params["temperature"] == 0.3

    def test_openai_compatible_endpoint_also_omits(self, monkeypatch):
        monkeypatch.setattr(
            "ad_seller.llm.get_settings",
            lambda: _settings(openai_compatible_llm_api_base_url="http://localhost:11434/v1"),
        )
        llm = build_llm(model="claude-opus-4-8", temperature=0.3, max_tokens=4096)
        assert llm.provider == "openai"
        assert llm.temperature is None
