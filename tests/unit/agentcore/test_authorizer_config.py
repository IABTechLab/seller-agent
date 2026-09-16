# Donated to IAB Tech Lab

"""Tests for the CUSTOM_JWT authorizer-config builder.

Validates: Requirements 1, 3 (authorizer-config builder, Cognito vs BYO branches).
Enterprise-auth-gateway tasks 2.3 / 7.2.
"""

import importlib.util
from pathlib import Path

import pytest

# Load the builder module directly from infra/ (not on the package path).
_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "infra"
    / "aws"
    / "agentcore"
    / "authorizer_config.py"
)
_spec = importlib.util.spec_from_file_location("authorizer_config", _MODULE_PATH)
authorizer_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(authorizer_config)
build_authorizer_config = authorizer_config.build_authorizer_config


COGNITO_DISCOVERY = (
    "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_EXAMPLE01/"
    ".well-known/openid-configuration"
)
BYO_DISCOVERY = "https://idp.example.com/.well-known/openid-configuration"


class TestCognitoBranch:
    """Cognito outputs → authorizer config."""

    def test_builds_with_client_and_scope(self):
        cfg = build_authorizer_config(
            COGNITO_DISCOVERY, "exampleclientid123", "seller-agent/invoke"
        )
        auth = cfg["customJWTAuthorizer"]
        assert auth["discoveryUrl"] == COGNITO_DISCOVERY
        assert auth["allowedClients"] == ["exampleclientid123"]
        assert auth["allowedScopes"] == ["seller-agent/invoke"]

    def test_no_allowed_audience(self):
        """client_credentials tokens have no aud — allowedAudience must be absent."""
        cfg = build_authorizer_config(
            COGNITO_DISCOVERY, "client1", "seller-agent/invoke"
        )
        assert "allowedAudience" not in cfg["customJWTAuthorizer"]


class TestByoIdpBranch:
    """BYO-IdP inputs → authorizer config (multiple clients/scopes)."""

    def test_builds_with_multiple_clients_and_scopes(self):
        cfg = build_authorizer_config(
            BYO_DISCOVERY, "clientA,clientB", "api/invoke,api/read"
        )
        auth = cfg["customJWTAuthorizer"]
        assert auth["discoveryUrl"] == BYO_DISCOVERY
        assert auth["allowedClients"] == ["clientA", "clientB"]
        assert auth["allowedScopes"] == ["api/invoke", "api/read"]

    def test_accepts_iterable_inputs(self):
        cfg = build_authorizer_config(BYO_DISCOVERY, ["c1"], ["s1"])
        assert cfg["customJWTAuthorizer"]["allowedClients"] == ["c1"]


class TestValidationAndNormalization:
    def test_empty_discovery_url_raises(self):
        with pytest.raises(ValueError):
            build_authorizer_config("", "client1", "scope1")

    def test_no_clients_or_scopes_raises(self):
        with pytest.raises(ValueError):
            build_authorizer_config(COGNITO_DISCOVERY, "", "")

    def test_scope_only_is_allowed(self):
        cfg = build_authorizer_config(COGNITO_DISCOVERY, "", "seller-agent/invoke")
        auth = cfg["customJWTAuthorizer"]
        assert "allowedClients" not in auth
        assert auth["allowedScopes"] == ["seller-agent/invoke"]

    def test_clients_only_is_allowed(self):
        cfg = build_authorizer_config(COGNITO_DISCOVERY, "client1", "")
        auth = cfg["customJWTAuthorizer"]
        assert auth["allowedClients"] == ["client1"]
        assert "allowedScopes" not in auth

    def test_csv_whitespace_and_blanks_stripped(self):
        cfg = build_authorizer_config(BYO_DISCOVERY, " a , , b ", " x ,y ")
        auth = cfg["customJWTAuthorizer"]
        assert auth["allowedClients"] == ["a", "b"]
        assert auth["allowedScopes"] == ["x", "y"]
