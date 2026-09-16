# Donated to IAB Tech Lab

"""Tests for the self-sustaining Bedrock bearer-token helper (Req 11)."""

import os
import sys
import types

import pytest

from ad_seller.llm.bedrock_token import (
    ensure_bedrock_token,
    refresh_bedrock_token,
)

_KEY = "ANTHROPIC_COMPATIBLE_LLM_API_KEY"
_URL = "ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL"
_BEDROCK_URL = "https://bedrock-runtime.us-west-2.amazonaws.com/anthropic"
_REAL_ANTHROPIC = "https://api.anthropic.com"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(_KEY, raising=False)
    monkeypatch.delenv(_URL, raising=False)
    yield


def _install_fake_generator(monkeypatch, token="bedrock-api-key-FRESH", raises=False):
    mod = types.ModuleType("aws_bedrock_token_generator")

    def provide_token(*args, **kwargs):
        if raises:
            raise RuntimeError("no creds")
        return token

    mod.provide_token = provide_token
    monkeypatch.setitem(sys.modules, "aws_bedrock_token_generator", mod)


class TestBedrockBaseUrlIsAuthoritative:
    def test_mints_when_no_key(self, monkeypatch):
        monkeypatch.setenv(_URL, _BEDROCK_URL)
        _install_fake_generator(monkeypatch, token="bedrock-api-key-FRESH")
        assert ensure_bedrock_token("us-west-2") is True
        assert os.environ[_KEY] == "bedrock-api-key-FRESH"

    def test_overwrites_a_stale_baked_key(self, monkeypatch):
        """A baked/retained (possibly expired) key MUST NOT shadow a fresh mint."""
        monkeypatch.setenv(_URL, _BEDROCK_URL)
        monkeypatch.setenv(_KEY, "stale-expired-token")
        _install_fake_generator(monkeypatch, token="bedrock-api-key-FRESH")
        assert ensure_bedrock_token("us-west-2") is True
        assert os.environ[_KEY] == "bedrock-api-key-FRESH"  # overwritten

    def test_mint_failure_keeps_prior_value_and_does_not_crash(self, monkeypatch):
        monkeypatch.setenv(_URL, _BEDROCK_URL)
        monkeypatch.setenv(_KEY, "prior")
        _install_fake_generator(monkeypatch, raises=True)
        # Returns True because a (prior) key is still present; never raises.
        assert ensure_bedrock_token("us-west-2") is True
        assert os.environ[_KEY] == "prior"

    def test_generator_missing_is_graceful(self, monkeypatch):
        monkeypatch.setenv(_URL, _BEDROCK_URL)
        monkeypatch.setitem(sys.modules, "aws_bedrock_token_generator", None)
        assert ensure_bedrock_token("us-west-2") is False
        assert _KEY not in os.environ


class TestNonBedrockRespectsExistingKey:
    def test_real_anthropic_keeps_operator_key(self, monkeypatch):
        monkeypatch.setenv(_URL, _REAL_ANTHROPIC)
        monkeypatch.setenv(_KEY, "operator-supplied")
        _install_fake_generator(monkeypatch, token="SHOULD-NOT-BE-USED")
        assert ensure_bedrock_token() is True
        assert os.environ[_KEY] == "operator-supplied"  # never overwritten

    def test_real_anthropic_no_key_does_not_mint(self, monkeypatch):
        monkeypatch.setenv(_URL, _REAL_ANTHROPIC)
        _install_fake_generator(monkeypatch)
        assert ensure_bedrock_token() is False
        assert _KEY not in os.environ


class TestNoBaseUrl:
    def test_no_base_url_no_mint(self, monkeypatch):
        _install_fake_generator(monkeypatch)
        assert ensure_bedrock_token() is False
        assert _KEY not in os.environ

    def test_no_base_url_reports_existing_key(self, monkeypatch):
        monkeypatch.setenv(_KEY, "preset")
        assert ensure_bedrock_token() is True


class TestRefresh:
    def test_refresh_remints_on_bedrock_url(self, monkeypatch):
        monkeypatch.setenv(_URL, _BEDROCK_URL)
        monkeypatch.setenv(_KEY, "old-token")
        _install_fake_generator(monkeypatch, token="bedrock-api-key-REFRESHED")
        assert refresh_bedrock_token("us-west-2") is True
        assert os.environ[_KEY] == "bedrock-api-key-REFRESHED"

    def test_refresh_noop_on_non_bedrock_url(self, monkeypatch):
        monkeypatch.setenv(_URL, _REAL_ANTHROPIC)
        monkeypatch.setenv(_KEY, "keep")
        _install_fake_generator(monkeypatch, token="SHOULD-NOT-BE-USED")
        assert refresh_bedrock_token() is False
        assert os.environ[_KEY] == "keep"

    def test_refresh_graceful_on_mint_failure(self, monkeypatch):
        monkeypatch.setenv(_URL, _BEDROCK_URL)
        monkeypatch.setenv(_KEY, "old")
        _install_fake_generator(monkeypatch, raises=True)
        assert refresh_bedrock_token("us-west-2") is False
        assert os.environ[_KEY] == "old"
