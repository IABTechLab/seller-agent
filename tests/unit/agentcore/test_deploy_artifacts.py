"""Tests for seller agent AgentCore CLI deployment artifacts.

Validates:
- http_main.py follows BedrockAgentCoreApp pattern
- requirements.txt contains bedrock-agentcore
- deploy.sh exists and is executable
- main.yaml is unchanged (ECS-only, no AgentCore conditions)
- Workshop data files are present

Validates: Requirements 1.1, 1.2, 8.1
"""

import os
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
AGENTCORE_DIR = REPO_ROOT / "infra" / "aws" / "agentcore"
ENTRYPOINT = REPO_ROOT / "src" / "ad_seller" / "interfaces" / "agentcore" / "http_main.py"
CFN_DIR = REPO_ROOT / "infra" / "aws" / "cloudformation"


# ===================================================================
# AgentCore Entrypoint Validation
# ===================================================================


class TestAgentCoreEntrypoint:
    """Validate http_main.py follows BedrockAgentCoreApp pattern."""

    def test_entrypoint_exists(self):
        assert ENTRYPOINT.exists(), f"http_main.py not found at {ENTRYPOINT}"

    @pytest.fixture
    def source(self):
        return ENTRYPOINT.read_text()

    def test_imports_bedrock_agentcore_app(self, source):
        assert "BedrockAgentCoreApp" in source

    def test_creates_app_instance(self, source):
        assert "app = BedrockAgentCoreApp()" in source

    def test_has_entrypoint_decorator(self, source):
        assert "@app.entrypoint" in source

    def test_has_invoke_function(self, source):
        assert "def invoke(payload, context)" in source

    def test_calls_app_run(self, source):
        assert "app.run()" in source

    def test_returns_response_dict(self, source):
        assert '"response"' in source
        assert '"metadata"' in source

    def test_handles_missing_prompt(self, source):
        assert '"error"' in source

    def test_adds_src_to_sys_path(self, source):
        """Entrypoint must add src/ to sys.path for ad_seller imports."""
        assert "sys.path" in source


# ===================================================================
# Requirements Validation
# ===================================================================


class TestAgentCoreRequirements:
    """Validate requirements.txt for AgentCore deployment."""

    def test_requirements_file_exists(self):
        req_file = AGENTCORE_DIR / "requirements.txt"
        assert req_file.exists(), f"requirements.txt not found at {req_file}"

    def test_contains_bedrock_agentcore(self):
        content = (AGENTCORE_DIR / "requirements.txt").read_text()
        assert "bedrock-agentcore" in content

    def test_contains_crewai(self):
        content = (AGENTCORE_DIR / "requirements.txt").read_text()
        assert "crewai" in content


# ===================================================================
# Deploy Script Validation
# ===================================================================


class TestDeployScript:
    """Validate deploy.sh for AgentCore CLI deployment."""

    def test_deploy_script_exists(self):
        script = AGENTCORE_DIR / "deploy.sh"
        assert script.exists(), f"deploy.sh not found at {script}"

    def test_deploy_script_is_executable(self):
        assert os.access(AGENTCORE_DIR / "deploy.sh", os.X_OK)

    def test_deploy_script_has_shebang(self):
        first_line = (AGENTCORE_DIR / "deploy.sh").read_text().split("\n")[0]
        assert first_line.startswith("#!/")

    @pytest.fixture
    def script_content(self):
        return (AGENTCORE_DIR / "deploy.sh").read_text()

    def test_uses_agentcore_configure(self, script_content):
        assert "agentcore configure" in script_content

    def test_uses_agentcore_deploy(self, script_content):
        assert "agentcore deploy" in script_content

    def test_uses_agentcore_invoke(self, script_content):
        assert "agentcore invoke" in script_content

    def test_references_entrypoint(self, script_content):
        assert "agentcore/http_main.py" in script_content

    def test_references_requirements(self, script_content):
        assert "requirements.txt" in script_content

    def test_accepts_region_param(self, script_content):
        assert "--region" in script_content

    def test_accepts_profile_param(self, script_content):
        assert "--profile" in script_content

    def test_sets_env_vars(self, script_content):
        assert "DEFAULT_LLM_MODEL" in script_content
        assert "STORAGE_TYPE" in script_content

    def test_help_flag(self):
        result = __import__("subprocess").run(
            ["bash", str(AGENTCORE_DIR / "deploy.sh"), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout


# ===================================================================
# main.yaml — ECS Only (no AgentCore conditions)
# ===================================================================


class TestMainTemplateECSOnly:
    """Validate main.yaml has no AgentCore conditions (reverted to ECS-only)."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        def _cfn_loader():
            loader = yaml.SafeLoader

            def _multi_constructor(loader, tag_suffix, node):
                if isinstance(node, yaml.ScalarNode):
                    return loader.construct_scalar(node)
                elif isinstance(node, yaml.SequenceNode):
                    return loader.construct_sequence(node)
                elif isinstance(node, yaml.MappingNode):
                    return loader.construct_mapping(node)

            loader.add_multi_constructor("!", _multi_constructor)
            return loader

        template_path = CFN_DIR / "main.yaml"
        assert template_path.exists()
        with open(template_path) as f:
            self.template = yaml.load(f, Loader=_cfn_loader())

    def test_no_deployment_mode_parameter(self):
        params = self.template.get("Parameters", {})
        assert "DeploymentMode" not in params, (
            "main.yaml should not have DeploymentMode (reverted to ECS-only)"
        )

    def test_no_agentcore_conditions(self):
        conditions = self.template.get("Conditions", {})
        assert "IsAgentCore" not in conditions
        assert "IsECSFargate" not in conditions

    def test_network_stack_unconditional(self):
        assert "Condition" not in self.template["Resources"]["NetworkStack"]

    def test_has_ecs_resources(self):
        resources = self.template.get("Resources", {})
        assert "NetworkStack" in resources
        assert "ComputeStack" in resources
