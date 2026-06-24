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

VALID_MODES = ["all", "mcp", "http", "crew", "chat"]
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
        assert "staging_aamp_seller_mcp" in content
        assert "staging_aamp_seller_http" in content

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
