# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""LLM construction shared by all agents.

The named providers (Anthropic, OpenAI, Gemini, Bedrock) are switched by
setting DEFAULT_LLM_MODEL / MANAGER_LLM_MODEL to a "<provider>/<model>"
string plus that provider's API key, per docs/guides/configuration.md — no
code here is involved in that path.

This module adds one more alternative: any OpenAI-wire-compatible endpoint
that has no CrewAI native provider prefix of its own (NVIDIA NIM, Ollama,
HuggingFace TGI, vLLM, ...). Setting OPENAI_COMPATIBLE_LLM_API_BASE_URL pins
the request to CrewAI's native OpenAI-compatible client regardless of the
model id's shape, using the raw model id the endpoint expects for
DEFAULT_LLM_MODEL / MANAGER_LLM_MODEL. Leaving
OPENAI_COMPATIBLE_LLM_API_BASE_URL unset keeps today's behavior exactly as-is.
"""

from crewai import LLM

from ..config import get_settings

# CrewAI's native OpenAI SDK client; with a base_url it drives any endpoint
# that speaks the OpenAI wire format (NVIDIA NIM, Ollama, HuggingFace TGI, ...).
_OPENAI_COMPATIBLE_PROVIDER = "openai"


def build_llm(model: str, temperature: float, max_tokens: int) -> LLM:
    """Build an ``LLM`` for ``model``, honoring a custom base URL if configured."""
    settings = get_settings()

    if settings.openai_compatible_llm_api_base_url:
        return LLM(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=settings.openai_compatible_llm_api_key,
            provider=_OPENAI_COMPATIBLE_PROVIDER,
            base_url=settings.openai_compatible_llm_api_base_url,
        )

    return LLM(model=model, temperature=temperature, max_tokens=max_tokens)
