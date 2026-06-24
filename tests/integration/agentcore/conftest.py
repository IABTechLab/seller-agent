"""Conftest for AgentCore runtime integration tests.

Registers custom pytest CLI options for AWS profile and runtime ARN.
"""


def pytest_addoption(parser):
    """Add AgentCore-specific CLI options."""
    parser.addoption("--profile", action="store", default=None, help="AWS CLI profile")
    parser.addoption("--runtime-arn", action="store", default=None, help="Runtime ARN override")
    parser.addoption(
        "--agent-name", action="store", default=None, help="Agent name in .bedrock_agentcore.yaml"
    )
