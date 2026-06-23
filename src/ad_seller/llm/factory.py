# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Provider-agnostic LLM construction.

CrewAI ships native provider SDKs (openai, anthropic, azure, bedrock, gemini,
openai_compatible) and routes by the "<provider>/<model>" prefix. Any provider
works by setting the model and llm_api_key — no third-party router. For
OpenAI-compatible endpoints (NVIDIA NIM, Ollama, vLLM, ...) set llm_api_base and
the request is sent over the native OpenAI-protocol client.
"""

from enum import Enum
from functools import lru_cache
from typing import Optional

from crewai import LLM

from ..config import Settings, get_settings

# CrewAI's native OpenAI SDK client; with a base_url it drives any endpoint that
# speaks the OpenAI wire format (NVIDIA NIM, Ollama, vLLM, Groq, ...).
_OPENAI_COMPATIBLE_PROVIDER = "openai"


class LLMRole(str, Enum):
    """Which configured model a caller wants."""

    DEFAULT = "default"
    MANAGER = "manager"


class LLMFactory:
    """Builds :class:`crewai.LLM` instances from application settings."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    def create(
        self,
        role: LLMRole = LLMRole.DEFAULT,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLM:
        """Create an ``LLM`` for the given role."""
        settings = self._settings
        kwargs: dict[str, object] = {
            "model": self._model_for_role(role),
            "temperature": (
                temperature if temperature is not None else settings.llm_temperature
            ),
            "max_tokens": (
                max_tokens if max_tokens is not None else settings.llm_max_tokens
            ),
        }
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if settings.llm_api_version:
            kwargs["api_version"] = settings.llm_api_version

        # A custom endpoint means an OpenAI-compatible provider; pin the native
        # provider explicitly so routing never falls back off the native path.
        if settings.llm_api_base:
            kwargs["provider"] = _OPENAI_COMPATIBLE_PROVIDER
            kwargs["base_url"] = settings.llm_api_base

        return LLM(**kwargs)

    def _model_for_role(self, role: LLMRole) -> str:
        if role is LLMRole.MANAGER:
            return self._settings.manager_llm_model
        return self._settings.default_llm_model


@lru_cache
def _get_factory() -> LLMFactory:
    return LLMFactory()


def get_llm(
    role: LLMRole = LLMRole.DEFAULT,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLM:
    """Return a configured ``LLM`` for the given role."""
    return _get_factory().create(role=role, temperature=temperature, max_tokens=max_tokens)
