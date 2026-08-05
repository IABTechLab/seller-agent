# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CrewAI memory configuration coherence (seller#42).

With the previously shipped defaults (Anthropic-only key,
CREW_MEMORY_ENABLED=true), CrewAI memory wanted an OpenAI embedder that
nothing configured — every search_memory call failed with "The
CHROMA_OPENAI_API_KEY environment variable is not set" while memory
silently ran disabled. These tests cover the fix:

- CREW_MEMORY_ENABLED defaults to false.
- Enabling it without any usable embedder configuration yields exactly ONE
  startup warning and memory disabled for the process (instead of per-call
  failures).
- Configurations with a usable embedder (OPENAI_API_KEY,
  CHROMA_OPENAI_API_KEY, or the AgentCore path via
  BEDROCK_AGENTCORE_MEMORY_ID) keep memory enabled — in particular the
  AgentCore path, which has its own storage backend in
  patches/crewai_agentcore_memory.py and must not be disabled here.
"""

import logging

import pytest

from ad_seller.config.settings import Settings

SETTINGS_LOGGER = "ad_seller.config.settings"

_MEMORY_ENV_VARS = (
    "CREW_MEMORY_ENABLED",
    "OPENAI_API_KEY",
    "CHROMA_OPENAI_API_KEY",
    "BEDROCK_AGENTCORE_MEMORY_ID",
)


@pytest.fixture(autouse=True)
def clean_memory_env(monkeypatch):
    """Strip every env var the memory validation consults."""
    for var in _MEMORY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _settings(**kwargs) -> Settings:
    """Build Settings without reading any local .env file."""
    return Settings(_env_file=None, **kwargs)


def _memory_warnings(caplog) -> list[logging.LogRecord]:
    return [
        rec
        for rec in caplog.records
        if rec.name == SETTINGS_LOGGER
        and rec.levelno == logging.WARNING
        and "CREW_MEMORY_ENABLED" in rec.getMessage()
    ]


class TestDefaultOff:
    """Memory is off by default until a provider-neutral embedder is wired."""

    def test_default_is_disabled(self):
        assert _settings().crew_memory_enabled is False

    def test_default_emits_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            _settings()
        assert _memory_warnings(caplog) == []

    def test_explicit_false_emits_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            settings = _settings(crew_memory_enabled=False)
        assert settings.crew_memory_enabled is False
        assert _memory_warnings(caplog) == []


class TestEnabledWithoutEmbedder:
    """Enabled + no embedder = one startup warning, memory disabled."""

    def test_memory_disabled_for_process(self):
        settings = _settings(crew_memory_enabled=True)
        assert settings.crew_memory_enabled is False

    def test_single_startup_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            _settings(crew_memory_enabled=True)
        assert len(_memory_warnings(caplog)) == 1

    def test_warning_explains_what_is_needed(self, caplog):
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            _settings(crew_memory_enabled=True)
        (record,) = _memory_warnings(caplog)
        message = record.getMessage()
        # The warning must name every way to actually enable memory and the
        # per-call failure it replaces.
        assert "OPENAI_API_KEY" in message
        assert "CHROMA_OPENAI_API_KEY" in message
        assert "BEDROCK_AGENTCORE_MEMORY_ID" in message
        assert "disabl" in message.lower()

    def test_env_var_enabled_without_embedder_is_disabled(self, monkeypatch, caplog):
        monkeypatch.setenv("CREW_MEMORY_ENABLED", "true")
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            settings = _settings()
        assert settings.crew_memory_enabled is False
        assert len(_memory_warnings(caplog)) == 1


class TestEnabledWithEmbedder:
    """A usable embedder configuration keeps memory enabled, warning-free."""

    def test_openai_api_key_keeps_memory_enabled(self, caplog):
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            settings = _settings(crew_memory_enabled=True, openai_api_key="sk-test")
        assert settings.crew_memory_enabled is True
        assert _memory_warnings(caplog) == []

    def test_chroma_openai_api_key_keeps_memory_enabled(self, monkeypatch, caplog):
        monkeypatch.setenv("CHROMA_OPENAI_API_KEY", "sk-test")
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            settings = _settings(crew_memory_enabled=True)
        assert settings.crew_memory_enabled is True
        assert _memory_warnings(caplog) == []


class TestAgentCorePathUnaffected:
    """BEDROCK_AGENTCORE_MEMORY_ID has its own embedder story (Nova Lite via
    patches/crewai_agentcore_memory.py) and must survive validation intact."""

    def test_agentcore_memory_id_keeps_memory_enabled(self, monkeypatch, caplog):
        monkeypatch.setenv("BEDROCK_AGENTCORE_MEMORY_ID", "mem-abc123")
        monkeypatch.setenv("CREW_MEMORY_ENABLED", "true")
        with caplog.at_level(logging.WARNING, logger=SETTINGS_LOGGER):
            settings = _settings()
        assert settings.crew_memory_enabled is True
        assert _memory_warnings(caplog) == []
