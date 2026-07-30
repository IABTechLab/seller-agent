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

from typing import Any

from crewai import LLM

from ..config import get_settings

# CrewAI's native OpenAI SDK client; with a base_url it drives any endpoint
# that speaks the OpenAI wire format (NVIDIA NIM, Ollama, HuggingFace TGI, ...).
_OPENAI_COMPATIBLE_PROVIDER = "openai"

# Claude families whose API rejects the ``temperature`` parameter
# (``400 invalid_request_error: 'temperature' is deprecated for this model.``):
# Opus 4.7+, Sonnet 5+, and Fable/Mythos. Matched as substrings so any
# provider prefix (``anthropic/``, ``bedrock/us.anthropic.`` ...) and version
# or date suffix is covered.
_TEMPERATURE_UNSUPPORTED_FAMILIES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-fable",
    "claude-mythos",
)


def _model_accepts_temperature(model: str) -> bool:
    """Whether ``model``'s API still accepts the ``temperature`` parameter.

    Anthropic removed ``temperature`` on the families listed in
    ``_TEMPERATURE_UNSUPPORTED_FAMILIES`` — sending it returns a 400. The
    check is case-insensitive and ignores any provider prefix in the id.
    """
    model_id = model.lower()
    return not any(family in model_id for family in _TEMPERATURE_UNSUPPORTED_FAMILIES)


def build_llm(model: str, temperature: float, max_tokens: int) -> LLM:
    """Build an ``LLM`` for ``model``, honoring a custom base URL if configured.

    ``temperature`` is omitted entirely for models whose API rejects it
    (see ``_model_accepts_temperature``); CrewAI only forwards the parameter
    to the provider when it is set, so omission keeps it out of the request.
    """
    settings = get_settings()

    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
    if _model_accepts_temperature(model):
        kwargs["temperature"] = temperature

    if settings.openai_compatible_llm_api_base_url:
        return LLM(
            api_key=settings.openai_compatible_llm_api_key,
            provider=_OPENAI_COMPATIBLE_PROVIDER,
            base_url=settings.openai_compatible_llm_api_base_url,
            **kwargs,
        )

    return LLM(**kwargs)
