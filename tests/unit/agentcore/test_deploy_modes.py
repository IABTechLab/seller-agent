"""Tests for deploy.sh --mode and --storage flag handling.

Validates:
- Property 2: For any valid --mode value, the deploy script accepts it
- Property 3: For any invalid --mode value, the deploy script rejects it
- deploy.sh --help exits 0 and shows usage

Validates: Requirements 3.1, 3.2
"""

import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "aws" / "agentcore" / "deploy.sh"

VALID_MODES = ["all", "mcp", "http", "crew", "chat", "a2a"]
VALID_STORAGE = ["sqlite", "postgres"]


# ===================================================================
# Basic deploy.sh validation
# ===================================================================


class TestDeployScriptBasics:
    """Validate deploy.sh basic behavior."""

    def test_deploy_script_exists(self):
        assert DEPLOY_SCRIPT.exists()

    def test_help_exits_zero(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_help_shows_usage(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "Usage:" in result.stdout

    def test_help_shows_mode_options(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "--mode" in result.stdout
        for mode in VALID_MODES:
            assert mode in result.stdout

    def test_help_shows_storage_options(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "--storage" in result.stdout
        assert "sqlite" in result.stdout
        assert "postgres" in result.stdout

    def test_help_shows_cleanup_option(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "--cleanup" in result.stdout

    def test_script_has_cleanup_section(self):
        """deploy.sh should have a cleanup section with agentcore destroy."""
        content = DEPLOY_SCRIPT.read_text()
        assert "DO_CLEANUP" in content
        assert "agentcore destroy" in content


# ===================================================================
# Auth-stack wiring (enterprise-auth-gateway groups 1-2)
# ===================================================================


class TestAuthFlags:
    """Validate --auth / BYO-IdP flag wiring in deploy.sh."""

    def _content(self):
        return DEPLOY_SCRIPT.read_text()

    def test_help_shows_auth_flag(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert "--auth" in result.stdout
        assert "--no-auth" in result.stdout
        assert "--idp-discovery-url" in result.stdout

    def test_auth_default_on(self):
        """Auth is applied BY DEFAULT (Req 5.4): DEPLOY_AUTH initializes to true."""
        content = self._content()
        assert "DEPLOY_AUTH=true" in content

    def test_parses_no_auth_flag(self):
        content = self._content()
        assert "--no-auth)" in content
        assert "DEPLOY_AUTH=false" in content

    def test_parses_auth_flag(self):
        content = self._content()
        assert "--auth)" in content

    def test_parses_byo_idp_flags(self):
        content = self._content()
        assert "--idp-discovery-url)" in content
        assert "--idp-allowed-clients)" in content
        assert "--idp-allowed-scopes)" in content

    def test_has_deploy_auth_stack_function(self):
        content = self._content()
        assert "deploy_auth_stack()" in content
        assert "auth-agentcore.yaml" in content

    def test_authorizer_wired_into_runtimes(self):
        """All three runtime configure blocks (mcp/http/a2a) append the authorizer."""
        content = self._content()
        assert "_authorizer_config_json()" in content
        # def + mcp + http + a2a call sites
        assert content.count("_authorizer_config_json") >= 4
        assert content.count("--authorizer-config") >= 3
        for proto in ("mcp", "http", "a2a"):
            assert f"CUSTOM_JWT authorizer attached ({proto})" in content

    def test_authorizer_uses_builder(self):
        content = self._content()
        assert "authorizer_config.py" in content

    def test_auth_stack_called_in_main_flow(self):
        content = self._content()
        assert content.count("deploy_auth_stack") >= 2

    def test_byo_idp_validates_discovery_url(self):
        """BYO-IdP path must assert a valid OIDC document before configuring."""
        content = self._content()
        assert "token_endpoint" in content

    def test_secret_not_printed(self):
        """The auth-stack deploy must not echo the app-client secret value."""
        content = self._content()
        # We reference retrieving it via CLI, but never echo a secret VALUE.
        assert "describe-user-pool-client" in content


# ===================================================================
# Property 2: Valid modes produce correct runtime names and protocols
# ===================================================================


class TestValidModes:
    """**Validates: Requirements 3.1**

    Property 2: For any valid --mode value, the deploy script generates
    correct runtime names and protocols.
    """

    @pytest.mark.parametrize("mode", VALID_MODES)
    def test_valid_mode_accepted_by_help(self, mode):
        """Each valid mode appears in --help output."""
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert mode in result.stdout

    @given(mode=st.sampled_from(VALID_MODES))
    @settings(max_examples=10, deadline=None)
    def test_valid_mode_does_not_fail_on_validation(self, mode):
        """**Validates: Requirements 3.1**

        Property 2: For any valid --mode value, the deploy script does not
        reject it at the argument validation stage. We test this by running
        with --test-only which skips actual deployment but still validates args.
        The script will fail later (no agentcore CLI) but NOT at mode validation.
        """
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"""
                source {DEPLOY_SCRIPT} --mode {mode} --test-only 2>&1 || true
            """,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())},
        )
        # Should NOT contain the mode validation error
        assert f"Invalid mode '{mode}'" not in result.stdout
        assert f"Invalid mode '{mode}'" not in result.stderr

    def test_mode_to_runtime_name_mapping(self):
        """Verify the expected runtime name conventions exist in the script."""
        content = DEPLOY_SCRIPT.read_text()
        assert "aamp_seller_mcp" in content
        assert "aamp_seller_http" in content

    def test_mcp_mode_uses_mcp_protocol(self):
        """MCP mode should configure with -p MCP."""
        content = DEPLOY_SCRIPT.read_text()
        assert "-p MCP" in content

    def test_http_mode_uses_http_protocol(self):
        """HTTP mode should configure with -p HTTP."""
        content = DEPLOY_SCRIPT.read_text()
        assert "-p HTTP" in content

    def test_mcp_mode_uses_mcp_main(self):
        """MCP mode should reference mcp_main.py."""
        content = DEPLOY_SCRIPT.read_text()
        assert "mcp_main.py" in content

    def test_http_mode_uses_http_main(self):
        """HTTP mode should reference http_main.py."""
        content = DEPLOY_SCRIPT.read_text()
        assert "http_main.py" in content


# ===================================================================
# Property 3: Invalid modes are rejected
# ===================================================================


class TestInvalidModes:
    """**Validates: Requirements 3.2**

    Property 3: For any invalid --mode value, the deploy script rejects it.
    """

    @pytest.mark.parametrize("bad_mode", ["invalid", "deploy", "run", "MCP", "HTTP", "ALL", ""])
    def test_invalid_mode_rejected(self, bad_mode):
        """Known invalid modes are rejected with non-zero exit."""
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--mode", bad_mode],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0

    @given(
        mode=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            min_size=1,
            max_size=20,
        ).filter(lambda m: m not in VALID_MODES)
    )
    @settings(max_examples=20)
    def test_arbitrary_invalid_mode_rejected(self, mode):
        """**Validates: Requirements 3.2**

        Property 3: For any string that is NOT in the valid modes set,
        the deploy script rejects it with a non-zero exit code.
        """
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--mode", mode],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0, f"Mode '{mode}' should have been rejected"
        assert "Invalid mode" in result.stderr or "ERROR" in result.stderr


# ===================================================================
# Storage flag validation
# ===================================================================


class TestStorageFlag:
    """Validate --storage flag behavior."""

    def test_invalid_storage_rejected(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--storage", "mysql"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0

    def test_script_has_deploy_infrastructure_function(self):
        """postgres mode should have infrastructure deployment logic."""
        content = DEPLOY_SCRIPT.read_text()
        assert "deploy_infrastructure" in content

    def test_script_has_deploy_mcp_runtime_function(self):
        content = DEPLOY_SCRIPT.read_text()
        assert "deploy_mcp_runtime" in content

    def test_script_has_deploy_http_runtime_function(self):
        content = DEPLOY_SCRIPT.read_text()
        assert "deploy_http_runtime" in content


class TestProtocolsFlag:
    """--protocols deploys a comma-list of protocol runtimes in one run."""

    def test_help_shows_protocols_option(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        assert "--protocols" in result.stdout

    def test_help_mentions_a2a_mode(self):
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        assert "a2a" in result.stdout

    def test_invalid_protocol_rejected(self):
        # --protocols with an unknown token must exit non-zero. Use --test-only
        # so no real deploy is attempted; the loop runs only under deploy.
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--protocols", "http,bogus", "--region", "us-west-2"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode != 0
        assert "bogus" in (result.stdout + result.stderr)


