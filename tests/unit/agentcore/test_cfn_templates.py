"""Tests for AgentCore CloudFormation templates.

Validates:
- agentcore-network.yaml and main-agentcore.yaml are valid YAML
- Templates have required Parameters, Resources, Outputs sections
- agentcore-network.yaml has AgentCoreSecurityGroup, ingress rules, VPC endpoints
- main-agentcore.yaml has NetworkStack, StorageStack, AgentCoreNetworkStack
- Zero git diff on infra/aws/cloudformation/ (existing files untouched)

Validates: Requirements 3.1, 3.2, 3.3
"""

import subprocess
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
AGENTCORE_DIR = REPO_ROOT / "infra" / "aws" / "agentcore"
CFN_DIR = REPO_ROOT / "infra" / "aws" / "cloudformation"

AGENTCORE_NETWORK = AGENTCORE_DIR / "agentcore-network.yaml"
MAIN_AGENTCORE = AGENTCORE_DIR / "main-agentcore.yaml"


# ---------------------------------------------------------------------------
# YAML loader that handles CloudFormation intrinsic functions
# ---------------------------------------------------------------------------
def cfn_loader():
    """Create a YAML loader that handles CloudFormation !Ref, !Sub, etc."""
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


def load_cfn_template(path: Path) -> dict:
    """Load a CloudFormation template, handling intrinsic functions."""
    with open(path) as f:
        return yaml.load(f, Loader=cfn_loader())


# ===================================================================
# agentcore-network.yaml validation
# ===================================================================


class TestAgentCoreNetworkTemplate:
    """Validate agentcore-network.yaml structure and resources."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        assert AGENTCORE_NETWORK.exists(), f"Not found: {AGENTCORE_NETWORK}"
        self.template = load_cfn_template(AGENTCORE_NETWORK)

    def test_is_valid_yaml(self):
        """Template parses as valid YAML."""
        assert self.template is not None

    def test_has_aws_template_format_version(self):
        assert "AWSTemplateFormatVersion" in self.template

    def test_has_description(self):
        assert "Description" in self.template

    def test_has_parameters_section(self):
        assert "Parameters" in self.template

    def test_has_resources_section(self):
        assert "Resources" in self.template

    def test_has_outputs_section(self):
        assert "Outputs" in self.template

    # -- Parameters --
    def test_has_required_parameters(self):
        params = self.template["Parameters"]
        required = [
            "Environment",
            "VpcId",
            "PrivateSubnet1Id",
            "PrivateSubnet2Id",
            "PrivateRouteTableId",
            "DatabaseSecurityGroupId",
            "RedisSecurityGroupId",
        ]
        for param in required:
            assert param in params, f"Missing parameter: {param}"

    # -- Resources --
    def test_has_agentcore_security_group(self):
        resources = self.template["Resources"]
        assert "AgentCoreSecurityGroup" in resources
        assert resources["AgentCoreSecurityGroup"]["Type"] == "AWS::EC2::SecurityGroup"

    def test_has_aurora_ingress_rule(self):
        resources = self.template["Resources"]
        assert "AuroraIngressFromAgentCore" in resources
        assert resources["AuroraIngressFromAgentCore"]["Type"] == "AWS::EC2::SecurityGroupIngress"

    def test_has_redis_ingress_rule(self):
        resources = self.template["Resources"]
        assert "RedisIngressFromAgentCore" in resources
        assert resources["RedisIngressFromAgentCore"]["Type"] == "AWS::EC2::SecurityGroupIngress"

    def test_has_ecr_dkr_endpoint(self):
        resources = self.template["Resources"]
        assert "ECRDkrEndpoint" in resources
        assert resources["ECRDkrEndpoint"]["Type"] == "AWS::EC2::VPCEndpoint"

    def test_has_ecr_api_endpoint(self):
        resources = self.template["Resources"]
        assert "ECRApiEndpoint" in resources

    def test_has_s3_gateway_endpoint(self):
        resources = self.template["Resources"]
        assert "S3GatewayEndpoint" in resources

    def test_has_cloudwatch_logs_endpoint(self):
        resources = self.template["Resources"]
        assert "CloudWatchLogsEndpoint" in resources

    # -- Outputs --
    def test_outputs_agentcore_security_group_id(self):
        outputs = self.template["Outputs"]
        assert "AgentCoreSecurityGroupId" in outputs

    # -- Security group egress rules --
    def test_security_group_has_aurora_egress(self):
        sg = self.template["Resources"]["AgentCoreSecurityGroup"]
        egress = sg["Properties"]["SecurityGroupEgress"]
        ports = [r.get("FromPort") for r in egress]
        assert 5432 in ports, "Missing Aurora egress rule (port 5432)"

    def test_security_group_has_redis_egress(self):
        sg = self.template["Resources"]["AgentCoreSecurityGroup"]
        egress = sg["Properties"]["SecurityGroupEgress"]
        ports = [r.get("FromPort") for r in egress]
        assert 6379 in ports, "Missing Redis egress rule (port 6379)"

    def test_security_group_has_https_egress(self):
        sg = self.template["Resources"]["AgentCoreSecurityGroup"]
        egress = sg["Properties"]["SecurityGroupEgress"]
        ports = [r.get("FromPort") for r in egress]
        assert 443 in ports, "Missing HTTPS egress rule (port 443)"


# ===================================================================
# main-agentcore.yaml validation
# ===================================================================


class TestMainAgentCoreTemplate:
    """Validate main-agentcore.yaml structure and resources."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        assert MAIN_AGENTCORE.exists(), f"Not found: {MAIN_AGENTCORE}"
        self.template = load_cfn_template(MAIN_AGENTCORE)

    def test_is_valid_yaml(self):
        assert self.template is not None

    def test_has_aws_template_format_version(self):
        assert "AWSTemplateFormatVersion" in self.template

    def test_has_description(self):
        assert "Description" in self.template

    def test_has_parameters_section(self):
        assert "Parameters" in self.template

    def test_has_resources_section(self):
        assert "Resources" in self.template

    def test_has_outputs_section(self):
        assert "Outputs" in self.template

    # -- Parameters --
    def test_has_required_parameters(self):
        params = self.template["Parameters"]
        required = [
            "Environment",
            "DBMasterPasswordSSMParam",
            "VpcCidr",
        ]
        for param in required:
            assert param in params, f"Missing parameter: {param}"

    # -- Resources: nested stacks --
    def test_has_network_stack(self):
        resources = self.template["Resources"]
        assert "NetworkStack" in resources
        assert resources["NetworkStack"]["Type"] == "AWS::CloudFormation::Stack"

    def test_has_storage_stack(self):
        resources = self.template["Resources"]
        assert "StorageStack" in resources
        assert resources["StorageStack"]["Type"] == "AWS::CloudFormation::Stack"

    def test_has_agentcore_network_stack(self):
        resources = self.template["Resources"]
        assert "AgentCoreNetworkStack" in resources
        assert resources["AgentCoreNetworkStack"]["Type"] == "AWS::CloudFormation::Stack"

    def test_no_compute_stack(self):
        """main-agentcore.yaml should NOT include the ECS compute stack."""
        resources = self.template["Resources"]
        assert "ComputeStack" not in resources

    # -- Outputs --
    def test_has_required_outputs(self):
        outputs = self.template["Outputs"]
        required = [
            "AuroraEndpoint",
            "AuroraPort",
            "RedisEndpoint",
            "RedisPort",
            "AgentCoreSecurityGroupId",
            "PrivateSubnet1Id",
            "PrivateSubnet2Id",
            "VpcId",
        ]
        for output in required:
            assert output in outputs, f"Missing output: {output}"

    # -- Template URLs reference relative paths (cloudformation package rewrites to S3) --
    def test_network_stack_uses_relative_path(self):
        props = self.template["Resources"]["NetworkStack"]["Properties"]
        template_url = str(props.get("TemplateURL", ""))
        assert "network.yaml" in template_url

    def test_storage_stack_uses_relative_path(self):
        props = self.template["Resources"]["StorageStack"]["Properties"]
        template_url = str(props.get("TemplateURL", ""))
        assert "storage.yaml" in template_url

    def test_agentcore_network_stack_uses_relative_path(self):
        props = self.template["Resources"]["AgentCoreNetworkStack"]["Properties"]
        template_url = str(props.get("TemplateURL", ""))
        assert "agentcore-network.yaml" in template_url


# ===================================================================
# Zero git diff on existing CloudFormation files
# ===================================================================


class TestExistingFilesUntouched:
    """Verify zero git diff on infra/aws/cloudformation/ files."""

    def test_cloudformation_dir_no_changes(self):
        """Existing CloudFormation files must have zero git diff."""
        result = subprocess.run(
            ["git", "diff", "--stat", "infra/aws/cloudformation/"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(REPO_ROOT),
        )
        assert result.stdout.strip() == "", (
            f"Unexpected changes in cloudformation/:\n{result.stdout}"
        )

    def test_network_yaml_exists(self):
        assert (CFN_DIR / "network.yaml").exists()

    def test_storage_yaml_exists(self):
        assert (CFN_DIR / "storage.yaml").exists()

    def test_compute_yaml_exists(self):
        assert (CFN_DIR / "compute.yaml").exists()

    def test_main_yaml_exists(self):
        assert (CFN_DIR / "main.yaml").exists()


# ===================================================================
# Dockerfile CMD management
# ===================================================================


class TestUnifiedEntrypoint:
    """Verify deploy.sh uses unified main.py with AGENTCORE_MODE env var."""

    @pytest.fixture
    def deploy_script(self):
        return (AGENTCORE_DIR / "deploy.sh").read_text()

    def test_mcp_deploy_sets_agentcore_mode_mcp(self, deploy_script):
        """MCP deploy should set AGENTCORE_MODE=mcp."""
        assert "AGENTCORE_MODE=mcp" in deploy_script

    def test_http_deploy_sets_agentcore_mode_http(self, deploy_script):
        """HTTP deploy should set AGENTCORE_MODE=http."""
        assert "AGENTCORE_MODE=http" in deploy_script

    def test_mcp_deploy_references_mcp_main(self, deploy_script):
        """MCP deploy should reference mcp_main.py for agentcore configure."""
        assert "mcp_main.py" in deploy_script

    def test_http_deploy_references_http_main(self, deploy_script):
        """HTTP deploy should reference http_main.py for agentcore configure."""
        assert "http_main.py" in deploy_script

    def test_no_set_dockerfile_cmd(self, deploy_script):
        """Unified main.py eliminates the need for _set_dockerfile_cmd."""
        assert "_set_dockerfile_cmd" not in deploy_script
